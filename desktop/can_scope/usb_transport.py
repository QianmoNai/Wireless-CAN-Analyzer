"""Native USB Serial/JTAG, separate from the device's external UART pins."""
import asyncio
import secrets
import os
import time
from .usb_protocol import Decoder, encode, HELLO, PING, BYE

HANDSHAKE_TIMEOUT = 4.0
HANDSHAKE_INTERVAL = 0.5


async def scan_usb(found):
    from serial.tools import list_ports
    ports = await asyncio.to_thread(list_ports.comports)
    for port in sorted(ports, key=lambda p: p.device):
        found(dict(address='usb:'+port.device, name=port.description or 'USB 串口',
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
        import serial
        self.ready = asyncio.get_running_loop().create_future()
        try:
            # Do not deliberately toggle reset lines. Some host/device states
            # still restart the device on open, so HELLO tolerates startup.
            options = {"exclusive": True} if os.name == "posix" else {}
            self.port = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=.5, **options)
            self.port.dtr = False
            self.port.rts = False
            self.port.port = device
            self.port.open()
            self.port.reset_input_buffer()
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
                pending = asyncio.create_task(asyncio.to_thread(self.port.write, frame))
                try:
                    count = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    # Keep the write lock until the bounded OS write actually ends.
                    await asyncio.gather(pending, return_exceptions=True)
                    raise
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
