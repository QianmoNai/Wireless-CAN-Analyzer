"""Bounded CAN history export, shared by the Qt dialog and offline checks."""
import json

MAX_ROWS = 20000
MAX_BRIDGE_PAYLOAD = 4_000_000
HEADER = ['kind', 'CAN_ID', 'EXT', 'RTR', 'DLC', 'data', 'receivedAt', 'token']


def rows(payload):
    if type(payload) is not str:
        raise ValueError('CAN 导出数据无效')
    value = json.loads(payload)
    if type(value) is not list or len(value) > MAX_ROWS:
        raise ValueError('CAN 导出最多 20000 条报文')
    result = []
    for frame in value:
        if type(frame) is not dict:
            raise ValueError('CAN 导出报文无效')
        def integer(name, low, high, default=None):
            number = frame.get(name, default)
            if type(number) is not int or not low <= number <= high:
                raise ValueError('CAN 导出字段无效：'+name)
            return number
        kind = integer('kind', 1, 3)
        ext, rtr = frame.get('ext'), frame.get('rtr')
        if type(ext) is not bool or type(rtr) is not bool:
            raise ValueError('CAN 导出帧类型无效')
        identifier = integer('id', 0, 0x1fffffff if ext else 0x7ff)
        dlc = integer('dlc', 0, 8)
        data = frame.get('data')
        if (type(data) is not list or len(data) != (0 if rtr else dlc)
                or any(type(byte) is not int or not 0 <= byte <= 255 for byte in data)):
            raise ValueError('CAN 导出数据字节无效')
        result.append([kind, hex(identifier), int(ext), int(rtr), dlc,
                       ' '.join(f'{byte:02X}' for byte in data),
                       integer('receivedAt', 0, 2**53-1, 0),
                       integer('seq', 0, 0xffffffff, 0)])
    return result
