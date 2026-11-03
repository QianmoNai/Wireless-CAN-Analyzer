"""One serialized control owner per channel, isolated from Qt's render thread."""
import asyncio
from collections import deque
import json
import time
from .protocol import packet, u32, setting_command, can_command, secret, device_settings_command, device_settings_status
from .transports import BleTransport, LanTransport, scan_ble, discover
from .usb_transport import UsbTransport, scan_usb


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
        self.token = 0x20000000
        self.revision = 1000
        self.detail = ''
        self.roster = []
        self.signature = None
        self.applied_mode = 0
        self.capabilities = 0
        self.max_hz = 0
        self.ui_pending = False
        self.tasks = []
        self.closed = False
        self.busy = False
        self.current = None

    def start(self):
        self.tasks = [asyncio.create_task(self.owner()), asyncio.create_task(self.flush()), asyncio.create_task(self.maintenance())]

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
        if name == 'delivered':
            self.ui_pending = False
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
                self.emit('error', dict(message=str(e) or type(e).__name__))
                if name in ('connect', 'connectLan', 'scan'):
                    await self.disconnect('操作失败，请重新连接')
            finally:
                self.current = None

    async def write(self, p):
        if not self.ready or not self.transport:
            raise ConnectionError('请先连接设备')
        await self.transport.write(p)

    async def acknowledge(self, p, kind, key):
        future = asyncio.get_running_loop().create_future()
        self.waiters[(kind, key)] = future
        try:
            await self.write(p)
            return await asyncio.wait_for(future, 2)
        finally:
            self.waiters.pop((kind, key), None)

    async def handle(self, name, args):
        if name == 'connectionMode':
            if args[0] not in ('ble', 'wifi', 'usb'):
                raise ValueError('无效连接方式')
            await self.disconnect('已切换连接方式')
            self.mode = args[0]
            self.persist()
            self.network_state()
        elif name == 'disconnect':
            await self.disconnect()
        elif name == 'scan':
            if self.ready:
                return
            self.state('scanning', '正在搜索设备…')
            self.emit('scanStart', {})
            if self.mode == 'usb':
                await scan_usb(lambda d: self.emit('device', d))
            elif self.mode == 'wifi':
                if not self.saved.get('id'):
                    raise ValueError('请先通过蓝牙读取网络状态，取得安全连接信息')
                await discover(self.saved['id'], lambda d: self.emit('device', d))
            else:
                self.devices = await scan_ble(lambda d: self.emit('device', d))
            self.state('idle', '搜索完成；可选择设备连接')
        elif name in ('connect', 'connectLan', 'reconnect'):
            if name == 'reconnect':
                target = 'wifi:'+self.saved.get('ip', '') if self.mode == 'wifi' else 'usb:'+self.saved.get('usb_port', '') if self.mode == 'usb' else self.address
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
                    self.saved['usb_port'] = target[4:]
                elif target.startswith('wifi:'):
                    self.mode = 'wifi'
                    self.transport = LanTransport(received, lost)
                    await self.transport.connect(target[5:], self.saved.get('fp'), self.saved.get('token'))
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
                self.state('ready', '已连接 · 等待设备状态')
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
        elif name == 'send':
            self.polls.clear()
            command = setting_command(args[0])
            if command[1] == 0x18 and not self.uart:
                raise ValueError('请先在设备屏幕选择 UART 数据')
            if command[1] == 0x12 and self.uart:
                raise ValueError('UART 模式不支持设置 CAN 波特率')
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
            if self.uart:raise ValueError('UART 模式不支持 CAN 查询')
            key, identifier, enabled, hz = args
            if not self.capabilities & 8:
                raise ValueError('当前固件不支持定时查询')
            if type(identifier) is not int or not 1 <= identifier <= 0x7fe or type(hz) is not int or not 1 <= hz <= 1000:
                raise ValueError('查询 ID 或频率无效')
            if enabled and (key not in [m['key'] for m in self.roster] or json.loads(key)['id'] != identifier):
                raise ValueError('查询对象不在当前电机列表')
            if enabled and any(k != key and p[0] == identifier for k, p in self.polls.items()):
                raise ValueError('此电机 ID 已开启查询')
            self.polls.pop(key, None)
            try:
                result = await self.acknowledge(packet(0x14, x=identifier, y=hz if enabled else 0, token=self.next_token()), 8, self.token)
                if result[2] != (0 if enabled else 1):
                    raise ValueError('设备未接受查询配置，状态码 '+str(result[2]))
                if enabled:
                    self.polls[key] = (identifier, hz, time.monotonic())
                self.emit('queryState', dict(key=key, enabled=enabled, message=f'{hz} Hz · 已确认' if enabled else '查询已关闭'))
            except Exception:
                self.emit('queryState', dict(key=key, enabled=False, message='未确认，停止续租；设备最多 3 秒后到期'))
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

    async def subscribe(self):
        if not self.ready or self.uart:
            return
        supported = bool(self.capabilities & 128)
        self.emit('detailSupport', dict(supported=supported, maxHz=self.max_hz, channel=self.name))
        if not self.capabilities & 64:
            return
        self.revision += 1
        rows = [m for m in self.roster if m['key'] == self.detail] if self.detail else []
        mode = 2 if rows and supported else 0
        self.packets.clear()
        if mode == 2:
            m = rows[0]
            c = json.loads(m['key'])
            await self.write(packet(0x16, b={'dji':0, 'dm':1, 'imu':2}[m['family']], x=c['id'], y=c['feedbackId'], token=self.revision))
        hz = min(self.max_hz, max(1, int(rows[0].get('sampleHz', 100)))) if mode == 2 else 0
        try:
            result = await self.acknowledge(packet(0x17, a=mode, b=int(mode == 2), y=hz, token=self.revision), 10, self.revision)
            self.applied_mode = result[2]
            self.emit('telemetryMode', dict(mode=result[2], hz=u32(result, 8)))
        except asyncio.TimeoutError:
            self.emit('error', dict(message='订阅未确认，请重新进入电机详情'))

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
            self.emit('manualResult', dict(token=u32(p, 16), status='CAN 发送成功（已收到 ACK）' if kind == 2 else 'CAN 发送失败，错误标志 0x%02X'%p[2]))
        if kind == 18 and p[2]:
            self.emit('error', dict(message='设备拒绝通信命令，状态码 '+str(p[2])))
        if kind == 11 and (not self.detail or u32(p, 4) != self.revision):
            return
        if kind == 12:
            if self.detail and u32(p, 16) == self.revision:
                self.emit('motorRates', dict(key=self.detail, received=u32(p,4), sampled=u32(p,8), elapsed=u32(p,12), age=0))
            return
        if kind in (8, 9, 10, 15, 16, 18):
            return
        queue = self.packets if kind in (1, 2, 3, 11, 19) else self.controls
        if len(queue) == queue.maxlen:
            self.dropped += 1
        queue.append((bytes(p), time.monotonic()))

    async def flush(self):
        while True:
            await asyncio.sleep(.025)
            if self.ui_pending or not self.ready or not (self.packets or self.controls):
                continue
            now = time.monotonic()
            raw, samples = [], []
            for queue in (self.controls, self.packets):
                for _ in range(min(len(queue), 128)):
                    p, arrived = queue.popleft()
                    item = dict(hex=p.hex(), age=int((now-arrived)*1000))
                    (samples if p[1] == 11 else raw).append(item)
            self.ui_pending = True
            self.emit('motorSamples' if samples else 'packets', dict(items=samples or raw, packets=raw if samples else [], key=self.detail, dropped=self.dropped, sentAt=int(time.time()*1000)))

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
        self.uart = False
        self.epoch += 1
        self.ready = False
        self.polls.clear()
        self.capabilities = 0
        self.ui_pending = False
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
