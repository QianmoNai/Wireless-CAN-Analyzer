"""Offline safety checks. All credentials/devices here are synthetic fixtures."""
import asyncio
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'desktop'))
from can_scope.connection_store import load_connection, save_connection
from can_scope.protocol import NETWORK, SERVICE, WRITE, packet, u32
from can_scope.transports import BleTransport, WifiProvisionError, discover, network_diagnostics_text, network_status_fields

spec = importlib.util.spec_from_file_location('wifi_provision', ROOT / 'scripts/wifi-provision.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class CacheTests(unittest.TestCase):
    def test_private_atomic_cache_and_preserved_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app' / 'connection-1.json'
            value = dict(mode='ble', name='Fixture', unrelated=True)
            save_connection(path, value)
            self.assertEqual(load_connection(path), value)
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(list(path.parent.iterdir()), [path])
            path.write_text('[]')
            with self.assertRaises(ValueError):
                load_connection(path)


class OfflineCliTests(unittest.TestCase):
    def test_wifi_error_uses_structured_fields_and_correct_save_failure(self):
        value = dict(state=4, error=5, busy=False, mode=0, reason=15,
                     ssid='SYNTHETIC-PRIVATE', fp='SYNTHETIC-PRIVATE', token='SYNTHETIC-PRIVATE')
        error = WifiProvisionError(value)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.reason, 15)
        with redirect_stderr(io.StringIO()) as output:
            cli.report_failure(error, '[3/7] 配网发送/接入')
            cli.report_failure(WifiProvisionError(dict(state=4, error=4, busy=False, mode=0)))
            cli.report_failure(ValueError('设备配网失败，错误码：SYNTHETIC-PRIVATE'))
        text = output.getvalue()
        self.assertNotIn('SYNTHETIC-PRIVATE', text + repr(error))
        self.assertIn('凭据保存失败', text)
        self.assertIn('reason=15', text)
        self.assertIn('reason=未提供', text)
        self.assertIn('错误码 4', text)

    def test_diagnostic_fields_are_bounded_and_no_arbitrary_text_is_retained(self):
        safe = dict(state=4, error=4, mode=0, busy=False, reason=-1,
                    stage=4, api_err=-1, got_ip=0)
        value = dict(safe, ssid='SYNTHETIC-PRIVATE', fp='SYNTHETIC-PRIVATE',
                     token='SYNTHETIC-PRIVATE', detail='SYNTHETIC-PRIVATE')
        self.assertEqual(network_status_fields(value), safe)
        error = WifiProvisionError(value)
        self.assertEqual(error.reason, -1)
        text = network_diagnostics_text(value)
        self.assertIn('未收到断开事件', text)
        self.assertIn('stage=4（wait_ip', text)
        self.assertIn('api_err=-1（0xffffffff）', text)
        self.assertNotIn('SYNTHETIC-PRIVATE', text + repr(error))
        for key, invalid in (('reason', -2), ('reason', 65536), ('stage', 6),
                             ('api_err', 2**31), ('api_err', -(2**31)-1),
                             ('got_ip', 2)):
            self.assertNotIn(key, network_status_fields({key: invalid}))
        for key in ('reason', 'stage', 'api_err', 'got_ip'):
            for invalid in (True, None, 'SYNTHETIC-PRIVATE', {'x': 'SYNTHETIC-PRIVATE'}):
                self.assertNotIn(key, network_status_fields({key: invalid}))
        self.assertIn('reason=未提供', network_diagnostics_text({}))

    def test_reason_and_stage_names_match_idf_numeric_codes(self):
        meanings = {2: '认证过期', 15: '四次握手超时', 23: '802.1X 认证失败',
                    201: '未找到 AP', 202: '认证失败', 203: '关联失败',
                    204: '握手超时', 205: '连接失败', 206: 'AP TSF 重置',
                    207: '漫游', 208: '关联重试等待时间过长', 209: 'SA Query 超时',
                    210: '安全配置兼容', 211: '认证模式阈值', 212: 'RSSI 阈值'}
        for reason, meaning in meanings.items():
            text = network_diagnostics_text(dict(reason=reason))
            self.assertIn('reason=' + str(reason), text)
            self.assertIn(meaning, text)
        for stage, meaning in enumerate(('尚无 APPLY', 'set_config', 'start', 'connect', 'wait_ip', 'save')):
            self.assertIn(meaning, network_diagnostics_text(dict(stage=stage)))
        self.assertIn('未知断开原因', network_diagnostics_text(dict(reason=65535)))

    def test_unsafe_status_fields_and_neighbor_ssids_are_not_retained(self):
        bad = WifiProvisionError(dict(error='SYNTHETIC-PRIVATE', reason='SYNTHETIC-PRIVATE',
                                      state=True, mode=-1, busy='SYNTHETIC-PRIVATE'))
        self.assertEqual(bad.status, {})
        self.assertIsNone(bad.code)
        self.assertIsNone(bad.reason)
        self.assertNotIn('SYNTHETIC-PRIVATE', repr(bad))
        rows = [['Fixture'.encode().hex(), -54, 3], ['Neighbor'.encode().hex(), -20, 0]]
        self.assertEqual(cli.matching_scan_rows(rows, 'Fixture'), [dict(channel=None, rssi=-54, authmode=3)])
        rows[0].append(2)
        self.assertEqual(cli.matching_scan_rows(rows, 'Fixture')[0]['channel'], 2)
        self.assertEqual(network_status_fields(dict(state=4, error=4, busy=False, mode=0, reason=15,
                                                   ssid='SYNTHETIC-PRIVATE')), dict(state=4, error=4, mode=0, busy=False, reason=15))

    def test_structured_errors_are_classified_without_backend_payloads(self):
        from bleak.exc import BleakDBusError, BleakGATTProtocolError
        from dbus_fast import DBusError
        errors = [BleakGATTProtocolError(3),
                  BleakDBusError('org.bluez.Error.AuthenticationTimeout', ['SYNTHETIC-SENSITIVE-PAYLOAD']),
                  BleakDBusError('SYNTHETIC-SENSITIVE-NAME', ['SYNTHETIC-SENSITIVE-PAYLOAD']),
                  DBusError('org.bluez.Error.AuthenticationFailed', 'SYNTHETIC-SENSITIVE-PAYLOAD'),
                  ValueError('SYNTHETIC-SENSITIVE-PAYLOAD')]
        with redirect_stderr(io.StringIO()) as output:
            for error in errors:
                cli.report_failure(error, '[2/7] 测试认证')
        text = output.getvalue()
        self.assertNotIn('SYNTHETIC-SENSITIVE', text)
        for expected in ('BLE 写入不允许', '认证超时', '认证失败', 'BleakGATTProtocolError', '[2/7] 测试认证'):
            self.assertIn(expected, text)

    def test_help_and_dry_run_cannot_access_hardware_or_cache(self):
        with patch('bleak.BleakClient', side_effect=AssertionError('pairing forbidden')), \
             patch('bleak.BleakScanner', side_effect=AssertionError('scan forbidden')), \
             patch('socket.socket', side_effect=AssertionError('socket forbidden')), \
             patch('serial.Serial', side_effect=AssertionError('serial forbidden')), \
             patch('can_scope.connection_store.load_connection', side_effect=AssertionError('cache forbidden')), \
             patch('can_scope.connection_store.save_connection', side_effect=AssertionError('cache forbidden')), \
             patch.object(cli, 'protect_process'), redirect_stdout(io.StringIO()) as output:
            with patch.object(sys, 'argv', ['wifi-provision.py', '--help']):
                with self.assertRaises(SystemExit) as caught:
                    cli.main()
                self.assertEqual(caught.exception.code, 0)
            with patch.object(sys, 'argv', ['wifi-provision.py', '--dry-run']):
                self.assertEqual(cli.main(), 0)
            self.assertIn('DRY-RUN PASS', output.getvalue())
            self.assertIn(cli.NOTICE, output.getvalue())

    def test_reject_password_without_echo_or_exception_payload(self):
        # Exercise invalid Unicode before wifi_commands can raise a secret-bearing
        # UnicodeEncodeError. Also refuse getpass's insecure fallback.
        sample = 'fixture非ASCII密码'
        with patch('builtins.input', return_value='Fixture SSID'), patch('getpass.getpass', return_value=sample):
            with self.assertRaises(cli.StopProvision) as caught:
                cli.read_credentials()
            self.assertNotIn(sample, str(caught.exception))
        def fallback(*_):
            import warnings
            import getpass
            warnings.warn('fallback', getpass.GetPassWarning)
            self.fail('getpass fallback must stop before reading')
        with patch('builtins.input', return_value='Fixture'), patch('getpass.getpass', side_effect=fallback):
            with self.assertRaises(cli.getpass.GetPassWarning):
                cli.read_credentials()

    def test_backend_exception_payload_is_never_printed(self):
        async def fail():
            raise ValueError('SYNTHETIC-SENSITIVE-BACKEND-PAYLOAD')
        with patch.object(cli, 'protect_process'), patch.object(cli.os, 'geteuid', return_value=1000), \
             patch.object(sys.stdin, 'isatty', return_value=True), patch('builtins.input', return_value=cli.TARGET), \
             patch.object(cli, 'provision', fail), patch.object(sys, 'argv', ['wifi-provision.py']), \
             redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as error:
            # redirect_stdout changes stdout, so install its isatty after capture.
            with patch.object(sys.stdout, 'isatty', return_value=True):
                self.assertEqual(cli.main(), 1)
            self.assertNotIn('SYNTHETIC-SENSITIVE', output.getvalue() + error.getvalue())


class ProvisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_bond_checks_paired_without_pair_or_trust_update(self):
        from dbus_fast import MessageType, Variant
        bus = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(
            message_type=MessageType.METHOD_RETURN, body=[Variant('b', True)])))
        client = SimpleNamespace(is_connected=True, pair=AsyncMock(side_effect=AssertionError('Pair forbidden')),
                                 _backend=SimpleNamespace(_bus=bus, _device_path='/org/bluez/hci0/dev_11_22_33_44_55_66'))
        link = BleTransport(lambda _: None, lambda _: None)
        link.client = client
        await link.use_existing_bond()
        message = bus.call.await_args.args[0]
        self.assertEqual((message.member, message.body), ('Get', ['org.bluez.Device1', 'Paired']))
        self.assertTrue(link.provision_authenticated)
        client.pair.assert_not_awaited()
        bus.call.return_value.body = [Variant('b', False)]
        with self.assertRaises(ConnectionError):
            await link.use_existing_bond()
        self.assertFalse(link.provision_authenticated)

    async def test_transport_preserves_numeric_failure_without_raw_json(self):
        link = BleTransport(lambda _: None, lambda _: None)
        link.provision_authenticated = True
        value = dict(state=4, error=4, busy=False, mode=0, reason=23, ssid='SYNTHETIC-PRIVATE')
        link.client = SimpleNamespace(is_connected=True, write_gatt_char=AsyncMock(),
                                      read_gatt_char=AsyncMock(return_value=json.dumps(value).encode()))
        with self.assertRaises(WifiProvisionError) as caught:
            await link.provision(2, '', '', lambda _: None, lambda _: None, pair=False)
        self.assertEqual((caught.exception.code, caught.exception.reason), (4, 23))
        self.assertNotIn('SYNTHETIC-PRIVATE', repr(caught.exception))

    async def test_diagnose_only_uses_existing_bond_status_and_scan(self):
        events = []
        address, ssid = '11:22:33:44:55:66', 'Fixture'
        test = self
        class Link:
            def __init__(self, receive, _): self.receive = receive
            async def connect(self, _): events.append('connect')
            async def use_existing_bond(self): events.append('existing-bond')
            async def write(self, command):
                test.assertEqual(command[2:16], bytes(14))
                self.receive(packet(22, b=0, x=1, y=115200, token=u32(command, 16)))
            async def provision(self, operation, name, password, status, networks, *, pair):
                test.assertFalse(pair)
                test.assertEqual((name, password), ('', ''))
                events.append(operation)
                status(dict(id=cli.TARGET, state=4, error=4 if len(events) < 4 else 0,
                            busy=False, mode=0, reason=203, stage=4, api_err=0, got_ip=0,
                            ssid='SYNTHETIC-PRIVATE', fp='SYNTHETIC-PRIVATE', token='SYNTHETIC-PRIVATE'))
                if operation == 2:
                    networks([[ssid.encode().hex(), -54, 3, 2], ['Neighbor'.encode().hex(), -20, 0]])
            async def close(self): events.append('close')
        with patch('can_scope.transports.scan_ble', AsyncMock(return_value={address: object()})), \
             patch('can_scope.transports.BleTransport', Link), patch('builtins.input', side_effect=[address, ssid]), \
             patch.object(cli, 'pairing_agent', side_effect=AssertionError('agent forbidden')), \
             patch('getpass.getpass', side_effect=AssertionError('password forbidden')), \
             patch('can_scope.connection_store.load_connection', side_effect=AssertionError('cache forbidden')), \
             patch('can_scope.connection_store.save_connection', side_effect=AssertionError('cache forbidden')), \
             redirect_stdout(io.StringIO()) as output:
            await cli.diagnose()
        self.assertEqual(events, ['connect', 'existing-bond', 1, 2, 1, 'close'])
        text = output.getvalue()
        self.assertIn('目标 SSID 是否出现：是', text)
        self.assertIn('RSSI=-54 dBm authmode=3', text)
        self.assertIn('信道=2', text)
        self.assertEqual(text.count('reason=203（关联失败）'), 2)
        self.assertEqual(text.count('stage=4（wait_ip/等待 IP）'), 2)
        self.assertIn('api_err=0（0x00000000）', text)
        self.assertNotIn('SYNTHETIC-PRIVATE', text)
        self.assertNotIn('Neighbor', text)

    async def test_discovery_uses_subnet_broadcast_and_filters_identity(self):
        loop = asyncio.get_running_loop()
        sock = MagicMock()
        sock.__enter__.return_value = sock
        responses = [(b'CAN_SCOPE_1 abcdef012345 32323', ('192.168.1.20', 32323)),
                     (b'CAN_SCOPE_1 90da7273722c 32323', ('192.168.1.42', 32323)),
                     asyncio.TimeoutError()]
        with patch('can_scope.transports.socket.socket', return_value=sock), \
             patch.object(loop, 'sock_sendto', new_callable=AsyncMock) as send, \
             patch.object(loop, 'sock_recvfrom', new=AsyncMock(side_effect=responses)):
            rows = []
            await discover(cli.TARGET, rows.append, broadcast='192.168.1.255')
            send.assert_awaited_once_with(sock, b'CAN_SCOPE_DISCOVER_1', ('192.168.1.255', 32323))
            self.assertEqual([row['address'] for row in rows], ['wifi:192.168.1.42'])
            sock.bind.assert_called_once_with(('', 0))

    async def exercise(self, identity=cli.TARGET, fail_apply=False, lose_mode_ack=False):
        events, clients, buffers = [], [], []
        address = '11:22:33:44:55:66'  # Deliberately different from STA ID.
        other = 'AA:BB:CC:DD:EE:FF'
        device = SimpleNamespace(address=address)
        test = self

        class Client:
            def __init__(self, selected, **_):
                test.assertIs(selected, device)
                self.is_connected = False
                self.services = SimpleNamespace(get_service=lambda uuid: object() if uuid == SERVICE else None)
                self.applied = False
                self.mode = 0
                clients.append(self)
            async def connect(self):
                self.is_connected = True
            async def disconnect(self):
                self.is_connected = False
            async def start_notify(self, _, notify):
                self.notify = notify
            async def pair(self):
                events.append('pair')
            async def write_gatt_char(self, uuid, data, response):
                if uuid == NETWORK:
                    buffers.append(data)
                    if data[0] == 5:
                        events.append('apply')
                        self.applied = True
                        if fail_apply:
                            raise ValueError('SYNTHETIC-PRIVATE-PAYLOAD')
                elif uuid == WRITE:
                    test.assertEqual(data[1], 0x19)
                    if data[2]:
                        events.append('mode')
                        self.mode = data[4]
                        if lose_mode_ack:
                            raise TimeoutError('synthetic lost reply')
                    self.notify(None, packet(22, a=int(bool(data[2])), b=self.mode, x=1,
                                             y=115200, token=u32(data, 16)))
            async def read_gatt_char(self, _):
                events.append('status')
                # A previous failed join must not prevent authenticated status
                # reading and a fresh, explicitly requested provisioning attempt.
                return json.dumps(dict(id=identity, fp='11' * 32, token='22' * 32,
                                       state=2 if self.applied else 4, error=0 if self.applied else 2,
                                       busy=False, ip='192.168.1.42' if self.applied else '')).encode()

        class Lan:
            def __init__(self, receive, _):
                self.receive = receive
            async def connect(self, ip, fp, token):
                events.append('tls')
                test.assertEqual(ip, '192.168.1.42')
                test.assertTrue(fp == '11' * 32 and token == '22' * 32)
            async def write(self, data):
                events.append('query')
                test.assertEqual(data[2:16], bytes(14))
                self.receive(packet(22, b=1, x=1, y=115200, token=u32(data, 16)))
            async def close(self):
                events.append('close-tls')

        async def scan(_):
            return {other: SimpleNamespace(address=other), address: device}

        async def discover(identity, found, *, broadcast):
            events.append('discover')
            test.assertEqual((identity, broadcast), (cli.TARGET, '192.168.1.255'))
            found(dict(address='wifi:10.42.0.42'))  # Ignore replies outside requested subnet.
            found(dict(address='wifi:192.168.1.42'))

        def credentials():
            events.append('credentials')
            return 'Fixture SSID', 'FixturePassword'

        @asynccontextmanager
        async def agent(selected_client, selected_address):
            test.assertIs(selected_client, clients[0])
            test.assertEqual(selected_address, address)
            events.append('register-agent')
            try:
                yield
            finally:
                events.append('unregister-agent')

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'connection-1.json'
            save_connection(path, dict(unrelated=True))
            with patch('bleak.BleakClient', Client), patch('can_scope.transports.scan_ble', scan), \
                 patch('can_scope.transports.LanTransport', Lan), patch('can_scope.transports.discover', discover), \
                 patch('can_scope.connection_store.connection_folder', return_value=Path(directory)), \
                 patch.object(cli, 'pairing_agent', agent), \
                 patch('builtins.input', return_value=address), patch.object(cli, 'read_credentials', credentials), \
                 redirect_stdout(io.StringIO()) as output, redirect_stderr(output):
                if identity != cli.TARGET or fail_apply:
                    with self.assertRaises(Exception):
                        await cli.provision()
                    self.assertEqual(load_connection(path), dict(unrelated=True))
                else:
                    await cli.provision()
                    saved = load_connection(path)
                    self.assertTrue(all(saved.get(k) for k in ('id', 'fp', 'token', 'ip')))
                    self.assertEqual((saved['id'], saved['ip'], saved['mode']), (cli.TARGET, '192.168.1.42', 'wifi'))
                    self.assertTrue(saved['unrelated'])
                    self.assertNotIn('FixturePassword', path.read_text())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                text = output.getvalue()
                self.assertTrue('FixturePassword' not in text and '11' * 32 not in text and '22' * 32 not in text)
        self.assertEqual(len(clients), 1)
        self.assertEqual(events.count('pair'), 1)
        self.assertFalse(clients[0].is_connected)
        # Mutable protocol buffers must be wiped on success and failure.
        self.assertTrue(all(not any(buffer) for buffer in buffers if isinstance(buffer, bytearray)))
        return events

    async def test_full_order_single_pair_tls_and_query(self):
        events = await self.exercise()
        self.assertLess(events.index('pair'), events.index('status'))
        self.assertLess(events.index('register-agent'), events.index('pair'))
        self.assertLess(events.index('unregister-agent'), events.index('credentials'))
        self.assertLess(events.index('status'), events.index('credentials'))
        self.assertLess(events.index('apply'), events.index('mode'))
        self.assertLess(events.index('mode'), events.index('discover'))
        self.assertEqual([event for event in events if event in ('tls', 'query')], ['tls', 'query'])

    async def test_wrong_identity_never_reads_or_sends_password(self):
        events = await self.exercise(identity='abcdef012345')
        self.assertNotIn('credentials', events)
        self.assertNotIn('apply', events)
        self.assertNotIn('tls', events)

    async def test_apply_failure_wipes_buffers_and_preserves_cache(self):
        events = await self.exercise(fail_apply=True)
        self.assertNotIn('mode', events)
        self.assertEqual(events.count('apply'), 1)

    async def test_unknown_mode_ack_not_replayed_tls_query_proves_mode(self):
        events = await self.exercise(lose_mode_ack=True)
        self.assertEqual(events.count('mode'), 1)
        self.assertEqual(events.count('tls'), 1)
        self.assertEqual(events.count('query'), 1)

    async def test_pair_false_requires_authentication_on_same_connection(self):
        transport = BleTransport(lambda _: None, lambda _: None)
        transport.client = SimpleNamespace(is_connected=True)
        with self.assertRaises(ConnectionError):
            await transport.provision(1, '', '', lambda _: None, lambda _: None, pair=False)


if __name__ == '__main__':
    unittest.main()
