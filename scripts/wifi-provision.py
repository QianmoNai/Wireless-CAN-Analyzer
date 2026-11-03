#!/usr/bin/env python3
"""交互式 BLE 安全配网；只由用户本人在终端运行。

本脚本会配对所选设备并修改设备的 Wi-Fi 设置。
先关闭桌面端/手机 App，在设备屏幕手动选择蓝牙模式。
仅支持目标设备 90da7273722c；不操作串口或电脑网络连接。
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


class StopProvision(Exception):
    """Messages are fixed literals; never include a credential or backend error."""


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
    print('目标 STA ID：' + TARGET + '。BLE 地址可能与 STA ID 不同，请先核对实体设备和系统蓝牙管理器。')
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
    path = connection_folder() / 'connection-1.json'
    saved = load_connection(path)  # Fail before pairing if the existing cache is corrupt.
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
        await ble.connect(device)
        channel.transport, channel.ready = ble, True
        current = await channel.handle('deviceSettings', [{}])
        if current['status'] != 0 or current['network'] != 0:
            raise StopProvision('设备未处于蓝牙模式；请在设备屏幕切换后重新运行。')
        print('[2/7] 仅对此地址调用一次配对/认证；请在系统提示中输入目标 ESP32 屏幕上的六位码。')
        await asyncio.wait_for(ble.provision(1, '', '', status, lambda _: None), 105)
        print('已通过加密 BLE 核对目标 ID；TLS 信任信息仅保留在内存中。')
        ssid = password = ''
        try:
            ssid, password = read_credentials()
            print('[3/7] 发送 Wi-Fi 配置并等待设备接入；复用当前认证，不再次配对。')
            pending = ble.provision(5, ssid, password, status, lambda _: None, pair=False)
            ssid = password = ''
            await asyncio.wait_for(pending, 105)
            if not completed:
                raise StopProvision('设备未确认 Wi-Fi 已接入并保存。')
        finally:
            ssid = password = ''
        print('设备已报告配网成功。')
        print('[4/7] 仅发送一次 Wi-Fi 模式设置；回执不确定时不重发，以最终 QUERY 为准。')
        try:
            result = await channel.handle('deviceSettings', [dict(network=1)])
            if result['status'] != 1 or result['network'] != 1:
                raise StopProvision('Wi-Fi 模式设置未确认。')
        except Exception:
            # Mode switching may reset notification routing before its ACK arrives.
            # Never replay the mutation; a successful TLS QUERY must prove mode=1.
            print('Wi-Fi 模式回执未确认，结果未知；继续发现并核验，不重发设置。')
        channel.mode, channel.address = 'wifi', address
        channel.network_status(trusted)  # Desktop validation, whitelist and persistence.
        print('[5/7] 已按桌面端方式原子保存 connection-1.json（权限 0600）；不显示令牌或指纹。')
    finally:
        channel.ready = False
        await ble.close()

    ip = await find_ip(discover)
    print('设备 IP：' + ip)
    channel.saved['ip'] = ip
    channel.persist()
    lan = LanTransport(channel.receive, disconnected)
    try:
        print('[7/7] 尝试一次 TLS 1.2 连接：先校验证书指纹，再发送认证令牌；随后只发送一次 QUERY。')
        await lan.connect(ip, trusted['fp'], trusted['token'])
        channel.transport, channel.ready = lan, True
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
    from can_scope.connection_store import load_connection, save_connection
    from can_scope.engine import Channel
    from can_scope.protocol import device_settings_command, device_settings_status, packet, wifi_commands
    from can_scope.transports import BleTransport, LanTransport, discover, scan_ble
    for module in ('bleak', 'serial', 'PySide6.QtCore'):
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
    print('DRY-RUN PASS：依赖及无凭据协议自检通过；未扫描、未配对、未打开网络/串口、未读取或写入缓存。')
    print('真实运行将依次：确认地址 → 一次 BLE 认证并核对 ID → 隐藏输入密码 → 配网 → Wi-Fi 模式 → 私有缓存 → UDP 发现 → 一次 TLS + QUERY。')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog='恢复 USB直连+UART+115200（先关闭所有占用串口的程序，在仓库根目录依次运行）：\n' + recovery_commands())
    parser.add_argument('--dry-run', action='store_true', help='离线无凭据自检；不扫描、不配对、不写缓存、不操作硬件')
    args = parser.parse_args()
    protect_process()
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
    except StopProvision as exc:
        print(str(exc), file=sys.stderr)  # Only our fixed literal messages.
    except (KeyboardInterrupt, EOFError):
        print('用户中止；已提交的配置可能仍生效，请先 QUERY，勿盲目重发。', file=sys.stderr)
    except Exception:
        # In particular never format UnicodeEncodeError, BleakError, JSON or OS
        # errors: their args/repr/traceback can retain a secret-bearing payload.
        print('当前步骤失败，错误详情已隐藏；请参照排查说明，先确认设备状态再重试。', file=sys.stderr)
    print('恢复 USB直连+UART+115200（仓库根目录，串口程序均关闭后）：\n' + recovery_commands(), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
