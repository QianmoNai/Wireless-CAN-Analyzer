"""Offline only: real temporary fixture files, mocked radios/processes/randomness."""
from contextlib import redirect_stderr, redirect_stdout, ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

if sys.platform != 'linux':
    raise unittest.SkipTest('Arch/Linux-only hotspot helper')

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('test_hotspot_script', ROOT / 'scripts/test-hotspot.py')
hotspot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hotspot)

RADIO = 'Interface wlan0\n type managed\n wiphy 0\n channel 1 (2412 MHz), width: 20 MHz, center1: 2412 MHz'
DEVICES = 'phy#0\n Interface wlan0\n type managed\n Unnamed/non-netdev interface\n type P2P-device\n'
PHY = '''* 2412.0 MHz [1] (22.0 dBm)
valid interface combinations:
 * #{ managed } <= 1, #{ AP, P2P-client, P2P-GO } <= 1, #{ P2P-device } <= 1,
   total <= 3, #channels <= 1
'''
BASE = dict(phy='phy0', channel=1, frequency=2412, width=20, bssid='00:11:22:33:44:55',
            ipv4=['192.168.1.119'], connection='fixture-uuid',
            default=[dict(gateway='192.168.1.1', dev='wlan0')], shared_10_42_0_1=True,
            problems=[], ready_for_user_up=True)


class HotspotTests(unittest.TestCase):
    def test_5g_and_non_20mhz_rejected_without_mutation(self):
        for value in (RADIO.replace('1 (2412', '149 (5745'), RADIO.replace('width: 20', 'width: 40'), 'disconnected'):
            with self.assertRaises(hotspot.HotspotError):
                hotspot.radio(value)
        self.assertEqual(hotspot.radio(RADIO)['channel'], 1)

    def test_combination_accounts_for_p2p_and_interface_limit(self):
        self.assertTrue(hotspot.same_channel_combination(PHY, DEVICES, 'phy0'))
        self.assertFalse(hotspot.same_channel_combination(PHY, DEVICES + ' Interface extra\n type managed\n', 'phy0'))
        self.assertFalse(hotspot.same_channel_combination(PHY.replace('total <= 3', 'total <= 2'), DEVICES, 'phy0'))
        self.assertFalse(hotspot.same_channel_combination(PHY.replace('AP, ', ''), DEVICES, 'phy0'))

    def test_config_is_wpa2_ccmp_same_channel_without_router_or_dns(self):
        hostapd, dhcp = hotspot.configurations(1, 'SYNTHETIC_TEST_ONLY')
        for expected in ('channel=1\n', 'wpa=2\n', 'rsn_pairwise=CCMP\n', 'ieee80211w=0\n',
                         'ht_capab=\n', 'logger_stdout_level=2\n', 'logger_syslog=0\n'):
            self.assertIn(expected, hostapd)
        for forbidden in ('HT40', 'TKIP', 'SAE', 'bridge=', 'ctrl_interface='):
            self.assertNotIn(forbidden, hostapd)
        for expected in ('interface=csap0\n', 'bind-interfaces\n', 'port=0\n',
                         'dhcp-option=option:router\n', 'dhcp-option=option:dns-server\n'):
            self.assertIn(expected, dhcp)

    def test_log_filter_never_repeats_arbitrary_daemon_payload(self):
        private = 'SYNTHETIC_PSK_TOKEN_HOSTNAME'
        lines = [('hostapd', 'WPA: PSK=' + private), ('hostapd', 'SSID=' + private),
                 ('hostapd', 'csap0: STA 90:DA:72:73:72:2C IEEE 802.11: authenticated ' + private),
                 ('hostapd', 'csap0: STA 90:DA:72:73:72:2C IEEE 802.11: associated ' + private),
                 ('hostapd', 'csap0: STA 90:DA:72:73:72:2C WPA: pairwise key handshake completed (RSN) ' + private),
                 ('dhcp', 'DHCPACK(csap0) 10.77.0.20 90:da:72:73:72:2c ' + private)]
        output = [hotspot.safe_event(kind, line) for kind, line in lines]
        self.assertEqual(output[:2], [None, None])
        self.assertIn('四次握手完成', output[4])
        self.assertIn('DHCPACK', output[5])
        self.assertNotIn(private, repr(output))

    def test_isolation_writes_only_new_interface_knobs_before_up(self):
        writes = []
        with patch.object(Path, 'write_text', lambda path, value: writes.append((str(path), value))), \
             patch.object(hotspot, 'read_forwarding', return_value='0'):
            hotspot.isolation()
        self.assertTrue(writes)
        self.assertTrue(all('/csap0/' in p for p, _ in writes))
        self.assertIn(('/proc/sys/net/ipv4/conf/csap0/forwarding', '0\n'), writes)
        self.assertIn(('/proc/sys/net/ipv6/conf/csap0/disable_ipv6', '1\n'), writes)

    def test_check_ap_refuses_vpn_route_or_changed_sta(self):
        with patch.object(hotspot, 'snapshot', return_value=dict(BASE, channel=6)):
            with self.assertRaises(hotspot.HotspotError):
                hotspot.check_unchanged(BASE)
        def read(*args, **_):
            return 'no' if args[0] == 'nmcli' else RADIO.replace('type managed', 'type AP')
        with patch.object(hotspot, 'check_unchanged'), patch.object(hotspot, 'read_forwarding', return_value='0'), \
             patch.object(hotspot, 'run', side_effect=read), patch.object(hotspot, 'data', return_value=[dict(dev='Meta')]):
            with self.assertRaises(hotspot.HotspotError):
                hotspot.check_ap(dict(baseline=BASE), enabled=True)

    def test_preflight_has_no_mutating_commands_no_randomness_or_private_files(self):
        commands = []
        def read(*args, **_):
            commands.append(args)
            if args == ('iw', 'phy', 'phy0', 'info'): return PHY
            if args == ('iw', 'dev'): return DEVICES
            if args[0] == 'nmcli': return 'yes'
            if args[:3] == ('iw', 'reg', 'get'): return 'country 00:'
            return ''
        with patch.object(hotspot, 'snapshot', return_value=dict(BASE)), \
             patch.object(hotspot, 'run', side_effect=read), patch.object(hotspot, 'data', return_value=[]), \
             patch.object(hotspot, 'active', return_value=False) as active, \
             patch.object(hotspot.shutil, 'which', return_value='/usr/bin/fixture'), \
             patch.object(hotspot.secrets, 'choice', side_effect=AssertionError('password generation forbidden')), \
             patch.object(hotspot, 'private_write', side_effect=AssertionError('private write forbidden')):
            active.side_effect = lambda name: name == 'NetworkManager'
            result = hotspot.preflight()
        self.assertTrue(result['ready_for_user_up'])
        self.assertFalse(any('add' in c or 'set' in c or 'start' in c for c in commands))
        with patch.object(hotspot, 'snapshot', side_effect=hotspot.HotspotError('5G')), \
             patch.object(hotspot, 'run', side_effect=AssertionError('must stop first')):
            with self.assertRaises(hotspot.HotspotError):
                hotspot.preflight()

    def exercise_lifecycle(self, fail=None):
        events, units = [], set()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            runtime = root / 'run'
            secret = root / 'home/grokbot/secret'
            secret.parent.mkdir(parents=True)
            owner = dict(uid=os.getuid(), gid=os.getgid(), home=str(root / 'home'))
            managed = ['yes']
            def command(*args, **kwargs):
                events.append(args)
                if args[0] == 'systemd-run':
                    name = next(v.removeprefix('--unit=') for v in args if v.startswith('--unit='))
                    if fail == 'timer' and name.endswith('rollback'):
                        raise hotspot.HotspotError('timer fixture failure')
                    units.add(name + ('.timer' if name.endswith('rollback') else '.service'))
                if args[:2] == ('systemctl', 'stop'):
                    units.discard(args[2])
                if args[:3] == ('nmcli', 'device', 'set'):
                    managed[0] = args[-1]
                if args[0] == 'nmcli': return managed[0]
                if fail == 'dhcp' and args[0] == 'systemd-run' and '--unit=canscope-test-dhcp' in args:
                    raise hotspot.HotspotError('fixture backend error')
                return ''
            def choose(_):
                self.assertIn('canscope-test-rollback.timer', units)
                events.append(('synthetic-password',))
                return 'A'  # Never generate a real password, even inside tests.
            original_read, original_exists = Path.read_text, Path.exists
            def read(path, *args, **kwargs):
                return '99' if str(path) == '/sys/class/net/csap0/ifindex' else original_read(path, *args, **kwargs)
            def exists(path):
                return True if str(path) == '/sys/class/net/csap0/ifindex' else original_exists(path)
            for name, replacement in [('RUNTIME', runtime), ('preflight', lambda: dict(BASE)), ('identity', lambda: owner),
                                      ('run', command), ('active', lambda name: name in units), ('isolation', lambda: events.append(('isolate',))),
                                      ('check_ap', lambda *_, **kw: events.append(('check-ap', kw.get('enabled', False)))),
                                      ('check_unchanged', lambda _: events.append(('check-original',))),
                                      ('load_state', lambda: json.loads((runtime / 'state.json').read_text()) if runtime.exists() else None)]:
                stack.enter_context(patch.object(hotspot, name, replacement))
            stack.enter_context(patch.object(hotspot.os, 'fchown'))
            stack.enter_context(patch.object(hotspot.secrets, 'choice', side_effect=choose))
            stack.enter_context(patch.object(hotspot.time, 'sleep'))
            stack.enter_context(patch.object(Path, 'read_text', read))
            stack.enter_context(patch.object(Path, 'exists', exists))
            stack.enter_context(patch.object(hotspot.subprocess, 'run', side_effect=AssertionError('real process forbidden')))
            with redirect_stdout(io.StringIO()) as output:
                if fail:
                    with self.assertRaises(hotspot.HotspotError):
                        hotspot.up()
                else:
                    hotspot.up()
                    for path in secret.iterdir():
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(secret.stat().st_mode & 0o777, 0o700)
                    (runtime / 'leases').write_text('SYNTHETIC_LEASE')
                    hotspot.down()
                self.assertFalse(runtime.exists())
                self.assertFalse(secret.exists() and list(secret.iterdir()))
                self.assertNotIn('A' * 24, output.getvalue())
                # A second cleanup must not touch unrelated interfaces/services.
                before = len(events)
                hotspot.down()
                self.assertEqual(len(events), before)
        for event in events:
            if event and event[0] in ('iw', 'ip', 'nmcli', 'systemctl', 'systemd-run'):
                self.assertNotIn('A' * 24, repr(event))
                self.assertNotIn('wlan0', event)  # mutations must target AP only
                self.assertFalse(any(v in event for v in ('nft', 'iptables', 'reload', 'reconnect', 'disconnect', 'netns')))
        return events

    def test_up_order_and_idempotent_cleanup_preserve_sta(self):
        events = self.exercise_lifecycle()
        create = next(i for i, e in enumerate(events) if e[:2] == ('iw', 'phy'))
        first_password = events.index(('synthetic-password',))
        self.assertLess(first_password, create)
        isolate = events.index(('isolate',))
        link_up = events.index(('ip', 'link', 'set', 'dev', 'csap0', 'up'))
        self.assertLess(isolate, link_up)
        self.assertIn(('nmcli', 'device', 'set', 'csap0', 'managed', 'yes'), events)
        self.assertIn(('iw', 'dev', 'csap0', 'del'), events)
        self.assertIn(('check-original',), events)

    def test_timer_failure_generates_no_password_and_no_interface(self):
        events = self.exercise_lifecycle('timer')
        self.assertFalse(any(e[0] in ('synthetic-password', 'iw', 'ip', 'nmcli') for e in events))

    def test_partial_service_failure_also_removes_private_files_and_ap(self):
        events = self.exercise_lifecycle('dhcp')
        self.assertIn(('iw', 'dev', 'csap0', 'del'), events)

    def test_up_refuses_failed_preflight_before_any_mutation(self):
        with patch.object(hotspot, 'preflight', return_value=dict(problems=['fixture conflict'])), \
             patch.object(hotspot, 'identity', side_effect=AssertionError('mutation forbidden')):
            with self.assertRaises(hotspot.HotspotError):
                hotspot.up()

    def test_private_directory_refuses_existing_insecure_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'grokbot'
            parent.mkdir()
            owner = dict(uid=os.getuid(), gid=os.getgid(), home=directory)
            secret = parent / 'secret'
            secret.mkdir(mode=0o755)
            with self.assertRaises(hotspot.HotspotError):
                hotspot.prepare_secret_directory(owner)
            self.assertEqual(secret.stat().st_mode & 0o777, 0o755)
            self.assertEqual(list(secret.iterdir()), [])

    def test_monitor_schedules_rollback_without_touching_sta(self):
        with patch.object(hotspot, 'load_state', return_value=dict(baseline=BASE)), \
             patch.object(hotspot, 'check_ap', side_effect=hotspot.HotspotError('channel changed')), \
             patch.object(hotspot, 'run') as command, patch.dict(os.environ), redirect_stdout(io.StringIO()):
            self.assertEqual(hotspot.monitor(), 1)
        command.assert_called_once_with('systemctl', 'start', '--no-block', 'canscope-test-rollback.service')

    def test_down_does_not_delete_replacement_interface_and_retains_retry_state(self):
        state = dict(owner=dict(uid=os.getuid(), gid=os.getgid(), home='/synthetic-only'),
                     baseline=BASE, created=True, ifindex=99, nm_changed=True, nm_before='yes', private_files=[])
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(hotspot, 'RUNTIME', Path(directory)), patch.object(hotspot, 'load_state', return_value=state), \
             patch.object(Path, 'exists', return_value=True), patch.object(Path, 'read_text', return_value='100'), \
             patch.object(hotspot, 'run') as command, patch.object(hotspot, 'active', return_value=False), \
             patch.object(hotspot, 'check_unchanged'), redirect_stdout(io.StringIO()):
            with self.assertRaises(hotspot.HotspotError):
                hotspot.down()
            self.assertFalse(any(c.args[0] in ('iw', 'ip', 'nmcli') for c in command.call_args_list))
            self.assertNotIn(('systemctl', 'stop', 'canscope-test-rollback.timer'), [c.args for c in command.call_args_list])

    def test_public_up_requires_root_and_suppresses_backend_exception(self):
        with patch.object(sys, 'argv', ['test-hotspot.py', 'up']), patch.object(hotspot.os, 'geteuid', return_value=1000), \
             patch.object(hotspot, 'up', side_effect=AssertionError('forbidden')), redirect_stderr(io.StringIO()) as out:
            self.assertEqual(hotspot.main(), 1)
        self.assertIn('sudo', out.getvalue())
        with patch.object(sys, 'argv', ['test-hotspot.py', 'status']), \
             patch.object(hotspot, 'status', side_effect=ValueError('SYNTHETIC_PRIVATE')), \
             redirect_stderr(io.StringIO()) as out:
            self.assertEqual(hotspot.main(), 1)
        self.assertNotIn('SYNTHETIC_PRIVATE', out.getvalue())


if __name__ == '__main__':
    unittest.main()
