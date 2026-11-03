"""Deterministic offered-rate and slow-UI tests of the real Channel batch path."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from can_scope.engine import Channel
from can_scope.protocol import packet


def uart(seq,data=bytes(range(12))):
    p=bytearray(packet(19,a=len(data),token=seq));p[4:4+len(data)]=data
    return bytes(p)


def content(payload):
    return b''.join(bytes.fromhex(row['uart']) if 'uart' in row else bytes.fromhex(row['hex'])[4:4+bytes.fromhex(row['hex'])[2]]
                    for row in payload['items'] if 'uart' in row or bytes.fromhex(row['hex'])[1]==19)


class RxBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events=[];self.c=Channel(lambda *x:self.events.append(x));self.c.ready=True
    async def asyncTearDown(self):await self.c.close()
    async def test_5000_and_8000_packets_per_second(self):
        for rate in (5000,8000):
            self.events.clear();expected=bytearray();actual=bytearray()
            with patch('can_scope.engine.time.monotonic',return_value=0) as now:
                seq=0
                for tick in range(40):
                    now.return_value=tick*.025
                    for _ in range(rate//40):
                        seq+=1;p=uart(seq);self.c.receive(p);expected.extend(p[4:16])
                    self.c.flush_once()
                    self.assertLessEqual(len(self.c.deliveries),2)
                    for _,payload in self.events:
                        actual.extend(content(payload));self.c.submit('delivered',[payload['delivery']])
                    self.events.clear()
            self.assertEqual(actual,expected)
            self.assertFalse(self.c.packets)
            self.assertEqual(self.c.dropped,0)
    async def test_slow_receipts_bounded_queue_and_control_priority(self):
        for rate in (5000,8000):
            c=self.c;self.events.clear();await c.disconnect();c.ready=True
            pending=[];seq=0;maximum=0;control_count=0
            with patch('can_scope.engine.time.monotonic',return_value=0) as now:
                for tick in range(80):
                    now.return_value=tick*.025
                    for due,token in list(pending):
                        if due<=tick:c.submit('delivered',[token]);pending.remove((due,token))
                    for _ in range(rate//40):seq+=1;c.receive(uart(seq))
                    c.receive(packet(21,a=1,x=115200))
                    c.flush_once()
                    for kind,payload in self.events:
                        if kind not in ('packets','motorSamples'):continue
                        is_control=any('hex' in row and bytes.fromhex(row['hex'])[1]==21 for row in payload['items'])
                        control_count+=is_control
                        # Data consumer stalls 150ms; control receipt stays 25ms.
                        pending.append((tick+(1 if is_control else 6),payload['delivery']))
                    self.events.clear()
                    maximum=max(maximum,len(c.packets))
                    self.assertLessEqual(len(c.deliveries),2)
                    self.assertLessEqual(len(c.controls),64)
                    self.assertLessEqual(len(c.packets),512)
            self.assertEqual(maximum,512)
            self.assertGreater(c.dropped,0)
            self.assertEqual(control_count,80)
    async def test_contiguous_runs_gaps_wrap_and_mixed_order(self):
        for p in [uart(0xffffffff,b'\xe4'),uart(0,b'\xb8'),uart(2,b'\xad'),packet(1),uart(3,b'abc')]:self.c.receive(p)
        self.c.flush_once();rows=self.events[-1][1]['items']
        self.assertEqual([(r['first'],r['last'],r['count']) for r in rows if 'uart' in r],[(0xffffffff,0,2),(2,2,1),(3,3,1)])
        self.assertEqual(content(self.events[-1][1]),b'\xe4\xb8\xadabc')
        self.assertEqual(bytes.fromhex(rows[2]['hex'])[1],1)
    async def test_byte_and_time_budget(self):
        for seq in range(100):self.c.receive(uart(seq))
        with patch('can_scope.engine.RX_BATCH_BYTES',80):self.c.flush_once()
        self.assertEqual(len(self.c.packets),96)
        self.c.submit('delivered',[self.events[-1][1]['delivery']])
        with patch('can_scope.engine.time.monotonic',side_effect=[n*.001 for n in range(50)]):self.c.flush_once()
        self.assertEqual(len(self.c.packets),93)
    async def test_stale_delivery_cannot_release_new_session_gate(self):
        self.c.receive(uart(1));self.c.flush_once();old=self.events[-1][1]['delivery']
        await self.c.disconnect();self.c.ready=True
        self.c.receive(uart(2));self.c.flush_once();new=self.events[-1][1]['delivery']
        self.c.submit('delivered',[old]);self.assertTrue(self.c.ui_pending)
        self.c.submit('delivered',[new]);self.assertFalse(self.c.ui_pending)
    async def test_control_batch_can_pass_pending_data(self):
        self.c.receive(uart(1));self.c.flush_once();first=self.events[-1][1]
        self.c.receive(packet(21,a=1,x=115200));self.c.flush_once()
        second=self.events[-1][1]
        self.assertNotEqual(first['delivery'],second['delivery'])
        self.assertTrue(self.c.ui_pending);self.assertTrue(self.c.control_pending)
        self.c.submit('delivered',[second['delivery']]);self.assertTrue(self.c.ui_pending)
        self.assertFalse(self.c.control_pending)
