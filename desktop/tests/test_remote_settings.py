"""Desktop allowlist must accept exactly the firmware remote-settings layout."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.protocol import setting_command


def command(op=0, mask=0, network=0, data=0, baud=0, token=1):
    return struct.pack('<BBBBBBHIII', 1, 0x19, op, mask, network, data, 0, baud, 0, token)


class RemoteSettingsTests(unittest.TestCase):
    def test_query_and_all_masks(self):
        samples = [command()]
        samples += [command(1, mask, 4 if mask & 1 else 0,
                            1 if mask & 2 else 0, 115200 if mask & 4 else 0)
                    for mask in range(1, 8)]
        samples += [command(1, 4, baud=baud) for baud in
                    (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)]
        for p in samples:
            self.assertEqual(setting_command(p.hex()), p)

    def test_reject_invalid_and_nonzero_unused_fields(self):
        samples = [command(token=0), command(2), command(1), command(1, 8),
                   command(1, 1, network=5), command(1, 2, data=2),
                   command(1, 4, baud=123456), command(network=4),
                   command(1, 2, network=4), command(1, 1, data=1),
                   command(1, 1, baud=115200)]
        for index in (6, 7, 12, 13, 14, 15):
            p = bytearray(command());p[index] = 1;samples.append(bytes(p))
        for p in samples:
            with self.subTest(packet=p.hex()), self.assertRaises(ValueError):
                setting_command(p.hex())


if __name__ == '__main__':
    unittest.main()
