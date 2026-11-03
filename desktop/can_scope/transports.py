"""Native BLE (BlueZ/WinRT), pinned TLS and UDP discovery."""
import asyncio
import hashlib
import hmac
import json
import socket
import ssl
import sys
import time
from .protocol import SERVICE, WRITE, NOTIFY, NETWORK, PORT, ipv4, secret, wifi_commands


class LanTransport:
    def __init__(self, received, disconnected):
        self.received, self.disconnected = received, disconnected
        self.writer = None
        self.tasks = []
        self.closed = False

    async def connect(self, ip, fingerprint, token, port=PORT):
        pin, key = secret(fingerprint), secret(token)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        # A device certificate is trusted ONLY after the exact DER SHA-256 pin
        # obtained over authenticated BLE matches. Never send the token first.
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
        try:
            reader, self.writer = await asyncio.wait_for(asyncio.open_connection(ipv4(ip), port, ssl=context), 5)
            cert = self.writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
            if not hmac.compare_digest(hashlib.sha256(cert).digest(), pin):
                raise ValueError('设备证书不匹配，请通过蓝牙重新确认设备')
            self.writer.write(b'CAN1' + key)
            await self.writer.drain()
            if await asyncio.wait_for(reader.readexactly(4), 5) != b'OK01':
                raise ValueError('设备拒绝连接，请确认屏幕已切换到 Wi-Fi 模式')
            self.tasks = [asyncio.create_task(self.read(reader)), asyncio.create_task(self.heartbeat())]
        except BaseException:
            await self.close()
            raise

    async def read(self, reader):
        try:
            while not self.closed:
                p = await asyncio.wait_for(reader.readexactly(20), 12)
                if p[0] != 1:
                    raise ValueError('设备报文格式错误')
                if p[1] != 0x7f:
                    self.received(p)
        except asyncio.CancelledError:
            pass
        except Exception:
            if not self.closed:
                self.disconnected('局域网连接中断或超时')

    async def heartbeat(self):
        try:
            while not self.closed:
                await asyncio.sleep(3)
                await self.write(bytes([1, 0x7f]) + bytes(18))
        except asyncio.CancelledError:
            pass
        except Exception:
            if not self.closed:
                self.disconnected('局域网心跳失败')

    async def write(self, p):
        if self.closed or not self.writer or len(p) != 20:
            raise ConnectionError('设备未连接')
        self.writer.write(p)
        await asyncio.wait_for(self.writer.drain(), 5)

    async def close(self):
        self.closed = True
        for task in self.tasks:
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(*(t for t in self.tasks if t is not asyncio.current_task()), return_exceptions=True)
        if self.writer:
            self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), 2)
            except Exception:
                pass


class BleTransport:
    def __init__(self, received, disconnected):
        self.received, self.disconnected = received, disconnected
        self.client = None
        self.closed = False
        self.provision_authenticated = False

    async def connect(self, device):
        from bleak import BleakClient
        self.provision_authenticated = False
        self.client = BleakClient(device, disconnected_callback=lambda _: self.lost(), timeout=20)
        try:
            await self.client.connect()
            if self.client.services.get_service(SERVICE) is None:
                raise ValueError('设备没有 CAN Scope 服务')
            await self.client.start_notify(NOTIFY, lambda _, data: self.received(bytes(data)))
        except BaseException:
            await self.close()
            raise

    def lost(self):
        self.provision_authenticated = False
        if not self.closed:
            self.disconnected('蓝牙连接已断开')

    async def write(self, p):
        if self.closed or not self.client or not self.client.is_connected:
            raise ConnectionError('蓝牙未连接')
        await asyncio.wait_for(self.client.write_gatt_char(WRITE, p, response=True), 5)

    async def provision(self, operation, ssid, password, status, networks, *, pair=True):
        """Use pair=False only to reuse authentication on this same connection."""
        commands = []
        p = None
        try:
            # Pairing is delegated to the OS agent; PIN is shown on the ESP32.
            if pair:
                if await asyncio.wait_for(self.client.pair(), 65) is False:
                    raise ConnectionError('蓝牙认证失败')
                self.provision_authenticated = True
            elif not self.provision_authenticated or not self.client.is_connected:
                raise ConnectionError('请先在当前蓝牙连接上认证')
            commands = [bytearray(item) for item in wifi_commands(
                operation, ssid, password, int(time.monotonic_ns() % 255) + 1)]
            ssid = password = ''
            for p in commands:
                await self.client.write_gatt_char(NETWORK, p, response=True)
                p[:] = bytes(len(p))
            for _ in range(50):
                value = json.loads(bytes(await self.client.read_gatt_char(NETWORK)).decode('utf8'))
                status(value)
                if not value.get('busy'):
                    break
                await asyncio.sleep(.7)
                await self.client.write_gatt_char(NETWORK, bytes([1]) + bytes(19), response=True)
            else:
                raise TimeoutError('Wi-Fi 配置超时')
            if value.get('error') and operation != 1:
                raise ValueError('设备配网失败，错误码：' + str(value['error']))
            if operation == 2:
                rows = []
                for _ in range(16):
                    await self.client.write_gatt_char(NETWORK, bytes([7, 0, len(rows)]) + bytes(17), response=True)
                    page = json.loads(bytes(await self.client.read_gatt_char(NETWORK)).decode('utf8'))
                    batch = page.get('scan', [])
                    rows.extend(batch)
                    if not batch or len(rows) >= page.get('total', 0):
                        break
                networks(rows)
        finally:
            ssid = password = ''
            for p in commands:
                p[:] = bytes(len(p))
            commands.clear()
            p = None

    async def close(self):
        self.closed = True
        self.provision_authenticated = False
        if self.client and self.client.is_connected:
            await self.client.disconnect()


async def scan_ble(found):
    from bleak import BleakScanner
    devices = {}
    def advertisement(device, adv):
        if SERVICE in [x.lower() for x in adv.service_uuids]:
            devices[device.address] = device
            found(dict(address=device.address, name=adv.local_name or device.name or 'ESP32-CAN', rssi=adv.rssi))
    # BlueZ 5.87 can crash in its UUID discovery filter (bluez/bluez#2282).
    # Linux filters advertisements above; keep native filtering elsewhere.
    options = {} if sys.platform == 'linux' else {'service_uuids': [SERVICE]}
    async with BleakScanner(detection_callback=advertisement, **options):
        await asyncio.sleep(5)
    return devices


async def discover(identity, found, *, broadcast='255.255.255.255', timeout=3):
    loop = asyncio.get_running_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(('', 0))
        await loop.sock_sendto(sock, b'CAN_SCOPE_DISCOVER_1', (ipv4(broadcast), PORT))
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                data, address = await asyncio.wait_for(loop.sock_recvfrom(sock, 128), deadline-loop.time())
                if data.decode('ascii', errors='ignore').split() == ['CAN_SCOPE_1', identity, str(PORT)]:
                    found(dict(address='wifi:'+address[0], name='ESP32-CAN', rssi=0, transport='wifi'))
            except asyncio.TimeoutError:
                break
