"""Failure-path report tests. FakeSerial only; never opens physical hardware."""
import importlib.util
from collections import deque
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('hardware_probe', Path(__file__).resolve().parents[1] / 'scripts/test-serial-hardware.py')
hardware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardware)

spec = importlib.util.spec_from_file_location('serial_stress', Path(__file__).resolve().parents[1] / 'scripts/test-serial-stress.py')
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)


class FakeSerial:
    def __init__(self, fail_close=False):
        self.closed = False
        self.fail_close = fail_close

    def write(self, data):
        raise OSError('fake USB unplug during BYE')

    def close(self):
        self.closed = True
        if self.fail_close:
            raise OSError('fake close failure')


class ReportTests(unittest.TestCase):
    def test_bye_and_close_failure_still_save_primary_failure(self):
        for close_fails in (False, True):
            with self.subTest(close_fails=close_fails), tempfile.TemporaryDirectory() as directory:
                probe = hardware.Probe.__new__(hardware.Probe)
                probe.port = FakeSerial(close_fails)
                probe.session = 1
                probe.active = True
                report = {'tests': [{'name': 'primary', 'status': 'FAIL', 'error': 'original'}]}
                def record(name, status, **detail):
                    report['tests'].append(dict(name=name, status=status, **detail))
                output = Path(directory) / 'nested/report.json'
                errors = hardware.finish_report(report, output, probe, record)
                saved = json.loads(output.read_text())
                self.assertTrue(probe.port.closed)
                self.assertEqual(len(errors), 2 if close_fails else 1)
                self.assertEqual(saved['tests'][0]['error'], 'original')
                self.assertEqual(saved['tests'][1]['name'], 'cleanup_BYE')
                self.assertIn('finished', saved)
                self.assertFalse(output.with_name(output.name+'.tmp').exists())

    def test_partial_initialization_still_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            errors = hardware.finish_report({'tests': []}, output, None, lambda *a, **kw: None)
            self.assertEqual(errors, [])
            self.assertIn('finished', json.loads(output.read_text()))


class BurstTests(unittest.TestCase):
    def run_fixture(self, outcome='full'):
        started, usb_polled, release, finished = (threading.Event() for _ in range(4))
        rx = deque()
        checkpoints = []

        class Peer:
            output = bytearray()
            writes = 0
            writer_thread = None
            def write(self, data):
                self.writes += 1
                self.writer_thread = threading.get_ident()
                started.set()
                try:
                    if not release.wait(2):
                        raise RuntimeError('USB polling did not release the blocked fixture write')
                    if outcome == 'error':
                        raise OSError('fixture failure with unknown accepted byte count')
                    accepted = len(data) if outcome == 'full' else 7
                    rx.append(data[:accepted])
                    return accepted
                finally:
                    finished.set()
            def read(self, size):
                data = bytes(self.output)
                self.output.clear()
                return data

        peer = Peer()
        class Probe:
            session = 1
            token = 0
            rx_bytes = tx_bytes = sequence = 0
            packets = deque()
            def next_token(self):
                self.token += 1
                return self.token
            def status(self, *args):
                return hardware.packet(21, a=1, x=115200, y=self.rx_bytes,
                                       z=self.tx_bytes)
            def send(self, packet):
                if packet[1] == 0x18:
                    data = packet[4:4+packet[2]]
                    peer.output.extend(data)
                    self.tx_bytes += len(data)
                    ack = bytearray(packet)
                    ack[1:4] = bytes((20, 0, len(data)))
                    self.packets.append((self.session, bytes(ack)))
                elif packet == hardware.PING:
                    self.packets.append((self.session, hardware.PING))
        probe = Probe()
        main_thread = threading.get_ident()
        def poll(_probe):
            if started.is_set() and not finished.is_set():
                # The fixture cannot finish until USB polling runs on the
                # main thread. This event handshake detects a synchronous
                # write regression without a latency threshold assertion.
                self.assertEqual(threading.get_ident(), main_thread)
                usb_polled.set()
                release.set()
            packets = list(probe.packets)
            probe.packets.clear()
            while rx:
                data = rx.popleft()
                for offset in range(0, len(data), 12):
                    part = data[offset:offset+12]
                    probe.sequence += 1
                    probe.rx_bytes += len(part)
                    packet = bytearray(hardware.packet(19, a=len(part), token=probe.sequence))
                    packet[4:4+len(part)] = part
                    packets.append((probe.session, bytes(packet)))
            packets.append((probe.session, probe.status()))
            return packets
        with patch.object(stress, 'poll', poll):
            if outcome == 'full':
                result = stress.run_load(probe, peer, 115200, 'lossless', .01, 'burst', checkpoints.append)
                self.assertEqual(result['status'], 'PASS')
                self.assertEqual(result['input_bytes'], 300)
                self.assertEqual(result['input_sha256'], result['output_sha256'])
            else:
                with self.assertRaises(OSError):
                    stress.run_load(probe, peer, 115200, 'lossless', .01, 'burst', checkpoints.append)
                self.assertEqual(checkpoints[-1]['input_bytes'], 7 if outcome == 'short' else 0)
                failure = checkpoints[-1]['fixture_write_failure']
                self.assertEqual(failure['accepted_bytes_unknown'], outcome == 'error')
        self.assertTrue(usb_polled.is_set())
        self.assertTrue(finished.is_set())
        self.assertEqual(peer.writes, 1)
        self.assertNotEqual(peer.writer_thread, main_thread)
        self.assertFalse(any(thread.name.startswith('uart-fixture') for thread in threading.enumerate()))

    def test_blocked_burst_keeps_polling_usb_without_replaying(self):
        self.run_fixture()

    def test_burst_short_write_and_exception_are_accounted_before_return(self):
        for outcome in ('short', 'error'):
            with self.subTest(outcome=outcome):
                self.run_fixture(outcome)


class DedicatedWriterTests(unittest.TestCase):
    def test_no_retry_and_exact_known_prefix_or_unknown_exception(self):
        for outcome in ('full', 'short', 'error'):
            with self.subTest(outcome=outcome):
                class Peer:
                    calls = 0
                    sent = b''
                    def write(self, data):
                        self.calls += 1
                        writer.stop.set()
                        if outcome == 'error':
                            raise OSError('write result unknown')
                        n = len(data) if outcome == 'full' else 7
                        self.sent = data[:n]
                        return n
                peer = Peer()
                writer = stress.FixtureWriter(peer, 1, 0, 128)
                writer.start()
                if outcome == 'full':
                    writer.join()
                else:
                    with self.assertRaises(OSError):writer.join()
                self.assertEqual(peer.calls, 1)
                self.assertEqual(bytes(writer.expected), peer.sent)
                self.assertEqual(writer.unknown_accepted_bytes, outcome == 'error')
                self.assertFalse(writer.thread.is_alive())

    def test_continuous_writer_does_not_block_usb_polling(self):
        started, release = threading.Event(), threading.Event()
        class Peer:
            def read(self, _):return b''
            def write(self, data):
                started.set()
                if not release.wait(2):raise RuntimeError('USB not polled')
                writer.stop.set()
                return len(data)
        peer = Peer()
        writer = stress.FixtureWriter(peer, 1, 0, 128).start()
        self.assertTrue(started.wait(1))
        release.set()
        writer.join()
        self.assertEqual(len(writer.expected), 128)


if __name__ == '__main__':
    unittest.main()
