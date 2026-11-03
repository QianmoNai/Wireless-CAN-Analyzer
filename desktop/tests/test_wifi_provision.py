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
    def test_missing_password_file_fails_before_any_ble_or_confirmation(self):
        with patch.object(cli, 'protect_process'), patch.object(cli.os, 'geteuid', return_value=1000), \
             patch.object(cli.os, 'open', side_effect=FileNotFoundError('SYNTHETIC_PRIVATE')), \
             patch.object(sys, 'argv', ['wifi-provision.py', '--password-file', '/synthetic/not-present']), \
             patch.object(cli, 'provision', side_effect=AssertionError('BLE forbidden')), \
             patch('builtins.input', side_effect=AssertionError('confirmation forbidden')), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as output:
            self.assertEqual(cli.main(), 1)
        self.assertIn('密码文件不存在，请先运行 test-hotspot.py up 并确认 status 正常', output.getvalue())
        self.assertIn('启动/密码文件检查', output.getvalue())
        self.assertNotIn('SYNTHETIC_PRIVATE', output.getvalue())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux file permission/NOFOLLOW semantics')
    def test_early_password_check_reads_metadata_only_and_rechecks_after_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.password'
            path.write_text('SyntheticFixtureOnly\n')
            path.chmod(0o600)
            with patch.object(cli.os, 'read', side_effect=AssertionError('credential read forbidden')):
                cli.check_password_file(path)
            path.unlink()
            with self.assertRaises(cli.StopProvision) as caught:
                cli.read_password_file(path)
            self.assertIn('密码文件不存在', str(caught.exception))
    @unittest.skipUnless(sys.platform == 'linux', 'Linux file permission/NOFOLLOW semantics')
    def test_password_file_checks_mode_owner_links_size_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.password'
            path.write_text('SyntheticFixtureOnly\n')
            path.chmod(0o600)
            with patch('getpass.getpass', side_effect=AssertionError('interactive password forbidden')), \
                 patch('builtins.input', side_effect=AssertionError('SSID input forbidden')):
                self.assertEqual(cli.read_credentials('Fixture', path), ('Fixture', 'SyntheticFixtureOnly'))
            for mode in (0o644, 0o400, 0o660):
                path.chmod(mode)
                with patch.object(cli.os, 'read', side_effect=AssertionError('read forbidden')):
                    with self.assertRaises(cli.StopProvision):
                        cli.read_password_file(path)
            path.chmod(0o600)
            with patch.object(cli.os, 'geteuid', return_value=path.stat().st_uid + 1), \
                 patch.object(cli.os, 'read', side_effect=AssertionError('read forbidden')):
                with self.assertRaises(cli.StopProvision):
                    cli.read_password_file(path)
            link = Path(directory) / 'symlink'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                cli.read_password_file(link)
            os.link(path, Path(directory) / 'hardlink')
            with self.assertRaises(cli.StopProvision):
                cli.read_password_file(path)
            (Path(directory) / 'hardlink').unlink()
            path.write_bytes(b'A' * 66)
            with self.assertRaises(cli.StopProvision):
                cli.read_password_file(path)
            path.write_bytes(b'\xff' * 8)
            with self.assertRaises(cli.StopProvision) as error:
                cli.read_password_file(path)
            self.assertNotIn('\\xff', str(error.exception))

    def test_target_and_subnet_parser_reject_bad_identity_and_address(self):
        self.assertEqual(cli.target_address(cli.TARGET + '@11:22:33:44:55:66'), '11:22:33:44:55:66')
        for target in ('bad@11:22:33:44:55:66', cli.TARGET, cli.TARGET + '@unknown'):
            with self.assertRaises(cli.argparse.ArgumentTypeError):
                cli.target_address(target)
        self.assertEqual(str(cli.discovery_subnet('10.77.0.0/24')), '10.77.0.0/24')
        for subnet in ('8.8.8.0/24', '10.77.0.1/24', '::/64', '10.77.0.0/32'):
            with self.assertRaises(cli.argparse.ArgumentTypeError):
                cli.discovery_subnet(subnet)

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

    def test_compact_snapshot_decodes_timestamps_and_safe_failure_details(self):
        origin = 2**32 + 100
        value = dict(state=4, error=4, busy=False, mode=0, reason=2, stage=4, api_err=0, got_ip=0,
                     diag=[7, origin, 1, -1, -1, 1, 'dcd87b53ee5c', -59, 1, '01', 80, '90da7273722c', 12],
                     ssid='SYNTHETIC_PRIVATE', token='SYNTHETIC_PRIVATE', fp='SYNTHETIC_PRIVATE')
        safe = network_status_fields(value)
        self.assertEqual(safe['sta_start_ms'], origin + 1)
        self.assertEqual(safe['connected_ms'], -1)
        self.assertEqual(safe['attempt_id'], 7)
        self.assertEqual(safe['disconnect_count'], 1)
        self.assertEqual(safe['sta_mac'], '90:da:72:73:72:2c')
        self.assertEqual(network_status_fields(safe), safe)
        with redirect_stderr(io.StringIO()) as output:
            cli.report_failure(WifiProvisionError(value), '[3/7] 配网发送/接入')
        text = output.getvalue()
        for expected in ('bssid=dc:d8:7b:53:ee:5c', 'connected_ms=未发生',
                         'rssi=-59', 'channel=1', 'country=01', 'TX功率上限=20 dBm',
                         'BLE实际间隔=15 ms', 'attempt_id=7', '不代表已关联'):
            self.assertIn(expected, text)
        self.assertNotIn('SYNTHETIC_PRIVATE', text + repr(WifiProvisionError(value)))
        for index, invalid in ((0, True), (2, 2**32), (6, 'SYNTHETIC_PRIVATE'), (9, 'SYNTHETIC_PRIVATE')):
            broken = list(value['diag'])
            broken[index] = invalid
            self.assertEqual(network_status_fields(dict(diag=broken)), {})
        complete = list(value['diag'])
        complete[3:5] = [10, 20]
        decoded = network_status_fields(dict(diag=complete))
        self.assertEqual((decoded['connected_ms'], decoded['got_ip_ms']), (origin+10, origin+20))

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
            with patch.object(sys, 'argv', ['wifi-provision.py', '--dry-run', '--password-file', '/nonexistent']), \
                 patch.object(cli, 'check_password_file', side_effect=AssertionError('file forbidden')):
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
    async def test_direct_provision_also_prechecks_before_cache_and_ble(self):
        options = SimpleNamespace(password_file=Path('/synthetic/missing'))
        with patch.object(cli, 'check_password_file', side_effect=cli.StopProvision('密码文件不存在')), \
             patch('can_scope.connection_store.load_connection', side_effect=AssertionError('cache forbidden')), \
             patch('can_scope.transports.scan_ble', side_effect=AssertionError('BLE forbidden')):
            with self.assertRaises(cli.StopProvision):
                await cli.provision(options)

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
        self.assertEqual((message.member, message.body), ('Get', ['org.bluez.Device1', 'Bonded']))
        self.assertEqual([call.args[0].body[-1] for call in bus.call.await_args_list], ['Paired', 'Bonded'])
        self.assertTrue(link.provision_authenticated)
        client.pair.assert_not_awaited()
        bus.call.return_value.body = [Variant('b', False)]
        with self.assertRaises(ConnectionError):
            await link.use_existing_bond()
        self.assertFalse(link.provision_authenticated)
        bus.call.side_effect = [SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[Variant('b', True)]),
                                SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[Variant('b', False)])]
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
        self.assertEqual(text.count('stage=4（wait_ip/等待接入或 IP，不代表已关联）'), 2)
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

    async def test_hotspot_subnet_filters_replies_and_target_still_checks_scan_address(self):
        async def discovery(identity, found, *, broadcast):
            self.assertEqual(broadcast, '10.77.0.255')
            for address in ('192.168.1.42', '10.77.0.0', '10.77.0.255', '10.77.0.20'):
                found(dict(address='wifi:' + address))
        with redirect_stdout(io.StringIO()), patch('builtins.input', side_effect=AssertionError('confirmation forbidden')):
            self.assertEqual(await cli.find_ip(discovery, cli.discovery_subnet('10.77.0.0/24')), '10.77.0.20')
            address = '11:22:33:44:55:66'
            scanner = AsyncMock(return_value={address: SimpleNamespace(address=address)})
            self.assertEqual((await cli.select_device(scanner, address))[0], address)
            scanner.return_value[address].address = '00:00:00:00:00:00'
            with self.assertRaises(cli.StopProvision):
                await cli.select_device(scanner, address)
            with self.assertRaises(cli.StopProvision):
                await cli.select_device(scanner, '00:00:00:00:00:00')

    async def exercise(self, identity=cli.TARGET, fail_apply=False, lose_fence_ack=False, reuse=False, bonded=True, join_error=False):
        events, clients, buffers = [], [], []
        join_started = []
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
                self.on_disconnect = _['disconnected_callback']
                clients.append(self)
            async def connect(self):
                self.is_connected = True
            async def disconnect(self):
                self.is_connected = False
            async def start_notify(self, _, notify):
                self.notify = notify
            async def pair(self):
                test.assertFalse(reuse)
                events.append('pair')
            @property
            def _backend(self):
                from dbus_fast import MessageType, Variant
                return SimpleNamespace(_device_path='/org/bluez/hci0/dev_11_22_33_44_55_66',
                    _bus=SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(
                        message_type=MessageType.METHOD_RETURN, body=[Variant('b', bonded)]))))
            async def write_gatt_char(self, uuid, data, response):
                if uuid == NETWORK:
                    buffers.append(data)
                    if data[0] == 5:
                        events.append('apply')
                        self.applied = True
                        if fail_apply:
                            raise ValueError('SYNTHETIC-PRIVATE-PAYLOAD')
                        join_started.append(True)
                        events.append('apply-ack')
                    elif data[0] == 1 and self.applied:
                        events.append('receipt-fence')
                        self.is_connected = False
                        self.on_disconnect(self)
                        events.append('ble-disconnected')
                        if lose_fence_ack:
                            raise TimeoutError('synthetic lost STATUS reply')
                elif uuid == WRITE:
                    test.assertEqual(data[1], 0x19)
                    if data[2]:
                        raise AssertionError('BLE mode mutation forbidden after APPLY')
                    self.notify(None, packet(22, a=int(bool(data[2])), b=self.mode, x=1,
                                             y=115200, token=u32(data, 16)))
            async def read_gatt_char(self, _):
                events.append('status')
                # A previous failed join must not prevent authenticated status
                # reading and a fresh, explicitly requested provisioning attempt.
                return json.dumps(dict(id=identity, fp='11' * 32, token='22' * 32,
                                       state=4, error=4 if join_started and join_error else 2, reason=2,
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
            if join_error:
                return
            found(dict(address='wifi:10.42.0.42'))  # Ignore replies outside requested subnet.
            found(dict(address='wifi:192.168.1.42'))

        def credentials(*_):
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
                options = SimpleNamespace(reuse_bond=True, target=address, ssid=None,
                                          password_file=None, subnet=cli.SUBNET) if reuse else None
                if identity != cli.TARGET or fail_apply or not bonded or join_error:
                    with self.assertRaises(Exception):
                        await cli.provision(options)
                    self.assertEqual(load_connection(path), dict(unrelated=True))
                else:
                    await cli.provision(options)
                    saved = load_connection(path)
                    self.assertTrue(all(saved.get(k) for k in ('id', 'fp', 'token', 'ip')))
                    self.assertEqual((saved['id'], saved['ip'], saved['mode']), (cli.TARGET, '192.168.1.42', 'wifi'))
                    self.assertTrue(saved['unrelated'])
                    self.assertNotIn('FixturePassword', path.read_text())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                text = output.getvalue()
                self.assertTrue('FixturePassword' not in text and '11' * 32 not in text and '22' * 32 not in text)
        self.assertEqual(len(clients), 2 if join_error else 1)
        self.assertEqual(events.count('pair'), 0 if reuse else 1)
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
        self.assertLess(events.index('apply-ack'), events.index('receipt-fence'))
        self.assertLess(events.index('receipt-fence'), events.index('ble-disconnected'))
        self.assertLess(events.index('ble-disconnected'), events.index('discover'))
        self.assertNotIn('mode', events)
        self.assertEqual([event for event in events if event in ('tls', 'query')], ['tls', 'query'])

    async def test_reuse_bond_never_pairs_or_registers_agent(self):
        with patch.object(cli, 'read_credentials', wraps=cli.read_credentials):
            events = await self.exercise(reuse=True)
        self.assertNotIn('register-agent', events)
        self.assertNotIn('pair', events)
        self.assertIn('apply', events)

    async def test_missing_bond_stops_before_password_or_mutation(self):
        events = await self.exercise(reuse=True, bonded=False)
        self.assertNotIn('apply', events)
        self.assertNotIn('credentials', events)
        self.assertNotIn('pair', events)

    async def test_wrong_identity_never_reads_or_sends_password(self):
        events = await self.exercise(identity='abcdef012345')
        self.assertNotIn('credentials', events)
        self.assertNotIn('apply', events)
        self.assertNotIn('tls', events)

    async def test_apply_failure_wipes_buffers_and_preserves_cache(self):
        events = await self.exercise(fail_apply=True)
        self.assertNotIn('mode', events)
        self.assertEqual(events.count('apply'), 1)

    async def test_status_fence_ack_lost_on_expected_disconnect_is_not_replayed(self):
        events = await self.exercise(lose_fence_ack=True)
        self.assertEqual(events.count('receipt-fence'), 1)
        self.assertEqual(events.count('apply'), 1)
        self.assertEqual(events.count('tls'), 1)
        self.assertEqual(events.count('query'), 1)

    async def test_discovery_timeout_reconnects_same_bond_for_status_only(self):
        events = await self.exercise(reuse=True, join_error=True)
        self.assertEqual(events.count('apply'), 1)
        self.assertEqual(events.count('credentials'), 1)
        self.assertEqual(events.count('status'), 2)
        self.assertNotIn('pair', events)
        self.assertNotIn('tls', events)

    async def test_pair_false_requires_authentication_on_same_connection(self):
        transport = BleTransport(lambda _: None, lambda _: None)
        transport.client = SimpleNamespace(is_connected=True)
        with self.assertRaises(ConnectionError):
            await transport.provision(1, '', '', lambda _: None, lambda _: None, pair=False)


if __name__ == '__main__':
    unittest.main()
