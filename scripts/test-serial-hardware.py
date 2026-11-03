#!/usr/bin/env python3
"""Opt-in physical USB/UART regression. Run with desktop/.venv/bin/python.

Uses the real CUSB encoder/decoder. Requires the analyzer and an independent
3.3 V UART adapter connected as documented in docs/UART.md. No simulated RX.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import secrets
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import serial
from can_scope.usb_protocol import Decoder, encode, HELLO, PING, BYE
from can_scope.protocol import packet, can_command, u32


def drain_setup_bytes(peer):
    """Allow pre-test ROM/adapter USB-buffered bytes to arrive before measuring.

    Capture rather than hide the baseline. No data is discarded after the
    test payload is sent. reset_input_buffer alone misses in-flight USB reads.
    """
    noise = bytearray()
    deadline = time.monotonic() + 1
    quiet_since = time.monotonic()
    while time.monotonic() < deadline:
        data = peer.read(4096)
        if data:
            noise.extend(data)
            quiet_since = time.monotonic()
        if time.monotonic() - quiet_since >= .1:
            return noise.hex()
        time.sleep(.005)
    raise RuntimeError('UART peer never became quiet before test')


class Probe:
    def __init__(self, port):
        self.port = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=1, exclusive=True)
        self.port.dtr = self.port.rts = False
        self.port.port = port
        self.port.open()
        self.decoder = Decoder()
        self.session = secrets.randbelow(0xffffffff) + 1
        self.token = 0x60000000
        self.active = False
        self.last_ping = 0
        self.received = []
        self.pending = deque()

    def send(self, p, session=None):
        frame = encode(self.session if session is None else session, p)
        assert self.port.write(frame) == len(frame)

    def next_token(self):
        self.token += 1
        return self.token

    def wait(self, match, timeout=3, heartbeat=True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.active and heartbeat and time.monotonic() - self.last_ping >= .8:
                self.send(PING)
                self.last_ping = time.monotonic()
            self.pending.extend(self.decoder.feed(self.port.read(4096)))
            while self.pending:
                session, p = self.pending.popleft()
                if session != self.session:
                    continue
                self.received.append(p)
                if match(p):
                    return p
            time.sleep(.005)
        raise TimeoutError('No matching response within %.1fs' % timeout)

    def settings(self, mask=0, network=0, data=0, baud=0, expected=None):
        token = self.next_token()
        p = bytearray(packet(0x19, a=int(bool(mask)), b=mask, y=baud, token=token))
        p[4], p[5] = network, data
        self.send(p)
        r = self.wait(lambda p: (p[1] == 22 and u32(p, 16) == token) or
                      (p[1] == 18 and u32(p, 4) == token and p[2] != 0))
        if r[1] == 18:
            if r[2] == 0x11 and not mask:
                raise BlockingIOError('Settings owner is still starting')
            if expected == 'reject':
                assert r[2] == 0x13, r.hex()
                return r
            raise AssertionError('Settings rejected: ' + r.hex())
        if expected is not None:
            assert r[2] == expected, r.hex()
        return r

    def settled(self, network, data, baud):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            p = self.settings(expected=0)
            if (p[3], p[4], u32(p, 8)) == (network, data, baud):
                return p
            time.sleep(.1)
        raise AssertionError('Settings did not apply: ' + p.hex())

    def hello(self):
        deadline = time.monotonic() + 5
        while True:
            self.send(HELLO)
            try:
                r = self.wait(lambda p: p == HELLO[:2] + b'\x01' + HELLO[3:], .5, False)
                self.active = True
                return r
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise

    def status(self, data, baud):
        return self.wait(lambda p: p[1] == 21 and p[2] == data and
                         u32(p, 4) == baud and (not data or p[3] == 0))

    def uart_rx(self, expected):
        got = bytearray()
        def match(p):
            if p[1] == 19:
                assert 1 <= p[2] <= 12 and not p[3], p.hex()
                got.extend(p[4:4+p[2]])
            return len(got) >= len(expected)
        self.wait(match)
        assert bytes(got) == expected, (got.hex(), expected.hex())

    def uart_tx(self, payload, peer):
        peer.reset_input_buffer()
        for start in range(0, len(payload), 12):
            part = payload[start:start+12]
            token = self.next_token()
            p = bytearray(packet(0x18, a=len(part), token=token))
            p[4:4+len(part)] = part
            self.send(p)
            r = self.wait(lambda p: p[1] == 20 and u32(p, 16) == token)
            assert r[2] == 0 and r[3] == len(part) and r[4:4+len(part)] == part, r.hex()
        got = bytearray()
        deadline = time.monotonic() + 3
        while len(got) < len(payload) and time.monotonic() < deadline:
            got.extend(peer.read(4096))
            time.sleep(.005)
        assert bytes(got) == payload, (got.hex(), payload.hex())

    def uart_sync(self, peer):
        # The fixture may deliver old ROM bytes only with the first TX batch.
        # Establish a measured boundary before sending any test payload.
        marker = b'CS-\x00\xfeSYNC\r\n'
        token = self.next_token()
        p = bytearray(packet(0x18, a=len(marker), token=token))
        p[4:4+len(marker)] = marker
        self.send(p)
        r = self.wait(lambda p: p[1] == 20 and u32(p, 16) == token)
        assert r[2] == 0 and r[3] == len(marker), r.hex()
        got = bytearray()
        deadline = time.monotonic() + 3
        while marker not in got and time.monotonic() < deadline:
            got.extend(peer.read(4096))
            time.sleep(.005)
        assert got.endswith(marker), ('Setup marker missing or followed by unexpected bytes', got.hex())
        return got[:-len(marker)].hex()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usb', default='/dev/ttyACM0')
    ap.add_argument('--uart', default='/dev/ttyACM1')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--can-tx', action='store_true', help='Send one STD 0x7fe data frame (00); only use on a safe bench bus')
    ap.add_argument('--check-only', action='store_true', help='Verify persisted USB/UART/115200 settings without changing them')
    args = ap.parse_args()
    report = {'usb': args.usb, 'uart': args.uart, 'tests': [], 'started': time.strftime('%Y-%m-%d %H:%M:%S')}
    def record(name, status='PASS', **detail):
        row = dict(name=name, status=status, **detail)
        report['tests'].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    probe = None
    try:
        probe = Probe(args.usb)
        ready_deadline = time.monotonic() + 8
        while True:
            try:
                initial = probe.settings(expected=0)
                break
            except BlockingIOError:
                if time.monotonic() >= ready_deadline:
                    raise
                time.sleep(.2)
        record('query_without_HELLO', response=initial.hex())
        if args.check_only:
            assert (initial[3], initial[4], u32(initial, 8)) == (4, 1, 115200), initial.hex()
            probe.hello()
            record('NVS_after_reset', response=probe.status(1, 115200).hex())
            return
        probe.settings(7, 4, 0, 115200, expected=1)
        state = probe.settled(4, 0, 115200)
        record('bootstrap_USB_CAN_115200', response=state.hex())
        for fields in [(1, 5, 0, 0), (2, 0, 2, 0), (4, 0, 0, 12345), (8, 0, 0, 0), (0, 4, 0, 0)]:
            probe.settings(*fields, expected='reject')
        probe.settled(4, 0, 115200)
        record('reject_invalid_values_atomically', cases=5)
        probe.hello()
        probe.send(PING)
        probe.wait(lambda p: p == PING)
        record('HELLO_PING_CAN_type21', response=probe.status(0, 115200).hex())
        if args.can_tx:
            token = probe.next_token()
            probe.send(can_command(0x7fe, b'\x00', token=token))
            accepted = probe.wait(lambda p: p[1] == 18 and u32(p, 4) == token)
            assert accepted[2] == 0, accepted.hex()
            try:
                r = probe.wait(lambda p: p[1] in (2, 3) and u32(p, 16) == token)
                record('CAN_single_TX', 'PASS' if r[1] == 2 else 'BLOCKED', response=r.hex(), note='type2 means bus ACK; type3 means physical CAN send failed')
            except TimeoutError:
                record('CAN_single_TX', 'BLOCKED', note='accepted but no physical completion; no active CAN peer confirmed')
        else:
            record('CAN_single_TX', 'SKIP', note='requires explicit --can-tx on a safe bench bus')
        frames = []
        def observe_can(p):
            if p[1] == 1:
                frames.append(p.hex())
            return False
        try:
            probe.wait(observe_can, 1.2)
        except TimeoutError:
            pass
        record('CAN_physical_RX', 'PASS' if frames else 'BLOCKED', frames=frames[:8],
               count=len(frames), note='Passive CAN observation; no synthetic frames')
        with serial.Serial(args.uart, 115200, timeout=0, write_timeout=1, exclusive=True) as peer:
            for baud in (115200, 230400):
                probe.settings(6, data=1, baud=baud, expected=1)
                probe.settled(4, 1, baud)
                peer.baudrate = baud
                status = probe.status(1, baud)
                setup_noise = drain_setup_bytes(peer)
                setup_noise += probe.uart_sync(peer)
                record('UART_setup_baseline_%d' % baud, discarded_before_test_hex=setup_noise,
                       boundary='CS-00feSYNC CRLF; formal payloads are compared strictly after this boundary')
                payloads = [bytes.fromhex('00 ff 01 7e 80 0d 0a 43 55 53 42 19 20 21'), '串口实机🙂 UTF-8\r\n'.encode()]
                for payload in payloads:
                    peer.write(payload)
                    probe.uart_rx(payload)
                    probe.uart_tx(payload, peer)
                record('UART_HEX_UTF8_bidirectional_%d' % baud, bytes_each_direction=sum(map(len, payloads)), type21=status.hex(), events='19 RX + 20 TX complete + 21 status; physical peer bytes equal')
        probe.send(BYE)
        probe.active = False
        probe.send(PING)
        try:
            probe.wait(lambda p: p == PING, .3, False)
            raise AssertionError('PING accepted after BYE')
        except TimeoutError:
            pass
        probe.session = secrets.randbelow(0xffffffff) + 1
        probe.hello()
        record('BYE_new_session_recovery')
        old = probe.session
        probe.send(PING, old ^ 0x80000000)
        try:
            probe.wait(lambda p: p == PING, .25, False)
            raise AssertionError('Stale session ping accepted')
        except TimeoutError:
            pass
        record('stale_session_rejected')
        probe.active = False
        time.sleep(4.3)
        probe.send(PING)
        try:
            probe.wait(lambda p: p == PING, .3, False)
            raise AssertionError('Expired lease accepted PING')
        except TimeoutError:
            pass
        probe.session = secrets.randbelow(0xffffffff) + 1
        probe.hello()
        record('heartbeat_expiry_recovery')
        probe.settings(1, network=0, expected=1)
        probe.active = False
        probe.settled(0, 1, 230400)
        probe.settings(7, network=4, data=1, baud=115200, expected=1)
        probe.settled(4, 1, 115200)
        probe.hello()
        record('non_USB_mode_bootstrap_back_to_USB', response=probe.status(1, 115200).hex())
        probe.send(BYE)
        probe.active = False
        probe.port.close()
        probe = Probe(args.usb)
        probe.hello()
        record('serial_close_reopen_recovery', response=probe.status(1, 115200).hex())
    except BaseException as e:
        record('exception', 'FAIL', error=repr(e))
        raise
    finally:
        if probe:
            if probe.active:
                probe.send(BYE)
            probe.port.close()
        report['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
