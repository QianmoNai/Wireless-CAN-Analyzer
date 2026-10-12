"""Wire encoding shared by the desktop transports; no GUI dependencies."""
import ipaddress
import re
import struct

SERVICE = 'c0de0001-1212-efde-1523-785feabcd123'
WRITE = 'c0de0002-1212-efde-1523-785feabcd123'
NOTIFY = 'c0de0003-1212-efde-1523-785feabcd123'
NETWORK = 'c0de0004-1212-efde-1523-785feabcd123'
PORT = 32323


def u32(p, offset):
    return struct.unpack_from('<I', p, offset)[0]


def packet(kind, a=0, b=0, x=0, y=0, z=0, token=0):
    return struct.pack('<BBBBIIII', 1, kind, a, b, x, y, z, token)


def ipv4(value):
    return str(ipaddress.IPv4Address(value))


def secret(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{64}', value):
        raise ValueError('缺少有效的设备凭据，请先通过蓝牙读取网络状态')
    return bytes.fromhex(value)


def can_command(identifier, data, extended=False, remote=False, dlc=None, token=1):
    if type(identifier) is not int or not 0 <= identifier <= (0x1fffffff if extended else 0x7ff):
        raise ValueError('CAN ID 超出所选帧类型的范围')
    data = bytes(data)
    dlc = len(data) if dlc is None else dlc
    if type(dlc) is not int or not 0 <= dlc <= 8 or len(data) > 8:
        raise ValueError('经典 CAN DLC 必须为 0–8')
    if (remote and data) or (not remote and dlc != len(data)):
        raise ValueError('数据长度与 DLC 不一致；远程帧不能携带数据')
    p = bytearray(packet(0x10, int(extended) | (int(remote) << 1), dlc, identifier, token=token))
    p[8:8+len(data)] = data
    return bytes(p)


def setting_command(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{40}', value):
        raise ValueError('设置报文格式错误')
    p = bytes.fromhex(value)
    if p[0] != 1:
        raise ValueError('不支持的协议版本')
    if p[1] == 0x12 and p[2:4] == b'\0\0' and p[8:16] == bytes(8) and u32(p, 4) in (125000, 250000, 500000, 1000000):
        return p
    if p[1] == 0x13 and 1 <= p[2] <= 16 and not p[3] and not any(p[4+p[2]:]):
        name = p[4:4+p[2]].decode('utf8')
        if name.strip() and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name):
            return p
    raise ValueError('不允许的设置报文')


def wifi_commands(operation, ssid='', password='', transaction=1):
    if operation not in (1, 2, 5, 6):
        raise ValueError('无效配网操作')
    if operation != 5:
        return [bytes([operation]) + bytes(19)]
    name, key = ssid.encode('utf8'), password.encode('ascii')
    if not 1 <= len(name) <= 32 or b'\0' in name:
        raise ValueError('Wi-Fi 名称须为 1–32 UTF-8 字节')
    if key and not (re.fullmatch(rb'[\x20-\x7e]{8,63}', key) or re.fullmatch(rb'[0-9a-fA-F]{64}', key)):
        raise ValueError('Wi-Fi 密码须为 8–63 ASCII 字符或 64 位十六进制密钥')
    result = [bytes([3, transaction, len(name), len(key)]) + bytes(16)]
    content = name + key
    for start in range(0, len(content), 16):
        part = content[start:start+16]
        result.append(bytes([4, transaction, start, len(part)]) + part.ljust(16, b'\0'))
    return result + [bytes([5, transaction]) + bytes(18)]
