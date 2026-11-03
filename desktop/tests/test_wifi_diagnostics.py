"""Small offline test of the real numeric parser, counters and Channel gate."""
import asyncio
import json
import re
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from can_scope.engine import Channel
from can_scope.protocol import packet
from can_scope.wifi_diagnostics import FIELDS, Snapshot, NumericLog, WifiDiagnostics, query


def frame(index, value=0, at=1000, seq=42, token=123):
    return struct.pack('<BBBBIIII',1,26,index,len(FIELDS),value,at,seq,token)


class WifiDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_order_matches_firmware(self):
        header=(Path(__file__).resolve().parents[2]/'firmware/main/wifi_uart_diag.h').read_text()
        names=re.findall(r'\bWD_[A-Z_]+\b',header.split('enum {',1)[1].split('WD_FIELDS',1)[0])
        self.assertEqual(FIELDS,[name[3:].lower() for name in names])

    async def test_snapshot_strict_sequence_unknown_signed_and_stale_reply(self):
        s=Snapshot(123)
        s.receive(frame(255,1,444,8,token=999));self.assertIsNone(s.header)
        s.receive(frame(255,1,444,8))
        for i,name in enumerate(FIELDS):
            value=0xffffffff if name in ('ring_lost_bytes','wifi_rate_kbps','wifi_retries','tcp_kernel_send_high_bytes') else 4
            if name=='wifi_rssi':value=(-59)&0xffffffff
            s.receive(frame(i,value))
        result=await s.future
        self.assertEqual(result['boot_id'],444);self.assertEqual(result['serial'],8)
        self.assertEqual(result['fields']['wifi_rssi'],dict(value=-59,available=True,at_ms=1000,uart_sequence=42))
        self.assertIsNone(result['fields']['ring_lost_bytes']['value'])
        invalid=Snapshot(124)
        invalid.receive(frame(0,token=124))
        with self.assertRaises(ValueError):await invalid.future
        duplicate=Snapshot(125)
        duplicate.receive(frame(255,1,444,8,token=125));duplicate.receive(frame(0,token=125));duplicate.receive(frame(0,token=125))
        with self.assertRaises(ValueError):await duplicate.future

    async def test_query_is_read_only_and_channel_does_not_queue_diagnostic_payload(self):
        p=query(123);self.assertEqual(p[:4],bytes((1,0x19,2,0)));self.assertFalse(any(p[3:16]))
        class MemoryLog:
            def write(self,*args,**kw):pass
        c=Channel(lambda *args:None);c.mode='wifi';c.ready=True
        c.enable_wifi_diagnostics(MemoryLog())
        c.wifi_diagnostics.pending=Snapshot(123)
        c.receive(frame(255,1,444,8));self.assertEqual(len(c.controls),0)
        self.assertEqual(len(c.packets),0)
        c.wifi_diagnostics.pending.cancel()
        await c.close()

    async def test_per_second_loss_wrap_and_private_numeric_log(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'diag.jsonl';log=NumericLog(path)
            c=Channel(lambda *args:None);c.mode='wifi';c.ready=True
            diag=WifiDiagnostics(c,log)
            for seq in (0xffffffff,1,1,0):
                p=bytearray(packet(19,a=12,token=seq));p[4:16]=b'SECRET-DATA!'
                diag.receive(bytes(p))
            diag.tick()
            diag.disconnected();log.close()
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[0]['bytes'],48)
            self.assertEqual(rows[0]['sequence_gaps'],1)
            self.assertEqual(rows[0]['duplicates'],1)
            self.assertEqual(rows[0]['reordered'],1)
            self.assertNotIn('SECRET',path.read_text())
            self.assertNotIn('hex',path.read_text())
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            path.chmod(0o644)
            with self.assertRaises(ValueError):NumericLog(path)
            alias=Path(d)/'link';alias.symlink_to(path)
            with self.assertRaises(OSError):NumericLog(alias)

    async def test_snapshot_timeout_keeps_old_snapshot_and_does_not_retry_write(self):
        class MemoryLog:
            def __init__(self):self.rows=[]
            def write(self,*args,**kw):self.rows.append((args,kw))
        c=Channel(lambda *args:None);c.mode='wifi';c.ready=True
        log=MemoryLog();d=WifiDiagnostics(c,log);writes=[]
        async def write(p):
            writes.append(p)
            d.pending.receive(bytes((1,18,0x13,0))+p[16:20]+bytes(12))
        c.write=write
        await d.poll()
        self.assertEqual(len(writes),1);self.assertTrue(d.unsupported)
        self.assertEqual(log.rows[-1][0][0],'snapshot_error')

    async def test_one_poll_logs_only_complete_device_snapshot(self):
        class MemoryLog:
            def __init__(self):self.rows=[]
            def write(self,*args,**kw):self.rows.append((args,kw))
        c=Channel(lambda *args:None);c.mode='wifi';c.ready=True
        log=MemoryLog();d=WifiDiagnostics(c,log);writes=[]
        async def write(p):
            writes.append(p);token=int.from_bytes(p[16:],'little')
            d.receive(frame(255,1,444,8,token))
            for i in range(len(FIELDS)):d.receive(frame(i,1,1000,42,token))
        c.write=write
        await d.poll()
        self.assertEqual(len(writes),1)
        self.assertEqual(log.rows[0][0][0],'device_snapshot')
        self.assertEqual(d.latest['serial'],8)
        self.assertEqual(len(d.latest['fields']),len(FIELDS))


if __name__=='__main__':unittest.main()
