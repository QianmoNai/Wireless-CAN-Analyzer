"""UART pipeline checks use device completion ACKs, never transport-write ACKs."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from can_scope.engine import Channel
from can_scope.protocol import packet,u32


class Transport:
    def __init__(self,c):self.c=c;self.writes=[];self.auto=True;self.ack_gate=None
    async def write(self,p):
        self.writes.append(p)
        if p[1]==24 and self.auto:
            await asyncio.sleep(0)
            self.reply(p)
    def reply(self,p,**kw):
        ack=bytearray(p);ack[1]=20;ack[3]=ack[2];ack[2]=kw.get('status',0)
        if 'length' in kw:ack[3]=kw['length']
        if kw.get('corrupt'):ack[4]^=1
        self.c.receive(ack)
    async def close(self):pass


class UartPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.c=Channel(lambda *_:None);self.c.ready=self.c.uart=True
        self.t=self.c.transport=Transport(self.c)
    async def asyncTearDown(self):await self.c.close()
    async def begin(self,data=b'x'*25,id=1):
        await self.c.handle('uartSend',[id,data.hex()]);await asyncio.sleep(.001)
        return self.c.uart_task
    async def test_4096_exact_without_ui_receipt(self):
        data=bytes(range(256))*16
        task=await self.begin(data);await task
        self.assertEqual(b''.join(p[4:4+p[2]] for p in self.t.writes),data)
        self.assertEqual(len(self.t.writes),342)
        self.assertEqual(self.c.uart_progress['confirmed'],4096)
        self.assertTrue(self.c.uart_progress['done'])
        self.assertEqual(len({u32(p,16) for p in self.t.writes}),342)
    async def test_single_inflight_cancel_and_late_ack(self):
        self.t.auto=False;task=await self.begin()
        self.assertEqual(len(self.t.writes),1)
        self.c.submit('uartCancel',[1]);await asyncio.sleep(.001)
        self.assertEqual(len(self.t.writes),1)
        with self.assertRaises(ValueError):await self.c.handle('uartSend',[2,'01'])
        self.t.reply(self.t.writes[0]);await task
        self.assertEqual(len(self.t.writes),1)
        self.assertTrue(self.c.uart_progress['done'])
        self.t.reply(self.t.writes[0])
        task=await self.begin(b'ab',2)
        self.t.reply(self.t.writes[0]);await asyncio.sleep(.001)
        self.assertFalse(task.done())
        self.t.reply(self.t.writes[-1]);await task
        self.assertEqual(self.c.uart_progress['confirmed'],2)
    async def test_bad_length_status_or_echo_stops_without_retry(self):
        for kw in ({'length':1},{'status':2},{'corrupt':True}):
            self.t.auto=False;task=await self.begin();p=self.t.writes[-1]
            self.t.reply(p,**kw);await task
            self.assertEqual(self.c.uart_progress['confirmed'],0)
            self.assertTrue(self.c.uart_progress['done'])
        self.assertEqual(len(self.t.writes),3)
    async def test_timeout_disconnect_and_mode_change_no_replay(self):
        self.t.auto=False
        with patch('can_scope.engine.UART_ACK_TIMEOUT',.01):
            task=await self.begin();await task
        self.assertEqual(len(self.t.writes),1)
        task=await self.begin(id=2)
        await self.c.disconnect();await asyncio.gather(task,return_exceptions=True)
        self.t.reply(self.t.writes[-1]);await asyncio.sleep(.01)
        self.assertEqual(len(self.t.writes),2)
        self.c.ready=self.c.uart=True;self.c.transport=self.t
        task=await self.begin(id=3)
        self.c.receive(packet(21,a=0));self.t.reply(self.t.writes[-1]);await task
        self.assertEqual(len(self.t.writes),3)
    async def test_control_owner_has_priority_between_chunks(self):
        self.t.auto=False;self.c.start();task=await self.begin()
        self.c.submit('send',[(bytes([1,0x13,1,0,65])+bytes(15)).hex()])
        self.t.reply(self.t.writes[0]);await asyncio.sleep(.01)
        self.assertEqual(self.t.writes[1][1],0x13)
        self.assertEqual(self.t.writes[2][1],24)
        self.c.submit('uartCancel',[1]);self.t.reply(self.t.writes[-1]);await task
    async def test_cancel_before_owner_starts_and_write_failure(self):
        self.c.submit('uartCancel',[7])
        await self.c.handle('uartSend',[7,'00'])
        self.assertFalse(self.t.writes)
        self.assertTrue(self.c.uart_progress['done'])
        async def fail(p):
            self.t.writes.append(p)
            raise OSError('short write; unknown')
        self.t.write=fail
        task=await self.begin();await task
        self.assertEqual(len(self.t.writes),1)
        self.assertEqual(self.c.uart_progress['confirmed'],0)
        self.assertIn('unknown',self.c.uart_progress['message'])
    async def test_validation(self):
        for args in ([1,''],[1,'00'*4097],[1,'zz'],[True,'00']):
            with self.assertRaises(ValueError):await self.c.handle('uartSend',args)
        self.assertFalse(self.t.writes)
