import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.usb_protocol import Decoder, encode, HELLO, PING
from can_scope.usb_transport import UsbTransport, prepare_usb, scan_usb
from can_scope.protocol import packet, u32
from can_scope.engine import Channel


class FakeSerial:
    def __init__(self, **kwargs):
        self.is_open = False
        self.input = bytearray()
        self.writes = []
        self.decoder = Decoder()
        self.short = False
        self.answer = True
        self.drop_hellos = 0
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
                if self.drop_hellos:
                    self.drop_hellos -= 1
                    self.input.extend(b'ESP-ROM:esp32s3-20210327\r\n')
                else:
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

    def test_batched_frames_arbitrary_chunks_and_corrupt_magic(self):
        expected = [(i+1, packet(1, x=i, token=i)) for i in range(128)]
        stream = b'noiseCUSB' + bytes(28) + b''.join(encode(s, p) for s, p in expected)
        for chunk in (1, 7, 31, 32, 63, 4096, len(stream)):
            decoder, received = Decoder(), []
            for start in range(0, len(stream), chunk):
                received.extend(decoder.feed(stream[start:start+chunk]))
                self.assertLess(len(decoder.buffer), 32)
            self.assertEqual(received, expected)
        decoder = Decoder()
        self.assertEqual(decoder.feed(b'x'*100000+b'CUS'), [])
        self.assertEqual(decoder.feed(encode(1, HELLO)[3:]), [(1, HELLO)])


class UsbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received=[];self.errors=[]
        self.patch=patch('can_scope.serial_port.NativeUsbSerial',FakeSerial);self.patch.start()
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
    async def test_startup_loses_first_hello(self):
        port = FakeSerial()
        port.drop_hellos = 1
        with patch('can_scope.serial_port.NativeUsbSerial', return_value=port), patch('can_scope.usb_transport.HANDSHAKE_INTERVAL', .02):
            await self.link.connect('TEST')
        frames = [Decoder().feed(frame)[0] for frame in port.writes]
        self.assertEqual([p for _,p in frames], [HELLO, HELLO])
        self.assertEqual({s for s,_ in frames}, {self.link.session})
        self.assertFalse(self.link.ready.cancelled())
        self.assertFalse(self.errors)
    async def test_handshake_timeout_closes_port_and_stops_retry(self):
        port = FakeSerial()
        port.answer = False
        with patch('can_scope.serial_port.NativeUsbSerial', return_value=port), patch('can_scope.usb_transport.HANDSHAKE_INTERVAL', .02), patch('can_scope.usb_transport.HANDSHAKE_TIMEOUT', .08):
            with self.assertRaisesRegex(ConnectionError, 'USB 握手超时'):
                await self.link.connect('TEST')
        count = len(port.writes)
        self.assertGreaterEqual(count, 2)
        self.assertFalse(port.is_open)
        self.assertTrue(self.link.ready.cancelled())
        self.assertTrue(all(t.done() for t in self.link.tasks))
        await asyncio.sleep(.03)
        self.assertEqual(len(port.writes), count)
        self.assertTrue(all(Decoder().feed(f)[0][1] == HELLO for f in port.writes))
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


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_bootstrap_persists_once_and_releases_port(self):
        class SettingsSerial(FakeSerial):
            network = 0
            data = 0
            baud = 115200
            def write(self, frame):
                self.writes.append(frame)
                for session, p in self.decoder.feed(frame):
                    if p[1] != 25:
                        raise AssertionError('bootstrap must not establish a session')
                    if p[2]:
                        self.network = p[4]
                        self.data = p[5] if p[3]&2 else self.data
                        self.baud = u32(p,8) if p[3]&4 else self.baud
                    ack = packet(22,a=int(bool(p[2])),b=self.network,x=self.data,y=self.baud,token=u32(p,16))
                    self.input.extend(encode(session,ack))
                return len(frame)
        port = SettingsSerial()
        with patch('can_scope.serial_port.NativeUsbSerial',return_value=port):
            result = await prepare_usb('TEST',data=1,baud=230400)
        self.assertEqual((result['network'],result['data'],result['baud']), (4,1,230400))
        self.assertFalse(port.is_open)
        commands = [Decoder().feed(frame)[0][1] for frame in port.writes]
        self.assertEqual([p[2] for p in commands],[0,1])
        self.assertEqual(commands[1][3],7)

    async def test_invalid_bootstrap_never_opens_port(self):
        with patch('can_scope.serial_port.NativeUsbSerial') as serial:
            with self.assertRaises(ValueError):await prepare_usb('TEST',baud=123)
            serial.assert_not_called()

    async def test_bootstrap_does_not_replay_unconfirmed_setting(self):
        clock = 0.0
        class NoSetReply(FakeSerial):
            def write(self, frame):
                nonlocal clock
                self.writes.append(frame)
                for session, p in self.decoder.feed(frame):
                    if p[2] == 0:
                        self.input.extend(encode(session,packet(22,b=4,y=115200,token=u32(p,16))))
                    else:
                        # Expire only after the setting was submitted. Thread
                        # pool startup can exceed a short wall-clock timeout
                        # on Windows before the initial read has completed.
                        clock = 2.0
                return len(frame)
        port=NoSetReply()
        with patch('can_scope.serial_port.NativeUsbSerial',return_value=port), \
             patch('can_scope.usb_transport.time', SimpleNamespace(monotonic=lambda: clock)):
            with self.assertRaisesRegex(ConnectionError,'设置结果可能未知'):
                await prepare_usb('TEST',data=1,timeout=1)
        commands=[Decoder().feed(frame)[0][1] for frame in port.writes]
        self.assertEqual([p[2] for p in commands],[0,1])
        self.assertFalse(port.is_open)

    async def test_bootstrap_does_not_submit_setting_after_query_budget_expires(self):
        clock = 0.0
        class SlowQuery(FakeSerial):
            def write(self, frame):
                nonlocal clock
                self.writes.append(frame)
                for session, p in self.decoder.feed(frame):
                    if p[2] != 0:
                        raise AssertionError('Expired bootstrap submitted a setting')
                    self.input.extend(encode(session,packet(22,b=4,y=115200,token=u32(p,16))))
                    clock = 2.0
                return len(frame)
        port = SlowQuery()
        with patch('can_scope.serial_port.NativeUsbSerial',return_value=port), \
             patch('can_scope.usb_transport.time', SimpleNamespace(monotonic=lambda: clock)):
            with self.assertRaises(ConnectionError):
                await prepare_usb('TEST',data=1,timeout=1)
        commands = [Decoder().feed(frame)[0][1] for frame in port.writes]
        self.assertEqual([p[2] for p in commands], [0])
        self.assertFalse(port.is_open)

    async def test_bootstrap_surfaces_admission_rejection(self):
        class Rejected(FakeSerial):
            def write(self, frame):
                for session, p in self.decoder.feed(frame):
                    self.input.extend(encode(session,packet(18,a=3,b=25,x=u32(p,16))))
                return len(frame)
        port=Rejected()
        with patch('can_scope.serial_port.NativeUsbSerial',return_value=port):
            with self.assertRaisesRegex(ValueError,'状态码 3'):
                await prepare_usb('TEST')
        self.assertFalse(port.is_open)

    async def test_bootstrap_retries_only_query_during_device_startup(self):
        class Starting(FakeSerial):
            queries = 0
            def write(self, frame):
                self.writes.append(frame)
                for session,p in self.decoder.feed(frame):
                    self.queries += 1
                    if self.queries == 1:
                        reply=packet(18,a=0x11,b=25,x=u32(p,16))
                    else:
                        reply=packet(22,b=4,y=115200,token=u32(p,16))
                    self.input.extend(encode(session,reply))
                return len(frame)
        port=Starting()
        with patch('can_scope.serial_port.NativeUsbSerial',return_value=port):
            state=await prepare_usb('TEST',timeout=.8)
        self.assertEqual(state['network'],4)
        self.assertEqual(port.queries,2)
        self.assertTrue(all(Decoder().feed(f)[0][1][2] == 0 for f in port.writes))
        self.assertFalse(port.is_open)


class UsbDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_filters_system_ports_prioritizes_native_and_never_opens(self):
        from types import SimpleNamespace
        ports = [
            SimpleNamespace(device='/dev/ttyS0', description='n/a', vid=None, pid=None),
            SimpleNamespace(device='/dev/ttyACM1', description='Horco CMSIS-DAP', vid=0xfaed, pid=0x4870),
            SimpleNamespace(device='/dev/ttyUSB0', description='USB Serial', vid=0x10c4, pid=0xea60),
            SimpleNamespace(device='/dev/ttyACM2', description='ESP32 USB', vid=0x303a, pid=0x1001),
        ]
        with patch('serial.tools.list_ports.comports', return_value=ports), patch('can_scope.usb_transport.open_port', side_effect=AssertionError('Discovery opened a port')), patch('serial.Serial', side_effect=AssertionError('Discovery opened a serial device')):
            rows = []
            await scan_usb(rows.append)
            self.assertEqual([r['address'] for r in rows], ['usb:/dev/ttyACM2', 'usb:/dev/ttyACM1', 'usb:/dev/ttyUSB0'])
            self.assertIn('待验证', rows[0]['name'])
            rows = []
            await scan_usb(rows.append, include_all=True)
            self.assertEqual(rows[-1]['address'], 'usb:/dev/ttyS0')
            self.assertEqual(len(rows), 4)
