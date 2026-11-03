"""Small offline parser/fallback test; never scans or opens a network device."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('wifi_txtest_scan', root/'scripts/wifi-txtest-scan.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class ScanTests(unittest.TestCase):
    def test_outputs_only_public_test_rows(self):
        iw = ('BSS 90:da:72:73:72:2d(on wlan0)\n\tfreq: 2412\n\tsignal: -62.50 dBm\n'
              '\tSSID: CANScope-TX-722D\n'
              'BSS 00:11:22:33:44:55(on wlan0)\n\tSSID: NEIGHBOR_NOT_PRINTED\n')
        row = scan.parse_iw(iw)[0]
        self.assertEqual(row, dict(ssid='CANScope-TX-722D', bssid='90:da:72:73:72:2d', channel=1, signal='-62.50 dBm'))
        nm = 'CANScope-TX-722D:90:DA:72:73:72:2D:1:78\nNEIGHBOR_NOT_PRINTED:00:11:22:33:44:55:1:99\n'
        self.assertEqual(scan.parse_nmcli(nm)[0]['signal'], '78%')
        self.assertEqual(scan.parse_nmcli(nm.replace(':1:78', ':1:999')), [])
        iwd = '  > CANScope-TX-722D     open   ****\n    NEIGHBOR_NOT_PRINTED psk ****\n'
        self.assertEqual(scan.parse_iwctl(iwd)[0]['ssid'], row['ssid'])
        self.assertIsNone(scan.parse_iwctl(iwd)[0]['bssid'])
        self.assertNotIn('NEIGHBOR_NOT_PRINTED', repr(scan.parse_iw(iw)+scan.parse_nmcli(nm)+scan.parse_iwctl(iwd)))

    def test_permission_failure_falls_back_without_connecting(self):
        nm = 'CANScope-TX-722D:90:DA:72:73:72:2D:1:78\n'
        with patch.object(scan.shutil, 'which', return_value='/synthetic/tool'), \
             patch.object(scan, 'capture', side_effect=[None, nm]) as command:
            self.assertEqual(scan.scan_round('wlan0')[0]['channel'], 1)
        self.assertEqual(command.call_args_list[0].args[0], ['iw','dev','wlan0','scan'])
        self.assertIn('--rescan', command.call_args_list[1].args[0])
        self.assertFalse(any('connect' in c.args[0] for c in command.call_args_list))


if __name__ == '__main__':
    unittest.main()
