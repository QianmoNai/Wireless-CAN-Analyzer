import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.usb_protocol import Decoder, encode, HELLO, PING
from can_scope.usb_transport import UsbTransport
from can_scope.engine import Channel


class FakeSerial:
    def __init__(self, **kwargs):
        self.is_open = False
        self.input = bytearray()
        self.writes = []
        self.decoder = Decoder()
        self.short = False
        self.answer = True
    def open(self): self.is_open = True
    def close(self): self.is_open = False
    def reset_input_buffer(self): self.input.clear()
    @property
    def in_waiting(self): return len(self.input)
    def read(self, n):
        value = bytes(self.input[:n]);del self.input[:n];return value
    def write(self, data):
        if self.short: return 3
        self.writes.append(data)
        for session, p in self.decoder.feed(data):
            if p == HELLO and self.answer:
                self.input.extend(b'noise'+encode(session,HELLO[:2]+b'\x01'+HELLO[3:]))
            elif p == PING and self.answer: self.input.extend(encode(session,p))
        return len(data)


class CodecTests(unittest.TestCase):
    def test_crc_and_stream_recovery(self):
        import zlib
        self.assertEqual(zlib.crc32(b'123456789'), 0xcbf43926)
        payload=bytes([1,16,0,8])+bytes(range(4,20))
        frame=encode(0x12345678,payload)
        corrupt=bytearray(frame);corrupt[11]^=1
        decoder=Decoder();out=[]
        for byte in b'x'*10000+corrupt+frame+frame:
            out.extend(decoder.feed(bytes([byte])))
            self.assertLess(len(decoder.buffer),32)
        self.assertEqual(out,[(0x12345678,payload)]*2)
    def test_packet_validation(self):
        for session,payload in [(0,HELLO),(1,b'bad')]:
            with self.assertRaises(ValueError):encode(session,payload)


class UsbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received=[];self.errors=[]
        self.patch=patch('serial.Serial',FakeSerial);self.patch.start()
        self.link=UsbTransport(self.received.append,self.errors.append)
    async def asyncTearDown(self):
        await self.link.close();self.patch.stop()
    async def test_handshake_and_nonce_filter(self):
        await self.link.connect('TEST')
        self.assertFalse(self.link.port.dtr);self.assertFalse(self.link.port.rts)
        p=bytes([1,19])+bytes(range(18))
        stale=(self.link.session%0xffffffff)+1
        self.link.port.input.extend(encode(stale,p)+encode(self.link.session,p))
        await asyncio.sleep(.03)
        self.assertEqual(self.received,[p])
        await self.link.write(bytes([1,0x19])+bytes(18))
        self.assertFalse(self.errors)
    async def test_short_write_never_replayed(self):
        await self.link.connect('TEST')
        self.link.port.short=True
        count=len(self.link.port.writes)
        with self.assertRaises(ConnectionError):await self.link.write(PING)
        self.assertEqual(len(self.link.port.writes),count)
        self.assertTrue(self.errors)
    async def test_close_cancels_and_closes_port(self):
        await self.link.connect('TEST')
        port=self.link.port
        await self.link.close()
        self.assertFalse(port.is_open)
        self.assertTrue(all(t.done() for t in self.link.tasks))
        self.assertEqual(Decoder().feed(port.writes[-1])[0][1][1],0x7d)
    async def test_engine_usb_keeps_wifi_and_ble_identity(self):
        saved=dict(mode='usb',address='BLE:OLD',usb_port='TEST',token='saved',ip='192.168.1.2')
        c=Channel(lambda *args:None,saved)
        try:
            await c.handle('reconnect',[])
            self.assertTrue(c.ready)
            self.assertEqual(c.mode,'usb')
            self.assertEqual(c.saved['token'],'saved')
            self.assertEqual(c.address,'BLE:OLD')
            for uart in (True,False):
                c.receive(bytes([1,21,int(uart)])+bytes(17))
                self.assertEqual(c.uart,uart)
        finally: await c.close()
