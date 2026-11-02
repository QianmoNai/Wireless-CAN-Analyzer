import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.protocol import SERVICE
from can_scope.transports import scan_ble


class ScanTests(unittest.IsolatedAsyncioTestCase):
    async def check_platform(self, platform):
        options = []
        target = SimpleNamespace(address='TARGET', name='ESP32-CAN')
        other = SimpleNamespace(address='OTHER', name='Other')
        class Scanner:
            def __init__(self, detection_callback, **kwargs):
                options.append(kwargs)
                self.callback = detection_callback
            async def __aenter__(self):
                self.callback(other, SimpleNamespace(service_uuids=[], local_name='Other', rssi=-50))
                self.callback(target, SimpleNamespace(service_uuids=[SERVICE.upper()], local_name='ESP32-CAN', rssi=-60))
                return self
            async def __aexit__(self, *args):
                return False
        found = []
        with patch('bleak.BleakScanner', Scanner), patch('can_scope.transports.sys.platform', platform), patch('can_scope.transports.asyncio.sleep', new_callable=AsyncMock):
            devices = await scan_ble(found.append)
        self.assertEqual(devices, {'TARGET': target})
        self.assertEqual([d['address'] for d in found], ['TARGET'])
        return options[0]

    async def test_linux_filters_in_application(self):
        self.assertNotIn('service_uuids', await self.check_platform('linux'))

    async def test_windows_keeps_native_filter(self):
        self.assertEqual((await self.check_platform('win32'))['service_uuids'], [SERVICE])
