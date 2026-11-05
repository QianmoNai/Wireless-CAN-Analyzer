"""One serialized control owner per channel, isolated from Qt's render thread."""
import asyncio
from collections import deque
import json
import time
from .protocol import packet, u32, setting_command, can_command, secret, device_settings_command, device_settings_status
from .transports import BleTransport, LanTransport, scan_ble, discover
from .usb_transport import UsbTransport, scan_usb, usb_identity, resolve_usb


UART_ACK_TIMEOUT = 3.0
RX_BATCH_BYTES = 16 * 1024
RX_BATCH_SECONDS = .004


class Channel:
    def __init__(self, emit, saved=None, save=lambda x: None):
        self.emit, self.save = emit, save
        self.saved = dict(saved or {})
        self.mode = self.saved.get('mode', 'ble')
        self.transport = None
        self.ready = False
        self.epoch = 0
        self.name = self.saved.get('name', 'ESP32-CAN')
        self.address = self.saved.get('address', '')
        self.devices = {}
        self.commands = asyncio.Queue(32)
        self.packets = deque(maxlen=512)
        self.controls = deque(maxlen=64)
        self.dropped = 0
        self.polls = {}
        self.uart = False
        self.waiters = {}
        self.pending_tx = {}
        self.uart_task = None
        self.uart_job = None
        self.uart_progress = None
        self.uart_cancelled = deque(maxlen=32)
        self.token = 0x20000000
        self.revision = 1000
        self.detail = ''
        self.roster = []
        self.signature = None
        self.applied_mode = 0
        self.monitor_visible = True
        self.channel_visible = True
        self.subscription = None
        self.applied_roster = []
        self.capabilities = 0
        self.max_hz = 0
        self.ui_pending = False
        self.control_pending = False
        self.deliveries = {}
        self.delivery_token = 0
        self.tasks = []
        self.closed = False
        self.busy = False
        self.current = None
        self.wifi_diagnostics = None

    def enable_wifi_diagnostics(self, log, slot=1):
        from .wifi_diagnostics import WifiDiagnostics
        self.wifi_diagnostics = WifiDiagnostics(self, log, slot)

    def start(self):
        self.tasks = [asyncio.create_task(self.owner()), asyncio.create_task(self.flush()), asyncio.create_task(self.maintenance())]
        if self.wifi_diagnostics:
            self.tasks.append(asyncio.create_task(self.wifi_diagnostics.run()))

    def next_token(self):
        self.token = (self.token + 1) & 0x7fffffff
        return self.token

    def persist(self):
        self.saved.update(mode=self.mode, name=self.name, address=self.address)
        self.save(dict(self.saved))

    def state(self, value, message):
        self.emit('state', dict(value=value, message=message, name=self.name, transport=self.mode))

    def network_state(self):
        self.emit('networkMode', dict(mode=self.mode, busy=self.busy, paired=bool(self.saved.get('token')), ip=self.saved.get('ip', '')))

    def submit(self, name, args):
        if self.closed:
            return
        if name == 'uartCancel':
            if self.uart_job and args == [self.uart_job['id']]:
                self.uart_job['stop'] = '已停止后续发送，已提交的字节可能仍在输出'
            elif len(args) == 1 and type(args[0]) is int:
                self.uart_cancelled.append(args[0])
            return
        if name == 'delivered':
            if not args:  # Explicit page-load reset only; callbacks carry tokens.
                self.deliveries.clear()
                self.ui_pending = self.control_pending = False
            elif len(args) == 1:
                lane = self.deliveries.pop(args[0], None)
                if lane == 'control':self.control_pending = False
                elif lane == 'data':self.ui_pending = False
            return
        if name == 'networkState':
            self.network_state()
            return
        if name == 'dashboard':
            rows = json.loads(args[0])
            if not isinstance(rows, list) or len(rows) > 32:
                raise ValueError('电机列表无效')
            signature = [(m['key'], m.get('sampleHz', 100)) for m in rows]
            self.roster = rows
            keys = {m['key'] for m in rows}
            for key, (identifier, hz, _) in list(self.polls.items()):
                if key not in keys:
                    self.polls.pop(key, None)
                    self.enqueue('setPolling', [key, identifier, False, hz])
            if signature != self.signature:
                self.signature = signature
                self.enqueue('subscribe', [])
            return
        if name == 'disconnect':
            # Interrupt a scan/connect/ACK wait; no old queued writes survive.
            if self.current:
                self.current.cancel()
            while not self.commands.empty():
                self.commands.get_nowait()
            self.enqueue(name, args)
            return
        self.enqueue(name, args)

    def enqueue(self, name, args):
        try:
            self.commands.put_nowait((name, args, self.epoch))
        except asyncio.QueueFull:
            if name == 'uartSend':
                self.uart_progress = dict(id=args[0] if args else 0, confirmed=0, total=0, done=True, message='命令队列已满，未发送')
            elif name == 'setPolling' and len(args)==4:
                self.emit('queryState',dict(key=args[0],enabled=args[0] in self.polls,
                                           message='命令队列已满，查询配置未提交，保持已确认状态'))
            self.emit('error', dict(message='命令队列已满，请等待当前操作完成'))

    async def owner(self):
        while True:
            name, args, epoch = await self.commands.get()
            if epoch != self.epoch and name not in ('disconnect', 'connectionMode'):
                continue
            self.current = asyncio.create_task(self.handle(name, args))
            try:
                await self.current
            except asyncio.CancelledError:
                if self.closed:
                    return
            except Exception as e:
                if name == 'uartSend':
                    self.uart_progress = dict(id=args[0] if args else 0, confirmed=0, total=0, done=True, message=str(e))
                self.emit('error', dict(message=str(e) or type(e).__name__))
                if name in ('connect', 'connectLan', 'scan'):
                    await self.disconnect('操作失败，请重新连接')
            finally:
                self.current = None

    async def write(self, p):
        if not self.ready or not self.transport:
            raise ConnectionError('请先连接设备')
        await self.transport.write(p)

    async def acknowledge(self, p, kind, key, timeout=2):
        future = asyncio.get_running_loop().create_future()
        self.waiters[(kind, key)] = future
        try:
            await self.write(p)
            return await asyncio.wait_for(future, timeout)
        finally:
            self.waiters.pop((kind, key), None)

    async def handle(self, name, args):
        if name == 'wifiDiagnostics' and self.wifi_diagnostics:
            await self.wifi_diagnostics.poll()
        elif name == 'connectionMode':
            if args[0] not in ('ble', 'wifi', 'usb'):
                raise ValueError('无效连接方式')
            await self.disconnect('已切换连接方式')
            self.mode = args[0]
            self.persist()
            self.network_state()
        elif name == 'disconnect':
            await self.disconnect()
        elif name == 'scan':
            if len(args) > 1 or (args and type(args[0]) is not bool):
                raise ValueError('串口搜索选项无效')
            if self.ready:
                return
            self.state('scanning', '正在搜索设备…')
            self.emit('scanStart', {})
            if self.mode == 'usb':
                await scan_usb(lambda d: self.emit('device', d), include_all=bool(args and args[0]))
            elif self.mode == 'wifi':
                if not self.saved.get('id'):
                    raise ValueError('请先通过蓝牙读取网络状态，取得安全连接信息')
                await discover(self.saved['id'], lambda d: self.emit('device', d))
            else:
                self.devices = await scan_ble(lambda d: self.emit('device', d))
            self.state('idle', '搜索完成；可选择设备连接')
        elif name in ('connect', 'connectLan', 'reconnect'):
            expected_usb_identity = None
            if name == 'reconnect':
                target = 'wifi:'+self.saved.get('ip', '') if self.mode == 'wifi' else 'usb:'+self.saved.get('usb_port', '') if self.mode == 'usb' else self.address
                if self.mode == 'usb' and self.saved.get('usb_identity') is not None:
                    expected_usb_identity = self.saved['usb_identity']
                    target = 'usb:'+await resolve_usb(expected_usb_identity)
            else:
                target = ('wifi:' if name == 'connectLan' else '') + args[0]
            if not target or target in ('wifi:', 'usb:'):
                raise ValueError('尚未保存连接地址，请先扫描')
            await self.disconnect('准备连接')
            generation = self.epoch
            def received(p):
                if generation == self.epoch:
                    self.receive(p)
            def lost(message):
                if generation == self.epoch:
                    self.submit('disconnect', [])
                    self.emit('error', dict(message=message))
            self.state('connecting', '正在连接设备…')
            try:
                if target.startswith('usb:'):
                    self.mode = 'usb'
                    self.transport = UsbTransport(received, lost)
                    await self.transport.connect(target[4:])
                    identity = await usb_identity(target[4:])
                    if expected_usb_identity is not None and identity != expected_usb_identity:
                        raise ConnectionError('USB 设备在连接过程中发生变化，请重新扫描')
                    self.saved['usb_port'] = target[4:]
                    if identity is not None:
                        self.saved['usb_identity'] = identity
                    else:
                        self.saved.pop('usb_identity', None)
                elif target.startswith('wifi:'):
                    self.mode = 'wifi'
                    # Association/DHCP and the embedded TLS accept loop may
                    # still be settling after a mode change. Retry connection
                    # setup only; a connected session never replays CAN writes.
                    for attempt in range(3):
                        self.transport = LanTransport(received, lost)
                        try:
                            await self.transport.connect(target[5:], self.saved.get('fp'), self.saved.get('token'), identity=self.saved.get('id'))
                            break
                        except (TimeoutError, ConnectionRefusedError, ConnectionResetError):
                            await self.transport.close()
                            if attempt == 2:
                                raise
                            self.state('connecting', f'等待 Wi-Fi 就绪，正在重试连接（{attempt+2}/3）…')
                            await asyncio.sleep(1)
                    self.saved['ip'] = target[5:]
                else:
                    self.mode = 'ble'
                    self.transport = BleTransport(received, lost)
                    await self.transport.connect(self.devices.get(target, target))
                    if self.address and self.address != target:
                        for key in ('id', 'fp', 'token', 'ip'):
                            self.saved.pop(key, None)
                    self.address = target
                self.ready = True
                self.persist()
                self.state('ready', '已连接')
                self.network_state()
            except BaseException:
                await self.disconnect('连接未完成')
                raise
        elif name == 'deviceSettings':
            if len(args) != 1 or not isinstance(args[0], dict) or set(args[0]) - {'network', 'data', 'baud'}:
                raise ValueError('设备设置参数无效')
            command = device_settings_command(**args[0], token=self.next_token())
            result = device_settings_status(await self.acknowledge(command, 22, self.token))
            self.emit('deviceSettings', result)
            if result['status'] not in (0, 1):
                raise ValueError('设备未保存设置，状态码 '+str(result['status']))
            return result
        elif name == 'uartSend':
            if (len(args) != 2 or type(args[0]) is not int or not 0 < args[0] <= 0x7fffffff
                    or not isinstance(args[1], str) or not 2 <= len(args[1]) <= 8192):
                raise ValueError('UART 发送参数无效')
            try:
                data = bytes.fromhex(args[1])
            except ValueError:
                raise ValueError('UART 十六进制数据无效') from None
            if not 1 <= len(data) <= 4096:
                raise ValueError('发送长度须为 1–4096 字节')
            if not self.ready or not self.uart:
                raise ValueError('请先连接 UART 设备')
            if self.uart_task and not self.uart_task.done():
                raise ValueError('上一笔 UART 发送尚未结束')
            job = dict(id=args[0], data=data, confirmed=0, stop='')
            if args[0] in self.uart_cancelled:
                self.uart_cancelled.remove(args[0])
                self.uart_update(job, done=True, message='已停止，未发送')
                return
            self.uart_job = job
            self.uart_task = asyncio.create_task(self.send_uart(job))
        elif name == 'send':
            self.polls.clear()
            command = setting_command(args[0])
            if command[1] == 0x18 and not self.uart:
                raise ValueError('请先在设备屏幕选择 UART 数据')
            if command[1] == 0x12 and self.uart:
                raise ValueError('UART 模式不支持设置 CAN 波特率')
            if command[1] == 0x18 and self.uart_job:
                raise ValueError('UART 批量发送进行中')
            await self.write(command)
            self.emit('writeDone', dict(success=True))
        elif name == 'manual':
            if self.uart:raise ValueError('当前为 UART 数据，请在设备屏幕切回 CAN')
            if len(self.pending_tx) >= 16:
                raise ValueError('待确认发送过多，请等待结果')
            token = self.next_token()
            p = can_command(**args[0], token=token)
            self.pending_tx[token] = time.monotonic()
            self.emit('manualResult', dict(token=token, status='等待 CAN 发送结果'))
            try:
                await self.write(p)
            except BaseException:
                self.pending_tx.pop(token, None)
                self.emit('manualResult', dict(token=token, status='提交失败；结果未知'))
                raise
        elif name == 'setPolling':
            key, identifier, enabled, hz = args
            try:
                if self.uart:raise ValueError('UART 模式不支持 CAN 查询')
                if not self.capabilities & 8:
                    raise ValueError('当前固件不支持定时查询')
                if type(identifier) is not int or not 1 <= identifier <= 0x7fe or type(hz) is not int or not 1 <= hz <= 1000:
                    raise ValueError('查询 ID 或频率无效')
                if enabled and (key not in [m['key'] for m in self.roster] or json.loads(key)['id'] != identifier):
                    raise ValueError('查询对象不在当前电机列表')
                if enabled and any(k != key and p[0] == identifier for k, p in self.polls.items()):
                    raise ValueError('此电机 ID 已开启查询')
                self.polls.pop(key, None)
                result = await self.acknowledge(packet(0x14, x=identifier, y=hz if enabled else 0, token=self.next_token()), 8, self.token)
                if result[2] != (0 if enabled else 1):
                    raise ValueError('设备未接受查询配置，状态码 '+str(result[2]))
                if enabled:
                    self.polls[key] = (identifier, hz, time.monotonic())
                self.emit('queryState', dict(key=key, enabled=enabled, message=f'{hz} Hz · 已确认' if enabled else '查询已关闭'))
            except Exception:
                retained = key in self.polls
                self.emit('queryState', dict(key=key, enabled=retained, message='配置未应用，保持原查询' if retained else '未确认，停止续租；设备最多 3 秒后到期'))
                raise
        elif name == 'renew':
            if not self.ready:
                return
            for key, (identifier, hz, previous) in list(self.polls.items()):
                if time.monotonic()-previous < 1:
                    continue
                try:
                    result = await self.acknowledge(packet(0x14, x=identifier, y=hz, token=self.next_token()), 8, self.token)
                    if result[2] != 0:
                        raise ValueError('设备停止查询')
                    self.polls[key] = (identifier, hz, time.monotonic())
                except Exception:
                    self.polls.pop(key, None)
                    self.emit('queryState', dict(key=key, enabled=False, message='查询确认超时，已停止续租'))
        elif name in ('setMonitorVisible', 'setChannelVisible'):
            if len(args) != 1 or type(args[0]) is not bool:
                raise ValueError('页面状态无效')
            field = 'monitor_visible' if name == 'setMonitorVisible' else 'channel_visible'
            if getattr(self, field) != args[0]:
                setattr(self, field, args[0])
                await self.subscribe()
        elif name == 'showDetail':
            if args[0] not in [m['key'] for m in self.roster]:
                raise ValueError('电机不在当前列表')
            self.detail = args[0]
            await self.subscribe()
        elif name == 'leaveDetail':
            self.detail = ''
            await self.subscribe()
        elif name == 'subscribe':
            await self.subscribe()
        elif name == 'wifiConfigure':
            if not self.ready or not isinstance(self.transport, BleTransport):
                raise ValueError('请先通过蓝牙连接设备')
            self.polls.clear()
            self.busy = True
            self.emit('wifiResult', dict(busy=True, message='请在系统蓝牙配对提示中输入 ESP32 屏幕上的六位码'))
            try:
                await asyncio.wait_for(self.transport.provision(*args, self.network_status, lambda rows: self.emit('wifiScan', dict(rows=rows))), 105)
                self.emit('wifiResult', dict(busy=False, message='操作完成；局域网使用前请在设备屏幕切换到 Wi-Fi 模式'))
            finally:
                self.busy = False
                self.network_state()

    def uart_update(self, job, done=False, message=''):
        # Overwrite progress until the next UI batch; no per-segment UI trip.
        self.uart_progress = dict(id=job['id'], confirmed=job['confirmed'],
                                  total=len(job['data']), done=done, message=message)

    async def send_uart(self, job):
        message = ''
        self.uart_update(job)
        try:
            for offset in range(0, len(job['data']), 12):
                # The serialized control owner gets the next write opportunity.
                while self.ready and not job['stop'] and (self.current or not self.commands.empty()):
                    await asyncio.sleep(.001)
                if job['stop'] or not self.ready or not self.uart:
                    message = job['stop'] or '连接或数据类型已改变，已停止发送'
                    break
                data = job['data'][offset:offset+12]
                token = self.next_token()
                p = bytearray(packet(0x18, a=len(data), token=token))
                p[4:4+len(data)] = data
                ack = await self.acknowledge(p, 20, token, UART_ACK_TIMEOUT)
                if ack[2] or ack[3] != len(data) or ack[4:4+len(data)] != data:
                    raise ValueError('设备未完成串口输出或确认内容不符，结果未知；未自动重发')
                job['confirmed'] += len(data)
                self.uart_update(job)
                await asyncio.sleep(0)
            message = message or job['stop']
        except asyncio.TimeoutError:
            message = '确认超时，当前分段结果未知；未自动重发'
        except asyncio.CancelledError:
            message = '连接断开；已提交分段结果未知，未自动重发'
        except Exception as exc:
            message = str(exc) or '通信失败，结果未知；未自动重发'
        finally:
            self.uart_update(job, done=True, message=message)
            if self.uart_job is job:
                self.uart_job = None

    async def subscribe(self):
        if not self.ready or self.uart:
            return
        supported = bool(self.capabilities & 128)
        self.emit('detailSupport', dict(supported=supported, maxHz=self.max_hz, channel=self.name))
        if not self.capabilities & 64:
            return
        detail_rows = [m for m in self.roster if m['key'] == self.detail] if self.detail else []
        if not self.channel_visible:
            mode, rows = (3 if supported else 1), []
        elif detail_rows and supported:
            mode, rows = 2, detail_rows
        elif not self.monitor_visible:
            mode, rows = 1, self.roster
        else:
            mode, rows = 0, []
        hz = min(self.max_hz, max(1, int(rows[0].get('sampleHz', 100)))) if mode == 2 else 0
        signature = (mode, hz, tuple((m['key'], m['family']) for m in rows))
        if signature == self.subscription:
            return
        # A failed transaction may still have changed the device. Never reuse
        # the preceding signature after advancing the subscription revision.
        self.subscription = None
        self.revision += 1
        self.packets.clear()
        for slot, m in enumerate(rows):
            c = json.loads(m['key'])
            await self.write(packet(0x16, a=slot, b={'dji':0, 'dm':1, 'imu':2}[m['family']], x=c['id'], y=c['feedbackId'], token=self.revision))
        try:
            result = await self.acknowledge(packet(0x17, a=mode, b=len(rows), y=hz, token=self.revision), 10, self.revision)
            if result[2] != mode or result[3] != len(rows):
                raise ValueError('设备订阅响应与请求不符')
            self.applied_mode = result[2]
            self.subscription = signature
            self.applied_roster = list(rows)
            self.emit('telemetryMode', dict(mode=result[2], quiet=result[2] == 1, hz=u32(result, 8)))
        except asyncio.TimeoutError:
            self.emit('error', dict(message='订阅未确认，请切换页面重试'))

    def network_status(self, value):
        if all(value.get(k) for k in ('id', 'fp', 'token')):
            secret(value['fp']); secret(value['token'])
            if len(value['id']) != 12:
                raise ValueError('设备标识无效')
            self.saved.update({k:value[k] for k in ('id', 'fp', 'token')})
            if value.get('ip'):
                self.saved['ip'] = value['ip']
            self.persist()
        self.emit('wifiStatus', {k:v for k,v in value.items() if k not in ('token', 'fp')})
        self.network_state()

    def receive(self, p):
        if len(p) != 20 or p[0] != 1:
            return
        kind = p[1]
        if self.wifi_diagnostics and self.mode == 'wifi':
            if self.wifi_diagnostics.receive(p):return
        if kind == 26:return # Diagnostic extension never enters the GUI/data queues.
        waiter = self.waiters.get((kind, u32(p, 16)))
        if waiter and not waiter.done():
            waiter.set_result(p)
        if kind == 21:
            previous_uart = self.uart
            self.uart = p[2] == 1
            if self.uart:
                for key in list(self.polls):self.emit('queryState',dict(key=key,enabled=False,message='UART 模式，查询已停止'))
                self.polls.clear()
                self.detail = ''
            elif previous_uart:
                if self.uart_job:
                    self.uart_job['stop'] = '数据类型已改变，已停止后续发送'
                self.enqueue('subscribe', [])
        if kind == 5:
            old = self.capabilities
            self.capabilities = p[3]
            self.max_hz = min(1000, u32(p, 12))
            if old != self.capabilities:
                self.enqueue('subscribe', [])
        if kind == 8 and p[2] not in (0, 1):
            for key, (identifier, _, _) in list(self.polls.items()):
                if identifier == u32(p, 4):
                    self.polls.pop(key, None)
                    self.emit('queryState', dict(key=key, enabled=False, message='设备停止查询，状态码 '+str(p[2])))
        if kind in (2, 3) and u32(p, 16) in self.pending_tx:
            self.pending_tx.pop(u32(p, 16))
            status = ('CAN 发送成功（已收到 ACK）' if kind == 2 else
                      'CAN 仲裁丢失；本次未发送成功，未自动重发' if p[2] & 0x80 and not p[2] & 0x7c else
                      'CAN 发送失败，错误标志 0x%02X'%p[2])
            self.emit('manualResult', dict(token=u32(p, 16), status=status))
        if kind == 18 and p[2]:
            self.emit('error', dict(message='设备拒绝通信命令，状态码 '+str(p[2])))
        if kind == 11 and (not self.detail or u32(p, 4) != self.revision):
            return
        if kind == 12:
            if self.detail and u32(p, 16) == self.revision:
                self.emit('motorRates', dict(key=self.detail, received=u32(p,4), sampled=u32(p,8), elapsed=u32(p,12), age=0, sentAt=int(time.time()*1000)))
            return
        if kind in (9, 16):
            count = len(self.applied_roster)
            if self.applied_mode != 1 or u32(p, 16) != self.revision or p[3] != count:
                return
            stamp = int(time.time()*1000)
            if kind == 9 and p[2] == 1:
                self.emit('onlineSummary', dict(count=count, online=u32(p, 4), seen=u32(p, 8), age=0, sentAt=stamp))
            elif kind == 16 and p[2] in (0, 10, 20, 30) and p[2] < count:
                size = min(10, count-p[2])
                mask = int.from_bytes(p[4:6], 'little')
                if mask >> size or any(p[6+size:16]):
                    return
                self.emit('motorTemperatures', dict(start=p[2], count=count,
                    temperatures=[p[6+i] if mask & (1 << i) else None for i in range(size)], age=0, sentAt=stamp))
            return
        if kind in (8, 9, 10, 15, 16, 18):
            return
        if kind in (1, 2, 3) and self.applied_mode != 0:
            return
        queue = self.packets if kind in (1, 2, 3, 11, 19) else self.controls
        if len(queue) == queue.maxlen:
            self.dropped += 1
        queue.append((bytes(p), time.monotonic()))

    def deliver_batch(self, lane, raw, samples=None, progress=None):
        self.delivery_token += 1
        self.deliveries[self.delivery_token] = lane
        if lane == 'control':self.control_pending = True
        else:self.ui_pending = True
        samples = samples or []
        self.emit('motorSamples' if samples else 'packets',
                  dict(items=samples or raw, packets=raw if samples else [],
                       uartTx=progress, key=self.detail, dropped=self.dropped,
                       delivery=self.delivery_token, sentAt=int(time.time()*1000)))

    def flush_once(self):
        if not self.ready:
            return
        now = time.monotonic()
        deadline = now + RX_BATCH_SECONDS
        # One bounded control batch can pass a stalled data receipt. Device
        # waiters/TX ACKs are already resolved directly in receive().
        control_bytes = 0
        if not self.control_pending and (self.controls or self.uart_progress):
            raw = []
            while self.controls and time.monotonic() < deadline:
                p, arrived = self.controls.popleft()
                raw.append(dict(hex=p.hex(), age=max(0, int((now-arrived)*1000))))
            progress, self.uart_progress = self.uart_progress, None
            control_bytes = len(raw)*20
            self.deliver_batch('control', raw, progress=progress)
        if self.ui_pending or not self.packets:
            return
        raw, samples, run = [], [], None
        used = control_bytes
        while self.packets and used + 20 <= RX_BATCH_BYTES and time.monotonic() < deadline:
            p, arrived = self.packets.popleft()
            used += 20
            age = max(0, int((now-arrived)*1000))
            if p[1] == 19 and 1 <= p[2] <= 12 and not p[3]:
                seq = u32(p, 16)
                if run is None or seq != (run['last']+1) & 0xffffffff:
                    run = dict(uart=bytearray(), first=seq, last=seq, count=0, age=age)
                    raw.append(run)
                run['uart'].extend(p[4:4+p[2]])
                run['last'] = seq
                run['count'] += 1
            else:
                run = None
                item = dict(hex=p.hex(), age=age)
                (samples if p[1] == 11 else raw).append(item)
        for item in raw:
            if 'uart' in item:item['uart'] = item['uart'].hex()
        if raw or samples:
            self.deliver_batch('data', raw, samples)

    async def flush(self):
        while True:
            await asyncio.sleep(.025)
            self.flush_once()

    async def maintenance(self):
        while True:
            await asyncio.sleep(.5)
            if self.ready and self.polls and not self.busy and self.commands.empty():
                self.enqueue('renew', [])
            for token, sent in list(self.pending_tx.items()):
                if time.monotonic()-sent >= 3:
                    self.pending_tx.pop(token, None)
                    self.emit('manualResult', dict(token=token, status='等待超时；发送结果未知，不自动重发'))

    async def disconnect(self, message='已断开连接'):
        if self.wifi_diagnostics and self.ready and self.mode == 'wifi':
            self.wifi_diagnostics.disconnected()
        if self.uart_task and not self.uart_task.done():
            self.uart_task.cancel()
            await asyncio.gather(self.uart_task, return_exceptions=True)
        self.uart_job = None
        self.uart_progress = None
        self.uart_cancelled.clear()
        self.uart = False
        self.epoch += 1
        self.ready = False
        self.polls.clear()
        self.capabilities = 0
        self.subscription = None
        self.applied_roster = []
        self.applied_mode = 0
        self.ui_pending = self.control_pending = False
        self.deliveries.clear()
        self.packets.clear(); self.controls.clear()
        for token in self.pending_tx:
            self.emit('manualResult', dict(token=token, status='连接断开；发送结果未知'))
        self.pending_tx.clear()
        for waiter in self.waiters.values():
            if not waiter.done():
                waiter.cancel()
        self.waiters.clear()
        transport, self.transport = self.transport, None
        if transport:
            await transport.close()
        self.state('idle', message)

    async def close(self):
        self.closed = True
        for task in self.tasks:
            task.cancel()
        if self.current:
            self.current.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.disconnect()
