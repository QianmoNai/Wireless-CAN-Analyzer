"""Host fault injection for the opt-in BLE hardware tool (no radio evidence)."""
import asyncio
import importlib.util
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from ble_stress import Owner, ChannelReceiver, diagnostics, HEADER, FIELDS, split_prefix
from can_scope.protocol import packet, u32

spec = importlib.util.spec_from_file_location('ble_entry', ROOT/'scripts/test-ble-stress.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class StressTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_holds_lock_until_token_reply(self):
        owner = Owner(.2)
        active, maximum, writes = 0, 0, []
        class Transport:
            async def write(self, p):
                nonlocal active, maximum
                active += 1
                maximum = max(active, maximum)
                writes.append(p)
                async def reply():
                    nonlocal active
                    await asyncio.sleep(.01)
                    active -= 1
                    owner.receive(packet(22, token=u32(p, 16)))
                asyncio.create_task(reply())
        owner.transport = Transport()
        await asyncio.gather(owner.query(), owner.query(), owner.query())
        self.assertEqual((maximum, len(writes)), (1, 3))
        self.assertEqual(len({u32(p, 16) for p in writes}), 3)

    async def test_unknown_tx_timeout_and_late_reply_never_replays(self):
        owner = Owner(.01)
        writes = []
        class Transport:
            async def write(self, p):
                writes.append(p)
        owner.transport = Transport()
        with self.assertRaises(TimeoutError):
            await owner.send(b'unknown')
        self.assertEqual(owner.tx[0]['outcome'], 'unknown')
        owner.receive(packet(20, token=u32(writes[0], 16)))
        self.assertEqual(len(writes), 1)
        self.assertFalse(owner.pending)
        self.assertEqual(owner.tx[0]['outcome'], 'unknown')

    async def test_cancel_and_old_connection_callbacks(self):
        transports = []
        class Transport:
            def __init__(self, receive, lost):
                self.receive = receive
                transports.append(self)
            async def connect(self, device):
                pass
            async def write(self, p):
                pass
            async def close(self):
                pass
        with patch('ble_stress.BleTransport', Transport):
            owner = Owner()
            await owner.connect(None)
            task = asyncio.create_task(owner.send(b'cancel'))
            await asyncio.sleep(.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(owner.tx[0]['outcome'], 'unknown')
            await owner.close()
            await owner.connect(None)
            transports[0].receive(packet(21, x=115200))
            self.assertIsNone(owner.latest)
            self.assertEqual(owner.stale_callbacks, 1)

    async def test_restore_unknown_set_queries_without_replay(self):
        calls = []
        target = dict(network_mode=4, data_mode=1, baudrate=115200)
        async def usb(args, report, *settings):
            calls.append(settings)
            if settings:
                raise TimeoutError('SET outcome unknown')
            return target
        report = {}
        with patch.object(entry, 'usb', usb), patch.object(entry.asyncio, 'sleep', return_value=None):
            await entry.restore(None, report)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(bool(x) for x in calls), 1)
        self.assertTrue(report['restored'])

    async def test_cleanup_failures_do_not_skip_independent_restore(self):
        calls = []
        async def peer_close(peer):
            calls.append('peer')
            raise OSError('fixture close fault')
        class BrokenOwner:
            async def close(self):
                calls.append('ble')
                raise OSError('BLE close fault')
        async def restore(args, report):
            calls.append('restore')
            report['restored'] = True
        report = {}
        with patch.object(entry, 'close_peer', peer_close), patch.object(entry, 'restore', restore):
            errors = await entry.cleanup(None, report, BrokenOwner(), None)
        self.assertEqual(calls, ['peer', 'ble', 'restore'])
        self.assertEqual([e['step'] for e in errors], ['peer', 'ble'])
        self.assertTrue(report['restored'])

    def test_rom_prefix_and_strict_boundary(self):
        marker = b'BS\0\xffsync'
        self.assertEqual(split_prefix(b'ESP-ROM:old\r\n'+marker, marker), b'ESP-ROM:old\r\n')
        for stream in (b'none', marker+b'extra', marker+marker):
            with self.assertRaises(ValueError):
                split_prefix(stream, marker)

    def test_bounded_snapshot_and_units(self):
        words = [0]*(len(HEADER)+3*len(FIELDS))
        words[:2] = [1, len(words)]
        words[HEADER.index('interval')] = 24
        words[len(HEADER)+FIELDS.index('drop_reserve_bytes')] = 12
        value = struct.pack('<'+'I'*len(words), *words)
        d = diagnostics(value)
        self.assertEqual(d['interval_ms'], 30)
        self.assertEqual(d['raw']['drop_reserve_bytes'], 12)
        self.assertLessEqual(len(value), 512)
        with self.assertRaises(ValueError):
            diagnostics(value[:-1])

    def test_real_channel_rx_binary_batch_and_receipts(self):
        channel = ChannelReceiver()
        for seq, data in enumerate((b'\0\xffa', b'\xe4\xb8\xad'), 1):
            p = bytearray(packet(19, a=len(data), token=seq))
            p[4:4+len(data)] = data
            channel.receive(p)
        result = channel.finish()
        self.assertEqual(channel.data, b'\0\xffa\xe4\xb8\xad')
        self.assertEqual(result['dropped'], 0)
        self.assertFalse(channel.channel.ui_pending)


if __name__ == '__main__':
    unittest.main()
