"""Native BLE (BlueZ/WinRT), pinned TLS and UDP discovery."""
import asyncio
import hashlib
import hmac
import json
import re
import socket
import ssl
import sys
import time
from .protocol import SERVICE, WRITE, NOTIFY, BATCH_NOTIFY, NETWORK, PORT, ipv4, secret, wifi_commands


_MONOTONIC_MS_MAX = (2**63-1)//1000
_DIAGNOSTIC_INTS = (
    ('attempt_id', 0, 2**32-1), ('attempt_ms', 0, _MONOTONIC_MS_MAX),
    ('sta_start_ms', -1, _MONOTONIC_MS_MAX+2**32-1),
    ('connected_ms', -1, _MONOTONIC_MS_MAX+2**32-1),
    ('got_ip_ms', -1, _MONOTONIC_MS_MAX+2**32-1),
    ('disconnect_count', 0, 65535), ('rssi', -127, 127), ('channel', 1, 255),
    ('tx_power_qdbm', -127, 127), ('ble_interval', 1, 65535),
)


def network_status_fields(value):
    """Only public, bounded diagnostic fields; never retain the original JSON."""
    result = {key: value[key] for key in ('state', 'error', 'mode')
              if type(value.get(key)) is int and 0 <= value[key] <= 255}
    if type(value.get('busy')) is bool:
        result['busy'] = value['busy']
    for key, low, high in (('reason', -1, 65535), ('stage', 0, 5),
                           ('api_err', -(2**31), 2**31-1), ('got_ip', 0, 1)):
        if type(value.get(key)) is int and low <= value[key] <= high:
            result[key] = value[key]
    # Also accept our own normalized status (e.g. WifiProvisionError.status).
    for key, low, high in _DIAGNOSTIC_INTS:
        if type(value.get(key)) is int and low <= value[key] <= high:
            result[key] = value[key]
    for key in ('bssid', 'sta_mac'):
        if type(value.get(key)) is str and re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', value[key]):
            result[key] = value[key].lower()
    if type(value.get('country')) is str and re.fullmatch(r'[A-Z]{2}|01', value['country']):
        result['country'] = value['country']
    # Compact array keeps the existing protected STATUS below 512 B, including
    # its original TLS fields. Never retain the array or arbitrary nested text.
    diag = value.get('diag')
    bounds = {0: (0, 2**32-1), 1: (0, _MONOTONIC_MS_MAX),
              2: (-1, 2**32-1), 3: (-1, 2**32-1), 4: (-1, 2**32-1),
              5: (0, 65535), 7: (-128, 127), 8: (0, 255),
              10: (-128, 127), 12: (0, 65535)}
    if (type(diag) is list and len(diag) == 13
            and all(type(diag[i]) is int and low <= diag[i] <= high for i, (low, high) in bounds.items())
            and all(type(diag[i]) is str and re.fullmatch(r'(?:[0-9a-fA-F]{12})?', diag[i]) for i in (6, 11))
            and type(diag[9]) is str and re.fullmatch(r'(?:[A-Z]{2}|01)?', diag[9])):
        decoded = dict(attempt_id=diag[0], attempt_ms=diag[1], disconnect_count=diag[5])
        for key, index in (('sta_start_ms', 2), ('connected_ms', 3), ('got_ip_ms', 4)):
            decoded[key] = -1 if diag[index] == -1 else diag[1]+diag[index]
        for key, index, missing in (('rssi', 7, -128), ('channel', 8, 0),
                                    ('tx_power_qdbm', 10, -128), ('ble_interval', 12, 0)):
            if diag[index] != missing:
                decoded[key] = diag[index]
        for key, index in (('bssid', 6), ('sta_mac', 11)):
            if diag[index]:
                decoded[key] = ':'.join(diag[index][i:i+2].lower() for i in range(0, 12, 2))
        if diag[9]:
            decoded['country'] = diag[9]
        result.update(decoded)
    return result


# ESP-IDF wifi_err_reason_t, not the separate provisioning error_code enum.
_WIFI_REASONS = {
    -1: '未收到断开事件（stage=0 表示启动后尚无 APPLY）',
    1: '未指定原因', 2: '认证过期/认证响应超时（也可能来自 AP）', 3: '认证后离开', 4: '因不活跃被断开',
    5: 'AP 关联数已满', 6: '未认证站点发送 Class 2 帧',
    7: '未关联站点发送 Class 3 帧', 8: '主动离开关联', 9: '关联时尚未认证',
    10: '功率能力不符合要求', 11: '支持信道不符合要求', 12: 'BSS 切换断开',
    13: '信息元素无效', 14: 'MIC 校验失败', 15: '四次握手超时',
    16: '组密钥更新超时', 17: '四次握手信息元素不一致',
    18: '组加密算法无效', 19: '单播加密算法无效', 20: 'AKMP 无效',
    21: 'RSN 信息元素版本不支持', 22: 'RSN 能力无效', 23: '802.1X 认证失败',
    24: '加密套件被拒绝', 29: '加密算法或 AKM 不符合要求',
    34: '缺少 ACK', 39: '超时', 46: '对端发起断开', 47: 'AP 发起断开',
    49: 'PMKID 无效', 200: 'Beacon 超时', 201: '未找到 AP',
    202: '认证失败', 203: '关联失败', 204: '握手超时', 205: '连接失败',
    206: 'AP TSF 重置', 207: '漫游', 208: '关联重试等待时间过长',
    209: 'SA Query 超时', 210: '未找到安全配置兼容的 AP',
    211: '未找到符合认证模式阈值的 AP', 212: '未找到符合 RSSI 阈值的 AP',
}
_WIFI_STAGES = {0: '尚无 APPLY', 1: 'set_config/设置配置', 2: 'start/启动',
                3: 'connect/发起连接', 4: 'wait_ip/等待接入或 IP，不代表已关联', 5: 'save/保存'}


def network_diagnostics_text(value):
    """Format bounded numbers/MAC/country only; no arbitrary backend text."""
    safe = network_status_fields(value)
    reason, stage, err = (safe.get(key) for key in ('reason', 'stage', 'api_err'))
    reason_text = ('未提供（当前固件需要增加诊断字段）' if reason is None else
                   str(reason) + '（' + _WIFI_REASONS.get(reason, '未知断开原因') + '）')
    stage_text = '未提供' if stage is None else str(stage) + '（' + _WIFI_STAGES[stage] + '）'
    err_text = '未提供' if err is None else str(err) + '（0x' + format(err & 0xffffffff, '08x') + '）'
    text = ('reason=' + reason_text + '；stage=' + stage_text + '；api_err=' + err_text
            + '；got_ip=' + str(safe.get('got_ip', '未提供')))
    if 'attempt_id' in safe:
        text += '；attempt_id=' + str(safe['attempt_id']) + '；attempt_ms=' + str(safe.get('attempt_ms', '未提供'))
        for key in ('sta_start_ms', 'connected_ms', 'got_ip_ms'):
            stamp = safe.get(key, -1)
            text += '；' + key + '=' + ('未发生（本次 APPLY 内）' if stamp == -1 else str(stamp))
        for key in ('disconnect_count', 'bssid', 'rssi', 'country', 'sta_mac'):
            text += '；' + key + '=' + str(safe.get(key, '未提供'))
        text += '；channel=' + str(safe.get('channel', '未提供'))
        if safe.get('connected_ms', -1) == -1:
            text += '（当前射频信道，未证明关联）'
        power, interval = safe.get('tx_power_qdbm'), safe.get('ble_interval')
        text += '；TX功率上限=' + ('未提供' if power is None else f'{power/4:g} dBm')
        text += '；BLE实际间隔=' + ('未提供' if interval is None else f'{interval*1.25:g} ms')
    return text


class WifiProvisionError(ValueError):
    """Structured public status, with no SSID, credential or backend text."""
    def __init__(self, value):
        self.status = network_status_fields(value)
        self.code = self.status.get('error')
        self.reason = self.status.get('reason')
        text = '设备配网失败，错误码：' + (str(self.code) if self.code is not None else '未知')
        text += '；' + network_diagnostics_text(self.status)
        super().__init__(text)


class LanTransport:
    def __init__(self, received, disconnected):
        self.received, self.disconnected = received, disconnected
        self.writer = None
        self.tasks = []
        self.closed = False
        self._disconnect_notified = False

    def _notify_disconnected(self, message):
        """Report one physical disconnect even if read and heartbeat fail together."""
        if self.closed or self._disconnect_notified:
            return
        self._disconnect_notified = True
        self.disconnected(message)

    async def connect(self, ip, fingerprint, token, port=PORT, *, identity=None):
        pin, key = secret(fingerprint), secret(token)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        # A device certificate is trusted ONLY after the exact DER SHA-256 pin
        # obtained over authenticated BLE matches. Never send the token first.
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
        try:
            from .lan_routes import interface_for, open_connection
            ip = ipv4(ip)
            self.interface = await interface_for(ip, identity, port)
            reader, self.writer = await asyncio.wait_for(open_connection(ip, port, context, self.interface), 5)
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
        buffer = bytearray()
        try:
            while not self.closed:
                data = await asyncio.wait_for(reader.read(4096), 12)
                if not data:
                    raise ConnectionError('设备关闭连接')
                buffer.extend(data)
                used = len(buffer)//20*20
                for offset in range(0, used, 20):
                    p = bytes(buffer[offset:offset+20])
                    if p[0] != 1:
                        raise ValueError('设备报文格式错误')
                    if p[1] != 0x7f:
                        self.received(p)
                del buffer[:used]
        except asyncio.CancelledError:
            pass
        except Exception:
            self._notify_disconnected('局域网连接中断或超时')

    async def heartbeat(self):
        try:
            while not self.closed:
                await asyncio.sleep(3)
                await self.write(bytes([1, 0x7f]) + bytes(18))
        except asyncio.CancelledError:
            pass
        except Exception:
            self._notify_disconnected('局域网心跳失败')

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
        self.wifi_apply_acked = False
        self.wifi_handoff_expected = False
        self.wifi_disconnected = asyncio.Event()

    async def connect(self, device):
        from bleak import BleakClient
        self.provision_authenticated = False
        self.client = BleakClient(device, disconnected_callback=lambda _: self.lost(), timeout=20)
        try:
            await self.client.connect()
            if self.client.services.get_service(SERVICE) is None:
                raise ValueError('设备没有 CAN Scope 服务')
            # The optional characteristic batches complete v1 packets without
            # changing legacy c0de0003 or requiring a control/CAN command.
            get_characteristic = getattr(self.client.services, 'get_characteristic', lambda _: None)
            batched = get_characteristic(BATCH_NOTIFY) is not None
            await self.client.start_notify(BATCH_NOTIFY if batched else NOTIFY, self.notification)
        except BaseException:
            await self.close()
            raise

    def notification(self, _, data):
        if not data or len(data) > 240 or len(data) % 20:
            return
        # Validate the entire value before delivering any part of it.
        if any(data[offset] != 1 for offset in range(0, len(data), 20)):
            return
        for offset in range(0, len(data), 20):
            self.received(bytes(data[offset:offset+20]))

    def lost(self):
        self.provision_authenticated = False
        self.wifi_disconnected.set()
        if not self.closed and not self.wifi_handoff_expected:
            self.disconnected('蓝牙连接已断开')

    async def write(self, p):
        if self.closed or not self.client or not self.client.is_connected:
            raise ConnectionError('蓝牙未连接')
        await asyncio.wait_for(self.client.write_gatt_char(WRITE, p, response=True), 5)

    async def use_existing_bond(self):
        """Reuse a BlueZ bond without Pair or agent registration.

        The protected GATT characteristic still enforces encryption and MITM;
        Paired=True alone does not bypass those checks.
        """
        from dbus_fast import Message, MessageType
        self.provision_authenticated = False
        backend = getattr(self.client, '_backend', None)
        bus, path = getattr(backend, '_bus', None), getattr(backend, '_device_path', None)
        if self.closed or not self.client or not self.client.is_connected or bus is None or not path:
            raise ConnectionError('无法检查当前目标的已有蓝牙绑定；不会自动配对')
        for property_name in ('Paired', 'Bonded'):
            reply = await asyncio.wait_for(bus.call(Message(
                destination='org.bluez', path=path, interface='org.freedesktop.DBus.Properties',
                member='Get', signature='ss', body=['org.bluez.Device1', property_name])), 5)
            if (reply.message_type != MessageType.METHOD_RETURN or len(reply.body) != 1
                    or reply.body[0].value is not True):
                raise ConnectionError('当前目标没有已确认的蓝牙绑定；不会自动配对')
        self.provision_authenticated = True

    async def provision(self, operation, ssid, password, status, networks, *, pair=True, handoff=False):
        """Use pair=False only to reuse authentication on this same connection."""
        commands = []
        p = None
        try:
            if handoff and operation != 5:
                raise ValueError('只有 APPLY 支持 BLE/Wi-Fi 交接')
            if operation == 5:
                self.wifi_apply_acked = False
                self.wifi_disconnected.clear()
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
            if operation == 5:
                self.wifi_apply_acked = True
            if handoff:
                # APPLY's ATT write response has arrived. The existing STATUS
                # request is a receipt fence, not a retry or a new wire opcode.
                self.wifi_handoff_expected = True
                try:
                    await self.client.write_gatt_char(NETWORK, bytes([1])+bytes(19), response=True)
                except Exception:
                    if self.client.is_connected and not self.wifi_disconnected.is_set():
                        raise
                if self.client.is_connected:
                    await asyncio.wait_for(self.wifi_disconnected.wait(), 5)
                return
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
                raise WifiProvisionError(value)
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


async def _discover_interface(identity, found, broadcast, timeout, interface=None):
    loop = asyncio.get_running_loop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if interface:
            from .lan_routes import bind_device
            try:
                bind_device(sock, interface)
            except OSError:
                return  # The parallel default-route socket remains available.
        sock.bind(('', 0))
        await loop.sock_sendto(sock, b'CAN_SCOPE_DISCOVER_1', (ipv4(broadcast), PORT))
        # Existing app images incorrectly require 19 bytes, omitting the final
        # version digit. Keep the canonical request and probe that legacy form.
        await loop.sock_sendto(sock, b'CAN_SCOPE_DISCOVER_', (ipv4(broadcast), PORT))
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                data, address = await asyncio.wait_for(loop.sock_recvfrom(sock, 128), deadline-loop.time())
                if address[1] == PORT and data.decode('ascii', errors='ignore').split() == ['CAN_SCOPE_1', identity, str(PORT)]:
                    found(dict(address='wifi:'+address[0], name='ESP32-CAN', rssi=0, transport='wifi'))
            except asyncio.TimeoutError:
                break


async def discover(identity, found, *, broadcast='255.255.255.255', timeout=3):
    broadcast = ipv4(broadcast)
    from .lan_routes import interfaces
    choices = interfaces() if broadcast == '255.255.255.255' else []
    if len(choices) < 2:
        return await _discover_interface(identity, found, broadcast, timeout)
    seen = set()
    def unique(device):
        if device['address'] not in seen:
            seen.add(device['address'])
            found(device)
    async def probe(interface):
        try:
            await _discover_interface(identity, unique, broadcast, timeout, interface)
        except OSError:
            pass  # A disappearing interface must not cancel the other probes.
    tasks = [asyncio.create_task(probe(name)) for name in [None, *choices]]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
