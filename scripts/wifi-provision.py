#!/usr/bin/env python3
"""交互式 BLE 安全配网；只由用户本人在终端运行。

本脚本会配对所选设备并修改设备的 Wi-Fi 设置。
先关闭桌面端/手机 App，在设备屏幕手动选择蓝牙模式。
仅支持目标设备 90da7273722c；不操作串口或电脑网络连接。
Linux 内置临时配对代理；仅在出现“ESP32 六位配对码”提示时，在本终端输入六位码。
"""
import argparse
import asyncio
import getpass
import ipaddress
import logging
import os
from pathlib import Path
import re
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'desktop'))

TARGET = '90da7273722c'
SUBNET = ipaddress.IPv4Network('192.168.1.0/24')
USB_PORT = '/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_90:DA:72:73:72:2C-if00'
NOTICE = '本脚本会配对所选设备并修改设备的 Wi-Fi 设置。'
CURRENT_STEP = '启动/参数检查'


class StopProvision(Exception):
    """Messages are fixed literals; never include a credential or backend error."""


def step(value):
    global CURRENT_STEP
    CURRENT_STEP = value


def report_failure(exc, where=None):
    """Only structured error codes, class names and our own fixed messages."""
    from bleak.exc import BleakDBusError, BleakGATTProtocolError
    from dbus_fast import DBusError
    from can_scope.bluez_agent import AgentSetupError
    category = '未分类错误'
    message = '请检查当前步骤；敏感错误正文未输出。'
    if isinstance(exc, (StopProvision, AgentSetupError)):
        category, message = '前置检查/设备状态', str(exc)
    elif isinstance(exc, BleakGATTProtocolError):
        try:
            code = int(exc.code)
        except ValueError:
            code = None
        if code == 3:
            category, message = 'BLE 写入不允许', '设备可能仍在 Wi-Fi/USB/dual 模式；请在设备屏幕选择蓝牙模式后重试。'
        elif code in (5, 8, 15):
            category, message = 'BLE 安全认证不足', '请完成本终端六位码认证；不绕过加密或 MITM 校验。'
    elif isinstance(exc, (BleakDBusError, DBusError)):
        name = exc.dbus_error if isinstance(exc, BleakDBusError) else exc.type
        known = {
            'org.bluez.Error.AuthenticationFailed': ('认证失败', '请核对 ESP32 屏幕六位码；脚本不自动重试配对。'),
            'org.bluez.Error.AuthenticationRejected': ('认证被拒绝', '请核对实体设备和六位码，不自动重试配对。'),
            'org.bluez.Error.AuthenticationCanceled': ('认证已取消', '配对被取消；仅在终端六位码提示出现后输入。'),
            'org.bluez.Error.AuthenticationTimeout': ('认证超时', '请在终端六位码提示出现后及时输入；不会自动重试。'),
            'org.bluez.Error.Canceled': ('终端认证取消/超时', '请输入恰好六位数字；回车、输入超时或远端取消会停止认证。'),
            'org.bluez.Error.Rejected': ('代理拒绝请求', '请求不属于所选设备，或不是要求的六位码认证流程。'),
            'org.bluez.Error.InProgress': ('BlueZ 操作进行中', '目标连接/配对或扫描尚在进行；关闭原脚本并等待后重试，不重启适配器。'),
            'org.bluez.Error.NotReady': ('蓝牙未就绪', '请检查系统蓝牙与设备状态；脚本不会重启 hci0。'),
            'org.bluez.Error.NotPermitted': ('BLE 操作不允许', '请确认设备为蓝牙模式（network=0），且未被其他客户端占用。'),
            'org.bluez.Error.NotAuthorized': ('蓝牙未授权', '请完成目标设备的终端配对认证。'),
            'org.bluez.Error.AlreadyExists': ('配对代理已存在', '当前 D-Bus 会话已有代理；关闭原脚本后重新运行。'),
            'org.freedesktop.DBus.Error.AccessDenied': ('D-Bus 权限不足', '请在正常用户桌面会话运行；不使用 sudo。'),
            'org.freedesktop.DBus.Error.NoReply': ('D-Bus 无回复', '请检查蓝牙服务状态，等待旧操作结束后重试。'),
        }
        category, message = known.get(name, ('BlueZ/D-Bus 失败', '后端错误正文未输出；请检查目标设备、模式与当前连接。'))
    elif isinstance(exc, TimeoutError):
        category, message = '等待超时', '当前步骤未按时完成；配对码只在本终端明确提示后输入，结果未知时先查询。'
    elif isinstance(exc, (EOFError, KeyboardInterrupt)):
        category, message = '用户中止', '已停止；已提交配置可能仍生效，请先 QUERY，不盲目重发。'
    elif isinstance(exc, ConnectionError):
        category, message = '连接中断', '请检查目标设备模式和其他客户端占用。'
    elif isinstance(exc, getpass.GetPassWarning):
        category, message = '终端不能隐藏输入', '请在正常交互式终端运行；拒绝回退到明文密码输入。'
    elif isinstance(exc, OSError):
        category, message = '系统 I/O 失败', '请检查终端、蓝牙连接、缓存目录权限或网络状态。'
    name = type(exc).__name__
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
        name = 'Exception'
    print(f'失败步骤：{where or CURRENT_STEP}；异常类型：{name}；分类：{category}。{message}', file=sys.stderr)


def pairing_agent(client, address):
    from can_scope.bluez_agent import TerminalPairingAgent
    return TerminalPairingAgent(client, address)


def recovery_commands():
    command = f'desktop/.venv/bin/python scripts/serial-settings.py --port {USB_PORT}'
    return '\n'.join((command, command + ' --mode usb --data uart --baud 115200',
                      'sleep 0.5', command))


def protect_process():
    # Backends can log GATT payloads at DEBUG. Do not allow those logs at any level.
    logging.disable(sys.maxsize)
    if sys.platform == 'linux':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Last resort for failures outside the guarded asyncio entry point.
    sys.excepthook = lambda *_: print('配网异常，详细内容已隐藏；请按恢复命令查询设备。', file=sys.stderr)


def read_credentials():
    ssid = input('SSID（区分大小写，支持 2.4 GHz 网络）：')
    if not 1 <= len(ssid.encode('utf8')) <= 32 or '\0' in ssid:
        raise StopProvision('SSID 须为 1–32 UTF-8 字节且不能包含空字符。')
    password = ''
    try:
        # Refuse getpass's echoing fallback, including when /dev/tty is unavailable.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('Wi-Fi 密码（不回显；开放网络直接回车）：')
        valid = (not password or
                 (8 <= len(password) <= 63 and all(32 <= ord(c) <= 126 for c in password)) or
                 (len(password) == 64 and all(c in '0123456789abcdefABCDEF' for c in password)))
        if not valid:
            raise StopProvision('密码格式无效：须为 8–63 个可打印 ASCII 字符或 64 位十六进制；开放网络可留空。')
        return ssid, password
    finally:
        # Python/getpass and the BLE backend use immutable copies; this releases
        # references but cannot promise physical erasure of every memory copy.
        ssid = password = ''


async def select_device(scan):
    print('[1/7] 扫描 CAN Scope BLE 服务（约 5 秒）；其他设备不会被连接或配对。')
    devices = await scan(lambda _: None)
    choices = {address.upper(): device for address, device in devices.items()
               if re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address)}
    if not choices:
        raise StopProvision('没有发现设备；请检查设备已选择蓝牙模式且未被其他客户端占用。')
    for address in sorted(choices):
        print('  BLE 地址：' + address)
    print('目标 STA ID：' + TARGET + '。BLE 地址可能与 STA ID 不同，请先核对实体设备和已知 BLE 地址。')
    address = input('只输入已确认属于该目标设备的完整 BLE 地址（回车取消）：').strip().upper()
    if address not in choices:
        raise StopProvision('未确认扫描列表中的目标地址，已停止。')
    return address, choices[address]


async def find_ip(discover):
    print('[6/7] 在 192.168.1.0/24 发送 UDP 32323 发现，最长约 30 秒。')
    for _ in range(10):
        found = set()

        def accept(row):
            ip = ipaddress.IPv4Address(row['address'].removeprefix('wifi:'))
            if ip in SUBNET and ip not in (SUBNET.network_address, SUBNET.broadcast_address):
                found.add(str(ip))

        await discover(TARGET, accept, broadcast=str(SUBNET.broadcast_address))
        if len(found) > 1:
            raise StopProvision('同一设备 ID 出现多个 IP，已停止；请检查网络中的重复设备或发现报文。')
        if found:
            return found.pop()
    raise StopProvision('未发现目标 IP；请检查 2.4 GHz、同网段、DHCP、客户端隔离和 UDP 32323。')


async def provision():
    from can_scope.connection_store import connection_folder, load_connection, save_connection
    from can_scope.engine import Channel
    from can_scope.protocol import secret
    from can_scope.transports import BleTransport, LanTransport, discover, scan_ble

    # Suppress arbitrary backend exception messages in asyncio diagnostics too.
    asyncio.get_running_loop().set_exception_handler(
        lambda *_: print('后台连接异常，详细内容已隐藏；请检查当前步骤。', file=sys.stderr))
    step('启动/读取连接缓存')
    path = connection_folder() / 'connection-1.json'
    saved = load_connection(path)  # Fail before pairing if the existing cache is corrupt.
    step('[1/7] BLE 扫描/选择')
    address, device = await select_device(scan_ble)
    channel = Channel(lambda *_: None, saved, lambda value: save_connection(path, value))

    def disconnected(_):
        for future in channel.waiters.values():
            if not future.done():
                future.set_exception(ConnectionError('连接已断开'))

    trusted = {}
    completed = False

    def status(value):
        nonlocal completed
        # No arbitrary JSON, SSID, token, fingerprint or error repr goes to stdout.
        if value.get('id') != TARGET:
            raise StopProvision('认证后的设备 ID 与目标不符，已停止；不会发送 Wi-Fi 凭据或覆盖信任缓存。')
        secret(value.get('fp'))
        secret(value.get('token'))
        trusted.update({key: value[key] for key in ('id', 'fp', 'token')})
        if value.get('ip'):
            trusted['ip'] = str(ipaddress.IPv4Address(value['ip']))
        completed = value.get('state') == 2 and value.get('busy') is False and not value.get('error')

    ble = BleTransport(channel.receive, disconnected)
    try:
        step('[1/7] BLE 连接')
        await ble.connect(device)
        channel.transport, channel.ready = ble, True
        step('[1/7] BLE QUERY/模式检查')
        current = await channel.handle('deviceSettings', [{}])
        if current['status'] != 0 or current['network'] != 0:
            raise StopProvision('设备未处于蓝牙模式；请在设备屏幕切换后重新运行。')
        print('[2/7] 注册本进程临时配对代理；仅对此地址调用一次配对/认证。')
        print('请等待本终端出现“ESP32 六位配对码”提示，再输入目标屏幕上的六位码；不会在系统弹窗中输入。')
        step('[2/7] 注册终端配对代理')
        async with pairing_agent(ble.client, address):
            step('[2/7] BLE 配对/认证和身份核对')
            await asyncio.wait_for(ble.provision(1, '', '', status, lambda _: None), 105)
        print('本进程配对代理已注销。')
        print('已通过加密 BLE 核对目标 ID；TLS 信任信息仅保留在内存中。')
        ssid = password = ''
        try:
            step('[3/7] 终端凭据输入')
            ssid, password = read_credentials()
            print('[3/7] 发送 Wi-Fi 配置并等待设备接入；复用当前认证，不再次配对。')
            pending = ble.provision(5, ssid, password, status, lambda _: None, pair=False)
            ssid = password = ''
            step('[3/7] 配网发送/接入')
            await asyncio.wait_for(pending, 105)
            if not completed:
                raise StopProvision('设备未确认 Wi-Fi 已接入并保存。')
        finally:
            ssid = password = ''
        print('设备已报告配网成功。')
        print('[4/7] 仅发送一次 Wi-Fi 模式设置；回执不确定时不重发，以最终 QUERY 为准。')
        step('[4/7] 设置 Wi-Fi 模式')
        try:
            result = await channel.handle('deviceSettings', [dict(network=1)])
            if result['status'] != 1 or result['network'] != 1:
                raise StopProvision('Wi-Fi 模式设置未确认。')
        except Exception as exc:
            # Mode switching may reset notification routing before its ACK arrives.
            # Never replay the mutation; a successful TLS QUERY must prove mode=1.
            report_failure(exc)
            print('Wi-Fi 模式回执未确认，结果未知；继续发现并核验，不重发设置。')
        step('[5/7] 保存信任缓存')
        channel.mode, channel.address = 'wifi', address
        channel.network_status(trusted)  # Desktop validation, whitelist and persistence.
        print('[5/7] 已按桌面端方式原子保存 connection-1.json（权限 0600）；不显示令牌或指纹。')
    finally:
        channel.ready = False
        await ble.close()

    step('[6/7] UDP 发现')
    ip = await find_ip(discover)
    print('设备 IP：' + ip)
    channel.saved['ip'] = ip
    step('[6/7] 保存发现的 IP')
    channel.persist()
    lan = LanTransport(channel.receive, disconnected)
    try:
        print('[7/7] 尝试一次 TLS 1.2 连接：先校验证书指纹，再发送认证令牌；随后只发送一次 QUERY。')
        step('[7/7] TLS 连接/认证')
        await lan.connect(ip, trusted['fp'], trusted['token'])
        channel.transport, channel.ready = lan, True
        step('[7/7] TLS QUERY')
        result = await channel.handle('deviceSettings', [{}])
        if result['status'] != 0 or result['network'] != 1:
            raise StopProvision('TLS QUERY 未确认 Wi-Fi 模式。')
        print(f"成功：TLS 认证通过；QUERY result=0 network=1 data={result['data']} baud={result['baud']}；IP={ip}")
    finally:
        channel.ready = False
        await lan.close()
        trusted.clear()
        channel.saved.clear()
        saved.clear()


def dry_run():
    # No credentials, scanning, pairing, sockets, serial ports or cache access.
    import importlib.util
    from can_scope.bluez_agent import PasskeyAgent
    from can_scope.connection_store import load_connection, save_connection
    from can_scope.engine import Channel
    from can_scope.protocol import device_settings_command, device_settings_status, packet, wifi_commands
    from can_scope.transports import BleTransport, LanTransport, discover, scan_ble
    for module in ('bleak', 'dbus_fast', 'serial', 'PySide6.QtCore'):
        if importlib.util.find_spec(module) is None:
            raise StopProvision('缺少桌面端依赖，请先按 desktop/README.md 配置虚拟环境。')
    if (device_settings_command(network=1)[4] != 1 or
            device_settings_command()[2:16] != bytes(14) or
            device_settings_status(packet(22, b=1, y=115200, token=1))['status'] != 0 or
            wifi_commands(1) != [bytes([1]) + bytes(19)]):
        raise StopProvision('协议自检失败。')
    if not all(callable(item) for item in (load_connection, save_connection, Channel,
                                           BleTransport.provision, LanTransport.connect, discover, scan_ble)):
        raise StopProvision('桌面端复用接口自检失败。')
    interface = PasskeyAgent('/org/bluez/hci0/dev_90_DA_72_73_72_2E').introspect()
    if not any(item.name == 'RequestPasskey' and item.in_signature == 'o' and item.out_signature == 'u'
               for item in interface.methods):
        raise StopProvision('BlueZ 配对代理接口自检失败。')
    print('DRY-RUN PASS：依赖及无凭据协议自检通过；未扫描、未配对、未打开网络/串口、未读取或写入缓存。')
    print('真实运行将依次：确认地址 → 一次 BLE 认证并核对 ID → 隐藏输入密码 → 配网 → Wi-Fi 模式 → 私有缓存 → UDP 发现 → 一次 TLS + QUERY。')
    print('配对代理接口自检通过；真实配对时仅在本终端的“ESP32 六位配对码”提示处输入，不依赖系统配对代理。')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog='恢复 USB直连+UART+115200（先关闭所有占用串口的程序，在仓库根目录依次运行）：\n' + recovery_commands())
    parser.add_argument('--dry-run', action='store_true', help='离线无凭据自检；不扫描、不配对、不写缓存、不操作硬件')
    args = parser.parse_args()
    protect_process()
    step('启动/参数检查')
    try:
        print(NOTICE)
        if args.dry_run:
            dry_run()
            return 0
        if os.geteuid() == 0:
            raise StopProvision('请用普通桌面用户运行，禁止 root。')
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise StopProvision('必须由用户在交互式终端运行；不接受重定向或管道输入。')
        print('请关闭桌面端和手机 App，在设备屏幕选择蓝牙模式；电脑保持现有网络连接。')
        if input('确认仅配对并配置目标 ' + TARGET + '，请输入该 ID（回车取消）：').strip() != TARGET:
            raise StopProvision('用户未确认，已停止。')
        asyncio.run(provision())
        return 0
    except (KeyboardInterrupt, EOFError) as exc:
        report_failure(exc)
    except Exception as exc:
        report_failure(exc)
    print('已停止；先查询设备状态，重试配网前请保持设备为蓝牙模式。手动 USB 恢复命令见 --help。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
