"""Native USB Serial/JTAG, separate from the device's external UART pins."""
import asyncio
import secrets
import time
from pathlib import Path
from .usb_protocol import Decoder, encode, HELLO, PING, BYE
from .protocol import device_settings_command, device_settings_status

HANDSHAKE_TIMEOUT = 4.0
HANDSHAKE_INTERVAL = 0.5


def _port_identity(port):
    vid, pid = getattr(port, 'vid', None), getattr(port, 'pid', None)
    serial = getattr(port, 'serial_number', None)
    if type(vid) is int and type(pid) is int and isinstance(serial, str) and serial:
        return dict(vid=vid, pid=pid, serial=serial)
    return None


async def usb_identity(device):
    from serial.tools import list_ports
    ports = await asyncio.to_thread(list_ports.comports)
    matches = [p for p in ports if Path(p.device).resolve() == Path(device).resolve()]
    return _port_identity(matches[0]) if len(matches) == 1 else None


async def resolve_usb(identity):
    """Resolve only one exact hardware identity; never guess from a reused port."""
    if (not isinstance(identity, dict) or set(identity) != {'vid', 'pid', 'serial'} or
            type(identity['vid']) is not int or type(identity['pid']) is not int or
            not isinstance(identity['serial'], str) or not identity['serial']):
        raise ValueError('保存的 USB 设备身份无效，请重新扫描选择设备')
    from serial.tools import list_ports
    ports = await asyncio.to_thread(list_ports.comports)
    matches = [p.device for p in ports if _port_identity(p) == identity]
    if len(matches) != 1:
        raise ValueError('未找到唯一匹配的 USB 设备，请检查连接或重新扫描')
    return matches[0]


def open_port(device):
    from .serial_port import open_native_port
    return open_native_port(device)


async def _write_serial_once(port, frame):
    pending = asyncio.create_task(asyncio.to_thread(port.write, frame))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        # Cancelling to_thread does not stop the bounded OS write. Even a
        # second cancel must not release its port/lock while it is running.
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not pending.cancelled():
            pending.exception()
        raise


async def prepare_usb(device, data=None, baud=None, timeout=8.0):
    """Explicitly persist USB mode without HELLO; release the port when applied.

    Requires settings-capable firmware. It never establishes a data session or
    sends UART/CAN data. A settings mutation is submitted once; only reads retry.
    """
    mutation = device_settings_command(network=4, data=data, baud=baud, token=2)
    session = secrets.randbelow(0xffffffff) + 1
    decoder = Decoder()
    port = open_port(device)
    deadline = time.monotonic() + timeout
    async def request(p, retry=False):
        due = 0
        while time.monotonic() < deadline:
            if time.monotonic() >= due:
                frame = encode(session, p)
                count = await _write_serial_once(port, frame)
                if count != len(frame):
                    raise ConnectionError('USB 设置写入不完整，结果未知')
                due = time.monotonic() + (.25 if retry else timeout)
            for nonce, response in decoder.feed(port.read(min(4096, port.in_waiting))):
                if nonce != session:
                    continue
                if response[:2] == bytes((1, 18)) and response[3] == 0x19 and int.from_bytes(response[4:8], 'little') == int.from_bytes(p[16:20], 'little') and response[2]:
                    # A newly opened native USB port may catch device startup.
                    # 0x11 means settings are not initialized yet. Retry only
                    # the read-only query; a mutation is still submitted once.
                    if retry and response[2] == 0x11:
                        continue
                    raise ValueError('设备拒绝 USB 设置命令，状态码 '+str(response[2]))
                if response[:2] == bytes((1, 22)):
                    status = device_settings_status(response)
                    if status['token'] == int.from_bytes(p[16:20], 'little'):
                        return status
            await asyncio.sleep(.005)
        raise ConnectionError('USB 远程设置超时，请确认固件支持设置接口；设置结果可能未知')
    try:
        query = device_settings_command(token=1)
        current = await request(query, retry=True)
        if current['status'] != 0:
            raise ConnectionError('设备设置读取失败，状态码 '+str(current['status']))
        if current['network'] != 4 or (data is not None and current['data'] != data) or (baud is not None and current['baud'] != baud):
            current = await request(mutation)
            if current['status'] != 1:
                raise ConnectionError('设备未保存 USB 设置，状态码 '+str(current['status']))
        if current['network'] != 4 or (data is not None and current['data'] != data) or (baud is not None and current['baud'] != baud):
            raise ConnectionError('设备应用的 USB 设置与请求不一致')
        return current
    finally:
        port.close()


async def scan_usb(found, include_all=False):
    from serial.tools import list_ports
    ports = await asyncio.to_thread(list_ports.comports)
    def usb(port):
        return getattr(port, 'vid', None) is not None and getattr(port, 'pid', None) is not None
    def native(port):
        return (getattr(port, 'vid', None), getattr(port, 'pid', None)) == (0x303a, 0x1001)
    for port in sorted(ports, key=lambda p: (not native(p), not usb(p), p.device)):
        if not include_all and not usb(port):
            continue
        name = 'ESP32 原生 USB（待验证）' if native(port) else port.description or '串口'
        found(dict(address='usb:'+port.device, name=name,
                   rssi=0, transport='usb'))


class UsbTransport:
    def __init__(self, received, disconnected):
        self.received, self.disconnected = received, disconnected
        self.port = None
        self.tasks = []
        self.closed = False
        self.session = secrets.randbelow(0xffffffff) + 1
        self.decoder = Decoder()
        self.lock = asyncio.Lock()
        self.ready = None
        self.last_ping = 0
        self.failed = False

    async def connect(self, device):
        self.ready = asyncio.get_running_loop().create_future()
        try:
            # Do not deliberately toggle reset lines. Some host/device states
            # still restart the device on open, so HELLO tolerates startup.
            self.port = open_port(device)
            self.tasks = [asyncio.create_task(self.read())]
            deadline = time.monotonic() + HANDSHAKE_TIMEOUT
            while True:
                # HELLO is idempotent for this session. Never retry data commands.
                await self.write(HELLO)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConnectionError('USB 握手超时，请确认固件支持 USB直连、屏幕已选择该模式，并连接原生 USB 口')
                try:
                    await asyncio.wait_for(asyncio.shield(self.ready), min(HANDSHAKE_INTERVAL, remaining))
                    break
                except asyncio.TimeoutError as exc:
                    if time.monotonic() >= deadline:
                        raise ConnectionError('USB 握手超时，请确认固件支持 USB直连、屏幕已选择该模式，并连接原生 USB 口') from exc
            self.last_ping = time.monotonic()
            self.tasks.append(asyncio.create_task(self.heartbeat()))
        except BaseException:
            await self.close()
            raise

    async def read(self):
        try:
            while not self.closed:
                data = self.port.read(min(4096, self.port.in_waiting))
                for session, p in self.decoder.feed(data):
                    if session != self.session or p[0] != 1:
                        continue
                    if p == HELLO[:2]+b'\x01'+HELLO[3:]:
                        if not self.ready.done():
                            self.ready.set_result(True)
                    elif p == PING:
                        self.last_ping = time.monotonic()
                    elif self.ready.done() and not self.ready.cancelled():
                        self.received(p)
                await asyncio.sleep(.005)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.lost('USB 连接中断：'+str(e))

    def lost(self, message):
        if not self.closed and not self.failed:
            self.failed = True
            if self.ready and not self.ready.done():
                self.ready.set_exception(ConnectionError(message))
            else:
                self.disconnected(message)

    async def heartbeat(self):
        try:
            while not self.closed:
                await self.write(PING)
                await asyncio.sleep(1)
                if time.monotonic()-self.last_ping > 4:
                    raise ConnectionError('设备未响应，请确认屏幕处于 USB直连 模式')
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.lost(str(e))

    async def write(self, payload):
        async with self.lock:
            if self.closed or self.failed or not self.port or not self.port.is_open:
                raise ConnectionError('USB 未连接')
            frame = encode(self.session, payload)
            try:
                # Bound blocking serial writes away from the control event loop.
                count = await _write_serial_once(self.port, frame)
                if count != len(frame):
                    raise ConnectionError('USB 写入不完整，结果未知；请重新连接')
            except Exception:
                self.lost('USB 写入失败，结果未知；请重新连接')
                raise

    async def close(self):
        if self.closed:
            return
        # Best effort immediate lease cancellation; heartbeat timeout is fallback.
        if self.ready and self.ready.done() and not self.ready.cancelled() and self.ready.exception() is None and self.port and self.port.is_open:
            try:
                await self.write(BYE)
            except Exception:
                pass
        self.closed = True
        if self.ready and not self.ready.done():
            self.ready.cancel()
        for task in self.tasks:
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(*(t for t in self.tasks if t is not asyncio.current_task()), return_exceptions=True)
        if self.port:
            self.port.close()
