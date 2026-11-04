"""Offline tests for numeric Linux TCP observation and the route gate."""
import importlib.util
from pathlib import Path
import socket
import struct
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('wifi_tls_check', Path(__file__).parents[1]/'scripts/wifi-tls-check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Numbers(unittest.TestCase):
    def test_tcp_prefix_and_optional_fields(self):
        class Sock:
            def getsockopt(self, level, option, size):
                self_outer.assertEqual((level, option, size), (socket.IPPROTO_TCP, socket.TCP_INFO, 232))
                return packet
            def fileno(self):
                return 7
        self_outer = self
        def queued(fd, op, value, mutate):
            self.assertEqual((fd, op, mutate), (7, 0x5411, True))
            value[0] = 163
        for length in (104, 232):
            packet = bytearray(length)
            packet[0] = 1
            struct.pack_into('=I', packet, 68, 57000)
            struct.pack_into('=I', packet, 100, 2)
            if length == 232:
                struct.pack_into('=Q', packet, 120, 1)
                struct.pack_into('=Q', packet, 208, 326)
            with patch.object(check.fcntl, 'ioctl', queued):
                result = check.tcp_numbers(Sock())
            self.assertEqual((result['state'], result['rtt_us'], result['total_retrans'], result['send_queue_bytes']), (1, 57000, 2, 163))
            self.assertEqual('bytes_acked' in result, length == 232)
            if length == 232:
                self.assertEqual(result['bytes_retrans'], 326)

    def test_route_guard(self):
        good = '[{"dst":"192.168.1.221","dev":"enp3s0","prefsrc":"192.168.1.131"}]'
        with patch.object(check.subprocess, 'check_output', return_value=good):
            self.assertEqual(check.route_check('192.168.1.221', 'enp3s0', '192.168.1.131')['dev'], 'enp3s0')
            with self.assertRaises(ValueError):
                check.route_check('192.168.1.221', 'wlan0', '192.168.1.131')

    def test_phase_failure_has_no_exception_text(self):
        with patch.object(check.time, 'monotonic_ns', side_effect=(0, 1000000, 4000000)):
            trace = check.Trace()
            trace.start('tls_handshake')
            trace.end('TimeoutError')
        self.assertEqual(trace.rows, [dict(name='tls_handshake', start_ms=1.0, end_ms=4.0, duration_ms=3.0, ok=False, error_type='TimeoutError')])


if __name__ == '__main__':
    unittest.main()
