import errno
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.serial_port import NativeUsbSerial, open_native_port


@unittest.skipUnless(sys.platform == 'linux', 'Linux native USB modem control')
class NativeControlTests(unittest.TestCase):
    def test_both_lines_clear_together_and_requested_lines_still_work(self):
        import struct
        import termios
        port = NativeUsbSerial(port=None)
        port.fd = 91
        port.dtr = port.rts = False
        with patch('fcntl.ioctl') as ioctl:
            port._update_dtr_state()
            port._update_rts_state()
            for call in ioctl.call_args_list:
                self.assertEqual(call.args, (91, termios.TIOCMBIC,
                    struct.pack('I', termios.TIOCM_DTR | termios.TIOCM_RTS)))
            self.assertEqual(ioctl.call_count, 2)
        port.dtr = True
        with patch('fcntl.ioctl') as ioctl:
            port._update_dtr_state()
            self.assertEqual(ioctl.call_args.args, (91, termios.TIOCMBIS,
                struct.pack('I', termios.TIOCM_DTR)))

    def test_real_posix_open_pty_roundtrip_and_reopen(self):
        # PTYs reject modem ioctls (ENOTTY); pyserial's existing fallback must
        # continue to support them and release the exclusive lock on close.
        master, slave = os.openpty()
        try:
            for _ in range(2):
                with open_native_port(os.ttyname(slave), timeout=.2) as port:
                    self.assertFalse(port.dtr)
                    self.assertFalse(port.rts)
                    os.write(master, b'can')
                    self.assertEqual(port.read(3), b'can')
                    port.write(b'usb')
                    self.assertEqual(os.read(master, 3), b'usb')
        finally:
            os.close(master)
            os.close(slave)

    def test_unexpected_modem_error_closes_descriptor(self):
        master, slave = os.openpty()
        try:
            with patch('can_scope.serial_port._clear_modem_lines',
                       side_effect=OSError(errno.EIO, 'fixture failure')):
                with self.assertRaises(OSError):
                    open_native_port(os.ttyname(slave))
            with open_native_port(os.ttyname(slave)) as port:
                self.assertTrue(port.is_open)
        finally:
            os.close(master)
            os.close(slave)
