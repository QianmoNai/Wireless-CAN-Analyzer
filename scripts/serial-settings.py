#!/usr/bin/env python3
"""Query or persist analyzer settings over physical native USB; never replay SET.

Run with desktop/.venv/bin/python. Close the desktop/monitor first.
"""
import argparse
import json
from pathlib import Path
import secrets
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from can_scope.usb_protocol import Decoder, encode, HELLO, BYE
from can_scope.serial_port import open_native_port

BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
MODES = {'ble': 0, 'wifi': 1, 'dual-a': 2, 'dual-b': 3, 'usb': 4}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', default='/dev/ttyACM0')
    ap.add_argument('--mode', choices=MODES)
    ap.add_argument('--data', choices=('can', 'uart'))
    ap.add_argument('--baud', type=int, choices=BAUDS)
    args = ap.parse_args()
    session = secrets.randbelow(0xffffffff) + 1
    token = secrets.randbelow(0xffffff00) + 1
    decoder = Decoder()
    with open_native_port(args.port, timeout=.05) as port:
        def exchange(payload, match, timeout=3):
            frame = encode(session, payload)
            if port.write(frame) != len(frame):
                raise OSError('USB write incomplete; outcome unknown')
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for sid, p in decoder.feed(port.read(4096)):
                    if sid == session and match(p):
                        return p
            raise TimeoutError('No reply; query before retrying a setting')
        def request(op=0, mask=0):
            p = bytearray(20)
            p[:6] = bytes((1, 0x19, op, mask,
                           MODES[args.mode] if args.mode else 0,
                           int(args.data == 'uart') if args.data else 0))
            if mask & 4:
                struct.pack_into('<I', p, 8, args.baud)
            struct.pack_into('<I', p, 16, token)
            if not op:
                p[3:16] = bytes(13)
            r = exchange(p, lambda r: (r[1] == 22 and struct.unpack_from('<I', r, 16)[0] == token)
                         or (r[1] == 18 and r[2] and struct.unpack_from('<I', r, 4)[0] == token))
            if r[1] == 18:
                if r[2] == 0x11 and not op:
                    raise BlockingIOError('Settings owner is still starting')
                raise ValueError('Settings rejected: ' + r.hex())
            if r[2] == 2:
                raise OSError('NVS save failed; setting was not applied')
            return r
        ready_deadline = time.monotonic() + 8
        while True:
            try:
                r = request()
                break
            except BlockingIOError:
                if time.monotonic() >= ready_deadline:
                    raise
                time.sleep(.2)
        mask = int(args.mode is not None) | (int(args.data is not None) << 1) | (int(args.baud is not None) << 2)
        active = False
        try:
            if mask:
                if r[3] == 4:
                    exchange(HELLO, lambda r: r == HELLO[:2] + b'\x01' + HELLO[3:])
                    active = True
                elif args.mode != 'usb':
                    raise ValueError('当前不是 USB 模式；请先指定 --mode usb（可同时指定数据/波特率）')
                r = request(1, mask)
            print(json.dumps(dict(result=r[2], network_mode=r[3], data_mode=r[4],
                                  baudrate=struct.unpack_from('<I', r, 8)[0],
                                  token=struct.unpack_from('<I', r, 16)[0]), ensure_ascii=False))
        finally:
            if active:
                port.write(encode(session, BYE))


if __name__ == '__main__':
    try:
        from hardware_bench_lock import hardware_bench_lock
        with hardware_bench_lock():
            main()
    except (OSError, ValueError, TimeoutError) as exc:
        sys.exit(str(exc))
