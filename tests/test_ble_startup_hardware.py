"""Offline fault injection for startup acceptance; never opens hardware."""
import asyncio
from contextlib import nullcontext, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ble_startup_entry',ROOT/'scripts/test-ble-startup-hardware.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class StartupTests(unittest.TestCase):
    def scenario(self, case):
        modes, periodic = [], []
        counters = dict(bridge_delivery_dropped=0,can_rx_queue_dropped=0,dual_dropped=0)
        identity = bytes.fromhex('001122334455')
        channel = None
        class Usb:
            def __init__(self, port):
                self.port = port
            def identity(self):
                return b'\xff'*6 if case == 'wrong-device' else identity
            def monitor_drops(self):
                return dict(counters)
            def close(self):
                pass
        class Fixture:
            image_sha256 = 'offline-fixture'
            def __init__(self, elf):
                if case == 'wrong-elf':
                    raise RuntimeError('Running fixture image differs')
            def health(self):
                return dict(can_bus_off=int(case == 'unhealthy'))
            def quiet(self, enabled):
                periodic.append(enabled)
        class Channel:
            def __init__(self, emit, saved):
                nonlocal channel
                channel = self
                self.emit, self.ready, self.polls, self.dropped, self.transport = emit,False,{},0,None
                self.capabilities = 0 if case == 'late-capabilities' else 8
            def start(self):
                pass
            def receive(self, packet):
                pass
            def enqueue(self, name, args):
                if name == 'connect':
                    self.ready = True
                    if case != 'missing-can':
                        self.receive(bytes([1,1,0,8])+struct.pack('<I',0x201)+bytes(12))
                elif name == 'setPolling':
                    if not self.capabilities & 8:
                        raise AssertionError('Query sent before capability report')
                    key,identifier,enabled,hz = args
                    if enabled:
                        self.polls[key] = (identifier,hz,0)
                        if case != 'missing-dm':
                            self.receive(bytes([1,1,0,8])+struct.pack('<I',17)+bytes([0x11])+bytes(11))
                    else:
                        self.polls.pop(key,None)
                        self.emit('queryState',dict(enabled=False,message='查询已关闭'))
            def submit(self, name, args):
                pass
            async def close(self):
                self.ready = False
        async def select(port, mode):
            modes.append(mode)
            if case == 'startup-drop' and mode == 'ble':
                counters['bridge_delivery_dropped'] += 1
            if case == 'cleanup-drop' and mode == 'usb' and counters['bridge_delivery_dropped'] == 0:
                counters['bridge_delivery_dropped'] += 1
        async def sleep(seconds):
            if case == 'app-drop' and seconds == 8:
                channel.dropped += 1
            if case == 'late-capabilities' and seconds == .02:
                channel.capabilities = 8
        async def to_thread(fn):
            return fn()
        def module(name, path):
            return SimpleNamespace(select=select) if name == 'startup_can_bench' else SimpleNamespace(Fixture=Fixture)
        notify, notification = entry.BleakClient.start_notify, entry.BleTransport.notification
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report.json'
            args = ['bench','--fixture','stm32h723-dual','--elf','unused.elf','--port','fake',
                    '--channel','1','--repeat','1','--output',str(output)]
            with patch.object(sys,'argv',args), patch.object(entry,'LocalUsb',Usb), \
                 patch.object(entry,'Channel',Channel), patch.object(entry,'module',module), \
                 patch.object(entry,'load_connection',return_value=dict(id=identity.hex(),address='fake')), \
                 patch.object(entry,'hardware_bench_lock',nullcontext), \
                 patch.object(entry,'asyncio',SimpleNamespace(run=asyncio.run,sleep=sleep,to_thread=to_thread)), \
                 redirect_stdout(io.StringIO()):
                result = entry.main()
            report = json.loads(output.read_text())
        self.assertIs(entry.BleakClient.start_notify,notify)
        self.assertIs(entry.BleTransport.notification,notification)
        return result,report,modes,periodic

    def test_identity_image_and_health_fail_before_mode_mutation(self):
        for case in ('wrong-device','wrong-elf','unhealthy'):
            with self.subTest(case=case):
                result,report,modes,periodic = self.scenario(case)
                self.assertEqual(result,1)
                self.assertFalse(report['ok'])
                self.assertEqual(modes,[])
                self.assertEqual(periodic,[])

    def test_functional_success_does_not_hide_startup_or_cleanup_drops(self):
        for case in ('healthy','startup-drop','cleanup-drop'):
            with self.subTest(case=case):
                result,report,modes,periodic = self.scenario(case)
                self.assertTrue(report['functional_completed'])
                self.assertTrue(report['restored'])
                self.assertEqual(report['ok'],case == 'healthy')
                self.assertEqual(result,0 if case == 'healthy' else 1)
                self.assertEqual(report['delta']['bridge_delivery_dropped'],int(case != 'healthy'))
                self.assertEqual(report['rounds'][0]['ok'],case == 'healthy')
                self.assertEqual(modes,['ble','usb','usb'])
                self.assertEqual(periodic,[1,1])

    def test_app_overflow_fails_and_preserves_counter_after_close(self):
        result,report,modes,_ = self.scenario('app-drop')
        self.assertEqual(result,1)
        self.assertFalse(report['ok'])
        self.assertTrue(report['restored'])
        self.assertEqual(report['rounds'][0]['app_dropped'],1)
        self.assertFalse(report['rounds'][0]['completed'])
        self.assertEqual(modes[-1],'usb')

    def test_connection_and_query_ack_cannot_replace_physical_feedback(self):
        for case in ('missing-can','missing-dm'):
            with self.subTest(case=case):
                result,report,modes,_ = self.scenario(case)
                self.assertEqual(result,1)
                self.assertFalse(report['ok'])
                self.assertTrue(report['restored'])
                self.assertFalse(report['rounds'][0]['completed'])
                self.assertEqual(modes[-1],'usb')

    def test_transport_ready_waits_for_delayed_query_capability(self):
        result,report,_,_ = self.scenario('late-capabilities')
        self.assertEqual(result,0)
        self.assertTrue(report['ok'])
        states = {s['stage']:s for s in report['rounds'][0]['stages']}
        self.assertEqual(states['raw-warmup']['capabilities'],0)
        self.assertEqual(states['query-confirmed']['capabilities'],8)

    def test_diagnostics_validate_version_length_and_type_ledger(self):
        base = len(entry.HEADER)+3*len(entry.FIELDS)+len(entry.UART_FIELDS)
        words = [3,base+24,*([0]*(base+22))]
        words[base+17] = 9
        result = entry.diagnostic_snapshot(struct.pack('<'+str(len(words))+'I',*words))
        self.assertEqual(result['drop_types']['17'],9)
        self.assertEqual(result['version'],3)
        words[0] = 99
        with self.assertRaises(ValueError):
            entry.diagnostic_snapshot(struct.pack('<'+str(len(words))+'I',*words))
        with self.assertRaises(ValueError):
            entry.diagnostic_snapshot(b'\0'*507)


if __name__ == '__main__':
    unittest.main()
