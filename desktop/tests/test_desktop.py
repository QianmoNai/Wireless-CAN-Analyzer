import asyncio
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.protocol import can_command, packet, u32, setting_command, wifi_commands, ipv4, device_settings_command, device_settings_status
from can_scope.engine import Channel
from can_scope.transports import LanTransport, BleTransport


class ProtocolTests(unittest.TestCase):
    def test_remote_device_settings_validation(self):
        p = device_settings_command(network=4, data=1, baud=115200, token=42)
        self.assertEqual(p, bytes((1, 25, 1, 7, 4, 1)) + bytes(2) + (115200).to_bytes(4, 'little') + bytes(4) + (42).to_bytes(4, 'little'))
        self.assertEqual(device_settings_command(token=9), packet(25, token=9))
        for values in ({'network':5}, {'data':2}, {'baud':12345}, {'token':0}, {'data':True}, {'token':2**32}):
            with self.assertRaises(ValueError):device_settings_command(**values)
        status = device_settings_status(packet(22, a=1, b=4, x=1, y=115200, token=42))
        self.assertEqual((status['network'], status['data'], status['baud']), (4, 1, 115200))
        with self.assertRaises(ValueError):device_settings_status(packet(21))
        for offset in (2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16):
            invalid = bytearray(packet(22, b=4, y=115200, token=1))
            invalid[offset] = 9 if offset != 16 else 0
            with self.assertRaises(ValueError):device_settings_status(bytes(invalid))

    def test_classic_can(self):
        for extended, identifier in ((False, 0x7ff), (True, 0x1fffffff)):
            p = can_command(identifier, bytes(range(8)), extended, token=42)
            self.assertEqual((len(p), p[:4], u32(p,4), p[8:16], u32(p,16)), (20, bytes([1,16,int(extended),8]), identifier, bytes(range(8)),42))
        self.assertEqual(can_command(1, b'', remote=True, dlc=8)[8:16], bytes(8))
        for args in [(0x800,b'',False), (-1,b''), (1,bytes(9)), (1,b'x',False,True)]:
            with self.assertRaises(ValueError):
                can_command(*args)

    def test_allowlist(self):
        self.assertEqual(setting_command(packet(0x12,x=500000,token=2).hex())[1],18)
        for p in [packet(0x10), packet(0x20), packet(0x12,x=123), packet(0x12,x=500000,y=1)]:
            with self.assertRaises(ValueError):
                setting_command(p.hex())
        with self.assertRaises(ValueError):
            ipv4('127.0.0.999')

    def test_uart_command(self):
        p = bytes([1, 0x18, 3, 0, 0, 255, 65]) + bytes(9) + (7).to_bytes(4, 'little')
        self.assertEqual(setting_command(p.hex()), p)
        for offset, value in [(2, 0), (2, 13), (3, 1), (7, 1), (16, 0)]:
            bad = bytearray(p); bad[offset] = value
            with self.assertRaises(ValueError):
                setting_command(bad.hex())

    def test_wifi_fragmentation_and_validation(self):
        name = '中文网络'*2
        p = wifi_commands(5,name,'12345678',31)
        self.assertTrue(all(len(x)==20 for x in p))
        self.assertEqual(p[0][:4],bytes([3,31,len(name.encode()),8]))
        self.assertEqual(b''.join(x[4:4+x[3]] for x in p[1:-1]),name.encode()+b'12345678')
        self.assertEqual(p[-1][:2],bytes([5,31]))
        for name, password in [('', ''), ('x'*33,''),('abc','short'),('abc','密码')]:
            with self.assertRaises((ValueError,UnicodeError)):
                wifi_commands(5,name,password)


class FakeTransport:
    def __init__(self, channel):
        self.channel, self.writes = channel, []
        self.closed = False
        self.confirm = True
    async def write(self, p):
        self.writes.append(p)
        if not self.confirm:
            return
        if p[1] == 0x14:
            self.channel.receive(packet(8, a=0 if u32(p,8) else 1,x=u32(p,4),y=u32(p,8),token=u32(p,16)))
        elif p[1] == 0x17:
            self.channel.receive(packet(10,a=p[2],b=p[3],y=u32(p,8),token=u32(p,16)))
        elif p[1] == 0x19:
            self.channel.receive(packet(22,a=1 if p[2] else 0,b=4,x=1,y=115200,token=u32(p,16)))
    async def close(self):
        self.closed = True


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.channel = Channel(lambda *x:self.events.append(x))
        self.channel.transport = FakeTransport(self.channel)
        self.channel.ready = True
        self.channel.capabilities = 0xc8
        self.channel.max_hz = 1000
        self.key = json.dumps(dict(profile='dm4310',id=1,feedbackId=2))
        self.channel.roster = [dict(key=self.key,family='dm',sampleHz=100)]
    async def asyncTearDown(self):
        await self.channel.close()

    async def test_remote_device_settings_ack_and_rejection(self):
        c = self.channel
        result = await c.handle('deviceSettings', [{'data':1, 'baud':115200}])
        self.assertEqual((result['status'],result['data'],result['baud']), (1,1,115200))
        self.assertEqual(c.transport.writes[-1],device_settings_command(data=1,baud=115200,token=c.token))
        self.assertEqual(self.events[-1][0],'deviceSettings')
        for settings in ({'data':2}, {'baud':1}, {'unrelated':1}):
            count = len(c.transport.writes)
            with self.assertRaises(ValueError):await c.handle('deviceSettings',[settings])
            self.assertEqual(len(c.transport.writes),count)

    async def test_uart_excludes_can_and_retains_raw_data(self):
        c = self.channel
        await c.handle('setPolling', [self.key, 1, True, 100])
        c.receive(packet(21, a=1, x=115200))
        self.assertTrue(c.uart)
        self.assertFalse(c.polls)
        with self.assertRaises(ValueError):
            await c.handle('manual', [dict(identifier=1, data=[])])
        with self.assertRaises(ValueError):
            await c.handle('send', [packet(0x12, x=500000).hex()])
        uart = bytes([1, 0x18, 3, 0, 0, 255, 65]) + bytes(9) + (7).to_bytes(4, 'little')
        await c.handle('send', [uart.hex()])
        self.assertEqual(c.transport.writes[-1], uart)
        raw = bytes([1, 19, 3, 0, 0, 255, 65]) + bytes(9) + (1).to_bytes(4, 'little')
        c.receive(raw)
        self.assertEqual(c.packets[-1][0], raw)
        c.receive(packet(21, a=0))
        with self.assertRaises(ValueError):
            await c.handle('send', [uart.hex()])
        self.assertFalse(c.uart)

    async def test_manual_success_requires_can_result(self):
        c = self.channel
        await c.handle('manual',[dict(identifier=0x123,data=[1,2])])
        token = c.token
        self.assertIn(token,c.pending_tx)
        self.assertNotIn('成功',self.events[-1][1]['status'])
        c.receive(packet(2,token=token))
        self.assertNotIn(token,c.pending_tx)
        self.assertIn('CAN 发送成功',self.events[-1][1]['status'])
        await c.handle('manual',[dict(identifier=1,data=[])])
        await c.disconnect()
        self.assertTrue(any('未知' in d.get('status','') for _,d in self.events))

    async def test_query_confirm_and_disconnect(self):
        c = self.channel
        await c.handle('setPolling',[self.key,1,True,700])
        self.assertIn(self.key,c.polls)
        self.assertTrue(self.events[-1][1]['enabled'])
        await c.handle('setPolling',[self.key,1,False,700])
        self.assertFalse(c.polls)
        await c.handle('setPolling',[self.key,1,True,100])
        c.receive(packet(8,a=4,x=1))
        self.assertFalse(c.polls)
        self.assertFalse(self.events[-1][1]['enabled'])

    async def test_removed_motor_stops_renewals_and_can_be_disabled(self):
        c = self.channel
        await c.handle('setPolling',[self.key,1,True,100])
        c.submit('dashboard',['[]'])
        self.assertFalse(c.polls)
        await c.handle('setPolling',[self.key,1,False,100])
        self.assertFalse(self.events[-1][1]['enabled'])

    async def test_detail_revision_filters_stale_samples(self):
        c = self.channel
        await c.handle('showDetail',[self.key])
        self.assertEqual(c.applied_mode,2)
        c.receive(packet(11,x=c.revision-1))
        self.assertFalse(c.packets)
        c.receive(packet(11,x=c.revision))
        self.assertEqual(len(c.packets),1)
        await c.handle('leaveDetail',[])
        self.assertEqual(c.applied_mode,0)
        self.assertFalse(c.packets)

    async def test_bounded_telemetry_and_command_queue(self):
        c = self.channel
        for _ in range(10000):
            c.receive(packet(1))
        self.assertEqual(len(c.packets),512)
        self.assertEqual(c.dropped,9488)
        for _ in range(100):
            c.submit('send',['bad'])
        self.assertEqual(c.commands.qsize(),32)
        c.submit('disconnect',[])
        self.assertEqual(c.commands.qsize(),1)

    async def test_secrets_not_emitted_to_web(self):
        self.channel.network_status(dict(id='0123456789ab',fp='11'*32,token='22'*32,state=2))
        self.assertNotIn('22'*32,json.dumps(self.events))
        self.assertNotIn('11'*32,json.dumps(self.events))
        self.assertEqual(self.channel.saved['token'],'22'*32)

    async def test_owner_cancel_connect_drops_old_commands(self):
        c = self.channel
        c.start()
        block = asyncio.Event()
        async def stuck(*_):
            await block.wait()
        with patch('can_scope.engine.scan_ble',stuck):
            c.ready=False
            c.submit('scan',[])
            await asyncio.sleep(.01)
            c.submit('disconnect',[])
            await asyncio.sleep(.03)
        self.assertFalse(c.ready)
        self.assertTrue(any(k=='state' and v['value']=='idle' for k,v in self.events))


class TlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.key,self.cert = root/'key.pem',root/'cert.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=can-test','-keyout',str(self.key),'-out',str(self.cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.cert.read_text())).hexdigest()
        self.auth = []
        self.done = asyncio.Event()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.maximum_version=context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.cert,self.key)
        async def server(reader,writer):
            try:
                auth = await reader.readexactly(36)
                self.auth.append(auth)
                writer.write(b'OK');await writer.drain();await asyncio.sleep(.01)
                writer.write(b'01'+packet(4)[:7]);await writer.drain();await asyncio.sleep(.01)
                writer.write(packet(4)[7:]+packet(14));await writer.drain()
                await reader.read()
            except (asyncio.IncompleteReadError,ConnectionError):
                pass
            finally:
                writer.close()
                try: await writer.wait_closed()
                except ConnectionError: pass
                self.done.set()
        self.server = await asyncio.start_server(server,'127.0.0.1',0,ssl=context)
        self.port = self.server.sockets[0].getsockname()[1]
    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed()
        await asyncio.wait_for(self.done.wait(),2)
        self.tmp.cleanup()

    async def test_fragmented_tls_and_token(self):
        packets=[]
        client=LanTransport(packets.append,lambda _:None)
        try:
            await client.connect('127.0.0.1',self.pin,'12'*32,self.port)
            await asyncio.sleep(.08)
            self.assertEqual(self.auth,[b'CAN1'+bytes.fromhex('12'*32)])
            self.assertEqual(packets,[packet(4),packet(14)])
        finally:
            await client.close()

    async def test_bad_pin_never_sends_token(self):
        client=LanTransport(lambda _:None,lambda _:None)
        with self.assertRaises(ValueError):
            await client.connect('127.0.0.1','00'*32,'12'*32,self.port)
        self.assertEqual(self.auth,[])



class BleTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_gatt_and_authenticated_provisioning(self):
        import types
        from can_scope.protocol import SERVICE, NETWORK, WRITE, NOTIFY
        instances=[]
        class FakeClient:
            def __init__(self, device, **kwargs):
                self.is_connected=False
                self.services=types.SimpleNamespace(get_service=lambda uuid: object() if uuid==SERVICE else None)
                self.writes=[];self.paired=False;instances.append(self)
            async def connect(self): self.is_connected=True
            async def disconnect(self): self.is_connected=False
            async def pair(self): self.paired=True
            async def start_notify(self, uuid, callback):
                self.notifier=(uuid,callback)
            async def write_gatt_char(self, uuid, p, response):
                assert response
                if uuid==NETWORK: assert self.paired
                self.writes.append((uuid,bytes(p)))
            async def read_gatt_char(self, uuid):
                return json.dumps(dict(id='0123456789ab',fp='11'*32,token='22'*32,state=2,error=0,busy=False)).encode()
        with patch.dict(sys.modules,bleak=types.SimpleNamespace(BleakClient=FakeClient)):
            got=[]
            transport=BleTransport(got.append,lambda _:None)
            await transport.connect('01:02:03:04:05:06')
            client=instances[0]
            self.assertEqual(client.notifier[0],NOTIFY)
            client.notifier[1](None,bytearray(packet(4)))
            self.assertEqual(got,[packet(4)])
            await transport.write(packet(0x12,x=500000))
            self.assertEqual(client.writes[0][0],WRITE)
            statuses=[]
            await transport.provision(5,'Test WiFi','12345678',statuses.append,lambda _:None)
            self.assertTrue(client.paired)
            self.assertEqual(client.writes[1][1][0],3)
            self.assertEqual(client.writes[-1][1][0],5)
            self.assertEqual(len(statuses),1)
            await transport.close()
            self.assertFalse(client.is_connected)

if __name__ == '__main__':
    unittest.main()
