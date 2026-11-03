#!/usr/bin/env python3
"""临时 CANScope-Test 热点。preflight/status 只读；up/down 由用户用 sudo 运行。

主网络命名空间、同 PHY 同信道、20 MHz；不修改 wlan0 配置、全局转发或防火墙。
up 会创建 csap0 和私有密码文件；20 分钟后自动 down。不要用 root 运行配网桌面端。
"""
import argparse
import configparser
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import resource
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time

AP = 'csap0'
STA = 'wlan0'
SSID = 'CANScope-Test'
NET = ipaddress.IPv4Network('10.77.0.0/24')
RUNTIME = Path('/run/canscope-hotspot')
PREFIX = 'canscope-test-'
MAC = r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}'
TOOLS = ('iw', 'ip', 'nmcli', 'NetworkManager', 'hostapd', 'dnsmasq', 'systemd-run', 'systemctl')
NM_RULE = Path('/run/NetworkManager/conf.d/canscope-test-unmanaged.conf')
# Append rather than replacing pre-existing unmanaged-device lists.
NM_RULE_TEXT = '[keyfile]\nunmanaged-devices+=interface-name:csap0\n'
STEP = '参数检查'
# Fixed paths/values only: diagnostics must never read arbitrary configuration.
AP_ISOLATION = {
    'ipv4/conf/csap0/forwarding': 0,
    'ipv6/conf/csap0/forwarding': 0,
    'ipv6/conf/csap0/disable_ipv6': 1,
}
GLOBAL_FORWARDING = ('ipv4/ip_forward', 'ipv4/conf/all/forwarding',
                     'ipv4/conf/default/forwarding', 'ipv6/conf/all/forwarding',
                     'ipv6/conf/default/forwarding')


class HotspotError(Exception):
    """Only fixed messages. Never include subprocess output or private file data."""


def run(*args, required=True, timeout=20):
    result = subprocess.run(list(args), stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=timeout, env={'PATH': '/usr/bin:/usr/sbin', 'LC_ALL': 'C'})
    if required and result.returncode:
        raise HotspotError('系统命令失败；未输出后端正文，请用 status 检查安全状态。')
    return result.stdout if result.returncode == 0 else ''


def data(*args):
    return json.loads(run(*args))


def active(name):
    return run('systemctl', 'is-active', name, required=False).strip() == 'active'


def radio(info, require_24=True):
    phy = re.search(r'\bwiphy (\d+)', info)
    channel = re.search(r'channel (\d+) \((\d+) MHz\), width: (\d+) MHz', info)
    if not phy or not channel or not re.search(r'\btype managed\b', info):
        raise HotspotError('wlan0 未连接或无法识别当前信道/带宽。')
    ch, freq, width = map(int, channel.groups())
    if require_24 and (not 2412 <= freq <= 2472 or not 1 <= ch <= 13):
        raise HotspotError('wlan0 必须已连接 2.4 GHz；5 GHz 时拒绝启动，不会切换连接。')
    if require_24 and width != 20:
        raise HotspotError('本测试要求 wlan0 当前为 20 MHz；不会更改现有连接带宽。')
    return dict(phy='phy' + phy[1], channel=ch, frequency=freq, width=width)


def same_channel_combination(info, devices, phy):
    section = info.split('valid interface combinations:', 1)
    if len(section) != 2:
        return False
    counts = {'AP': 1}
    current_phy = None
    for line in devices.splitlines():
        found = re.match(r'phy#(\d+)', line)
        if found:
            current_phy = 'phy' + found[1]
        kind = re.match(r'\s*type (.+)', line)
        if current_phy == phy and kind:
            counts[kind[1]] = counts.get(kind[1], 0) + 1
    for block in re.split(r'\n\s*\* ', section[1]):
        total = re.search(r'total <= (\d+)', block)
        channels = re.search(r'#channels <= (\d+)', block)
        limits = re.findall(r'#\{\s*([^}]+)\}\s*<=\s*(\d+)', block)
        covered, valid = set(), True
        for group, limit in limits:
            kinds = {v.strip() for v in group.split(',')}
            covered.update(kinds)
            valid &= sum(counts.get(k, 0) for k in kinds) <= int(limit)
        if (total and channels and valid and set(counts) <= covered
                and sum(counts.values()) <= int(total[1]) and int(channels[1]) >= 1):
            return True
    return False


def snapshot(require_24=True):
    info = radio(run('iw', 'dev', STA, 'info'), require_24=require_24)
    link = run('iw', 'dev', STA, 'link')
    bssid = re.search(r'Connected to (' + MAC + ')', link)
    if not bssid:
        raise HotspotError('wlan0 当前未关联；不建立热点。')
    addresses = data('ip', '-j', '-4', 'address', 'show', 'dev', STA)
    ips = sorted(v['local'] for row in addresses for v in row.get('addr_info', []) if v['family'] == 'inet')
    wired = data('ip', '-j', '-4', 'address', 'show')
    shared = any(v.get('local') == '10.42.0.1' for row in wired for v in row.get('addr_info', []))
    shared_link = [dict(interface=row['ifname'], state=row.get('operstate'), flags=row.get('flags', []),
                        prefix=v['prefixlen']) for row in wired for v in row.get('addr_info', [])
                   if v.get('local') == '10.42.0.1']
    return dict(info, bssid=bssid[1].lower(), ipv4=ips,
                connection=run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', STA).strip(),
                default=data('ip', '-j', '-4', 'route', 'show', 'default'), shared_10_42_0_1=shared,
                shared_link=shared_link)


def preflight():
    missing = [name for name in TOOLS if not shutil.which(name)]
    if missing:
        raise HotspotError('缺少热点依赖；请检查 iw/ip/nmcli/NetworkManager/hostapd/dnsmasq/systemd-run/systemctl。')
    baseline = snapshot()
    phy = baseline['phy']
    info = run('iw', 'phy', phy, 'info')
    devices = run('iw', 'dev')
    problems = []
    if NM_RULE.exists() or NM_RULE.is_symlink():
        problems.append('测试 NM 临时规则已存在；先检查旧会话，不覆盖未知文件。')
    if not same_channel_combination(info, devices, phy):
        problems.append('当前接口数量/类型不满足 STA+AP 同信道组合。')
    frequency = re.search(r'^\s*\* ' + str(baseline['frequency']) + r'(?:\.0)? MHz[^\n]*', info, re.M)
    if not frequency or re.search(r'disabled|no IR|radar', frequency[0], re.I):
        problems.append('当前信道不允许主动 AP 发射。')
    if re.search(r'\bInterface ' + AP + r'\b', devices):
        problems.append('csap0 已存在；不复用或删除未知接口。')
    if not active('NetworkManager'):
        problems.append('NetworkManager 未运行。')
    if run('nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', STA).strip() != 'yes':
        problems.append('wlan0 当前不由 NM 管理。')
    for row in data('ip', '-j', '-4', 'route', 'show', 'table', 'all'):
        dst = row.get('dst', 'default')
        if dst != 'default':
            try:
                if ipaddress.IPv4Network(dst, strict=False).overlaps(NET):
                    problems.append('10.77.0.0/24 与现有路由重叠。')
            except ValueError:
                pass
    for row in data('ip', '-j', '-4', 'address', 'show'):
        for address in row.get('addr_info', []):
            if address.get('family') == 'inet' and ipaddress.IPv4Interface(
                    f"{address['local']}/{address['prefixlen']}").network.overlaps(NET):
                problems.append('10.77.0.0/24 与现有地址重叠。')
    for unit in ('hostapd', 'dhcp', 'monitor', 'rollback.timer', 'rollback.service'):
        if active(PREFIX + unit if '.' in unit else PREFIX + unit + '.service'):
            problems.append('同名测试单元已运行；请先检查 status/down。')
    driver = Path('/sys/class/net/wlan0/device/driver')
    baseline['driver'] = driver.resolve().name if driver.exists() else 'unknown'
    pci = Path('/sys/class/net/wlan0/device')
    baseline['pci_id'] = ':'.join((pci / name).read_text().strip().removeprefix('0x')
                                for name in ('vendor', 'device')) if (pci / 'vendor').exists() else 'unknown'
    baseline['regdomain'] = re.findall(r'country ([A-Z0-9]{2}):', run('iw', 'reg', 'get'))
    baseline['iwd_active'] = active('iwd')
    baseline['global_ip_forward'] = Path('/proc/sys/net/ipv4/ip_forward').read_text().strip()
    baseline['same_channel_sta_ap'] = not any('组合' in p for p in problems)
    baseline['dependencies'] = {v: bool(shutil.which(v)) for v in (*TOOLS, 'nft')}
    baseline['wireless_phys'] = sorted(p.name for p in Path('/sys/class/ieee80211').glob('phy*'))
    baseline['existing_dhcp_listener_possible'] = ':67' in run('ss', '-H', '-lun', required=False)
    baseline['problems'] = sorted(set(problems))
    baseline['ready_for_user_up'] = not problems
    baseline['note'] = '只读；未创建接口/密码。up 仍须验证 NM、同信道、专用 DHCP 绑定与 AP 路由；不改防火墙。'
    return baseline


def identity():
    uid = int(os.environ.get('SUDO_UID', os.getuid()))
    if uid == 0:
        raise HotspotError('请由普通用户通过 sudo 运行 up；不能使用 root 的主目录。')
    person = pwd.getpwuid(uid)
    return dict(uid=uid, gid=person.pw_gid, home=person.pw_dir)


def private_write(path, value, uid=0, gid=0):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, 'w', closefd=False) as stream:
            stream.write(value)
    finally:
        os.close(fd)


def save_state(state):
    temporary = RUNTIME / 'state.new'
    private_write(temporary, json.dumps(state))
    temporary.replace(RUNTIME / 'state.json')


def nm_config():
    # Read-only parser output stays in RAM; never log the complete configuration.
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(run('NetworkManager', '--print-config'))
    return {(section, key): value for section in parser.sections()
            for key, value in parser.items(section, raw=True)}


def install_nm_rule(state):
    before = nm_config()
    # Stage on /run and journal its inode before linking into NM's config dir.
    # A hard link avoids the crash window between creating a file and recording
    # its ownership; never overwrite or delete another session's rule.
    staged = RUNTIME / 'nm-unmanaged.conf'
    private_write(staged, NM_RULE_TEXT)
    info = staged.stat()
    state['nm_rule_inode'] = [info.st_dev, info.st_ino]
    save_state(state)
    NM_RULE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.link(staged, NM_RULE, follow_symlinks=False)
    after = nm_config()
    key = ('keyfile', 'unmanaged-devices')
    old = [v.strip() for v in re.split('[;,]', before.get(key, '')) if v.strip()]
    new = [v.strip() for v in re.split('[;,]', after.get(key, '')) if v.strip()]
    expected = old if 'interface-name:csap0' in old else old + ['interface-name:csap0']
    before.pop(key, None)
    after.pop(key, None)
    if before != after or new != expected:
        raise HotspotError('NM 临时规则未按预期生效或被其他配置覆盖；不重载，停止测试。')
    state['nm_rule_applied'] = True
    reload_nm_conf(state)
    check_unchanged(state['baseline'])


def reload_nm_conf(state):
    state['nm_reload_pending'] = True
    save_state(state)
    # CONF only: no SIGHUP, DNS reload, profile reload or service restart.
    run('nmcli', 'general', 'reload', 'conf')
    state['nm_reload_pending'] = False
    save_state(state)


def remove_nm_rule(state):
    identity = state.get('nm_rule_inode')
    if not identity:
        return
    if NM_RULE.exists() or NM_RULE.is_symlink():
        info = NM_RULE.lstat()
        if not stat.S_ISREG(info.st_mode) or [info.st_dev, info.st_ino] != identity:
            raise HotspotError('测试 NM 规则已被替换；拒绝删除其他文件，保留回滚状态。')
        state['nm_reload_pending'] = state.get('nm_rule_applied', False) or state.get('nm_reload_pending', False)
        save_state(state)
        NM_RULE.unlink()
    if state.get('nm_reload_pending'):
        reload_nm_conf(state)


def nm_ap_state(timeout=1):
    try:
        values = run('nmcli', '-g', 'GENERAL.NM-MANAGED,GENERAL.STATE,GENERAL.REASON',
                     'device', 'show', AP, required=False, timeout=timeout).strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        values = []
    managed = values[0] if values and values[0] in ('yes', 'no') else 'unknown'
    def number(index):
        match = re.match(r'([0-9]{1,3})(?:\s|$)', values[index]) if len(values) > index else None
        return int(match[1]) if match else None
    return dict(managed=managed, state=number(1), reason=number(2))


def nm_state_text(value):
    code = value['state']
    label = {10: 'unmanaged', 20: 'unavailable', 30: 'disconnected', 100: 'activated', 120: 'failed'}.get(code, '其他/未知')
    return f"NM managed={value['managed']} state={code}（{label}）reason={value['reason']}"


def wait_unmanaged(state):
    deadline = time.monotonic() + 5
    value = dict(managed='unknown', state=None, reason=None)
    for _ in range(20):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        value = nm_ap_state(timeout=min(1, remaining))
        if value['managed'] == 'no' and value['state'] == 10:
            return
        if value['managed'] != 'unknown' and not state['nm_changed']:
            state['nm_changed'], state['nm_before'] = True, 'reset'
            save_state(state)
            try:
                run('nmcli', 'device', 'set', AP, 'managed', 'no', timeout=1)
            except (HotspotError, OSError, subprocess.TimeoutExpired):
                raise HotspotError('设置 csap0 managed no 失败；' + nm_state_text(value)) from None
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise HotspotError('等待 csap0 unmanaged 超时；' + nm_state_text(value) + '；停止测试，不改 wlan0/iwd。')


def load_state():
    if not RUNTIME.exists():
        return None
    info = RUNTIME.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise HotspotError('运行目录不是 root 所有的 0700 目录；拒绝操作。')
    return json.loads((RUNTIME / 'state.json').read_text())


def files(owner):
    directory = Path(owner['home']) / 'grokbot' / 'secret'
    return directory, {kind: directory / ('canscope-test.' + suffix) for kind, suffix in
                       (('password', 'password'), ('hostapd', 'hostapd.conf'), ('dhcp', 'dnsmasq.conf'))}


def prepare_secret_directory(owner):
    directory, paths = files(owner)
    parent = directory.parent
    if not parent.is_dir() or parent.is_symlink() or parent.stat().st_uid != owner['uid']:
        raise HotspotError('用户 grokbot 目录必须已存在且由本人所有，不能是符号链接。')
    if not directory.exists():
        directory.mkdir(mode=0o700)
        os.chown(directory, owner['uid'], owner['gid'])
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner['uid'] or stat.S_IMODE(info.st_mode) != 0o700:
        raise HotspotError('secret 目录必须由本人所有且权限恰好 0700；不会修改既有目录权限。')
    if any(p.exists() or p.is_symlink() for p in paths.values()):
        raise HotspotError('测试私有文件已存在；先检查旧会话，不覆盖文件。')
    return paths


def configurations(channel, password):
    hostapd = f'''interface=csap0
driver=nl80211
ssid=CANScope-Test
hw_mode=g
channel={channel}
ieee80211n=1
ht_capab=
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
ieee80211w=0
wpa_passphrase={password}
max_num_sta=4
ap_isolate=1
logger_syslog=0
logger_stdout=127
logger_stdout_level=2
'''
    dhcp = f'''interface=csap0
listen-address=10.77.0.1
bind-interfaces
port=0
no-resolv
no-hosts
dhcp-range=10.77.0.20,10.77.0.29,255.255.255.0,30m
dhcp-option=option:router
dhcp-option=option:dns-server
dhcp-leasefile={RUNTIME}/leases
dhcp-lease-max=4
log-dhcp
log-facility=-
user=root
group=root
'''
    return hostapd, dhcp


def service(name, *command):
    run('systemd-run', '--quiet', '--collect', '--unit=' + PREFIX + name,
        '--property=Restart=no', '--property=LimitCORE=0', '--property=UMask=0077',
        '--property=TimeoutStopSec=10', *command)


class IsolationError(HotspotError):
    """Safe, structured per-interface sysctl diagnostic, with no daemon payload."""


def read_switch(relative):
    try:
        value = Path('/proc/sys/net', relative).read_text().strip()
    except OSError:
        return None
    # Even a mocked/corrupt proc value cannot inject private text into errors.
    if re.fullmatch(r'-?[0-9]{1,10}', value):
        number = int(value)
        if -(2**31) <= number < 2**31:
            return number
    return None


def isolation_values():
    return {key: read_switch(key) for key in AP_ISOLATION}


def switch_text(value):
    return str(value) if type(value) is int and -(2**31) <= value < 2**31 else '不可读/无效'


def sysctl_name(relative):
    return 'net.' + relative.replace('/', '.')


def check_isolation(context, previous=None):
    current = isolation_values()
    wrong = [key for key, expected in AP_ISOLATION.items() if current[key] != expected]
    if wrong:
        changes = []
        for key in wrong:
            before = switch_text(previous[key]) if previous and key in previous else '未记录'
            changes.append(f'{sysctl_name(key)}：上次={before} 当前={switch_text(current[key])} 期望={AP_ISOLATION[key]}')
        globals_text = '，'.join(f'{sysctl_name(key)}={switch_text(read_switch(key))}' for key in GLOBAL_FORWARDING)
        raise IsolationError(f'csap0 隔离检查失败（{context}）；' + '；'.join(changes) +
                             '；全局只读参考：' + globals_text + '；停止测试，不修改全局值。')
    return current


def isolation():
    # Only this newly created interface. Never change all/default/global knobs.
    values = {**AP_ISOLATION, 'ipv4/conf/csap0/send_redirects': 0,
              'ipv4/conf/csap0/accept_redirects': 0, 'ipv6/conf/csap0/accept_ra': 0}
    for relative, value in values.items():
        try:
            Path('/proc/sys/net', relative).write_text(str(value) + '\n')
        except OSError:
            raise IsolationError('csap0 隔离写入失败：' + sysctl_name(relative) +
                                 f'，期望={value} 当前={switch_text(read_switch(relative))}；停止测试。') from None
    return check_isolation('写入后')


def settle_isolation(context):
    """Bounded startup settling after link/daemon transitions; no global writes.

    The initial pre-link write is insufficient if IPv4 state is recreated or a
    queued manager update restores the default. Runtime monitoring stays strict:
    it checks and rolls back instead of fighting another writer indefinitely.
    """
    for _ in range(3):
        try:
            before = isolation_values()
            previous = isolation()
            time.sleep(0.2)
            current = check_isolation(context, previous)
            changes = [f'{sysctl_name(key)} {switch_text(before[key])} -> {current[key]}'
                       for key in AP_ISOLATION if before[key] != current[key]]
            if changes:
                print(f'csap0 隔离已重新施加（{context}）：' + '；'.join(changes), flush=True)
            return current
        except IsolationError as exc:
            failure = exc
    raise failure


def check_unchanged(baseline):
    current = snapshot()
    for key in ('phy', 'channel', 'frequency', 'width', 'bssid', 'ipv4', 'connection', 'default', 'shared_10_42_0_1', 'shared_link'):
        if current.get(key) != baseline.get(key):
            raise HotspotError('wlan0、默认路由或 10.42.0.1 与启动前不同；只回滚测试热点，不切换现有连接。')


def check_ap(state, enabled=False):
    check_unchanged(state['baseline'])
    check_isolation('运行检查', state.get('isolation'))
    nm_state = nm_ap_state()
    if nm_state['managed'] != 'no' or nm_state['state'] != 10:
        raise HotspotError('NM 未放弃 csap0 管理；' + nm_state_text(nm_state) + '；停止测试，不改 wlan0/iwd。')
    ap = run('iw', 'dev', AP, 'info')
    if not re.search(r'\btype AP\b', ap):
        raise HotspotError('csap0 类型异常；可能存在管理器争用，停止测试。')
    frequency = re.search(r'channel (\d+) \((\d+) MHz\), width: (\d+) MHz', ap)
    if enabled and not frequency:
        raise HotspotError('AP 尚未在预期信道工作；停止测试。')
    if frequency and tuple(map(int, frequency.groups())) != (
            state['baseline']['channel'], state['baseline']['frequency'], 20):
        raise HotspotError('AP 没有保持 STA 同信道、20 MHz；停止测试。')
    uid = str(state.get('owner', {}).get('uid', os.getuid()))
    for address, protocol in (('10.77.0.20', 'udp'), ('10.77.0.255', 'udp'), ('10.77.0.20', 'tcp')):
        routes = data('ip', '-j', '-4', 'route', 'get', address, 'ipproto', protocol, 'dport', '32323', 'uid', uid)
        if not routes or routes[0].get('dev') != AP or routes[0].get('prefsrc') != '10.77.0.1':
            raise HotspotError('热点 UDP/TLS 路由被 VPN 或策略路由接管；不修改主路由/防火墙，停止测试。')


def up():
    global STEP
    STEP = '启动前只读检查'
    baseline = preflight()
    if baseline['problems']:
        raise HotspotError('preflight 未通过；请先单独运行 preflight 查看固定提示。')
    owner = identity()
    # Refuse credentials inside ANY Git checkout, including a symlinked home.
    directory, _ = files(owner)
    if any((p / '.git').exists() for p in (directory.resolve(), *directory.resolve().parents)):
        raise HotspotError('私有目录位于 Git 仓库中；拒绝生成密码。')
    RUNTIME.mkdir(mode=0o700)  # EEXIST is intentional: never take over a session.
    state = dict(owner=owner, baseline=baseline, created=False, ifindex=None,
                 nm_changed=False, nm_before=None, private_files=[])
    save_state(state)
    helper = RUNTIME / 'helper.py'
    private_write(helper, Path(__file__).read_text())
    try:
        STEP = '安排 root 自动回滚'
        run('systemd-run', '--quiet', '--collect', '--unit=' + PREFIX + 'rollback',
            '--on-active=20m', '--timer-property=AccuracySec=1s',
            '--property=LimitCORE=0', '--property=Restart=on-failure', '--property=RestartSec=15s',
            '/usr/bin/python', '-I', str(helper), 'down')
        if not active(PREFIX + 'rollback.timer'):
            raise HotspotError('自动回滚定时器未就绪；拒绝创建热点。')
        STEP = '创建接口前安装临时 NM unmanaged 规则'
        install_nm_rule(state)
        STEP = '生成私有临时配置'
        paths = prepare_secret_directory(owner)
        # Persist intentions first so a partial write is cleaned by down/watchdog.
        state['private_files'] = [str(p) for p in paths.values()]
        save_state(state)
        password = ''
        hostapd = ''
        try:
            password = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789') for _ in range(24))
            private_write(paths['password'], password + '\n', owner['uid'], owner['gid'])
            hostapd, dhcp = configurations(baseline['channel'], password)
            private_write(paths['hostapd'], hostapd, owner['uid'], owner['gid'])
            private_write(paths['dhcp'], dhcp, owner['uid'], owner['gid'])
        finally:
            password = hostapd = ''
        STEP = '创建专用 AP 接口'
        # No namespace movement or wlan0/iwd changes. Rule is already loaded.
        run('iw', 'phy', baseline['phy'], 'interface', 'add', AP, 'type', '__ap')
        state['ifindex'] = int(Path('/sys/class/net/csap0/ifindex').read_text())
        state['created'] = True
        save_state(state)
        STEP = '等待 NM 确认 csap0 unmanaged'
        wait_unmanaged(state)
        isolation()  # Before link up, address assignment and hostapd.
        run('ip', 'address', 'add', '10.77.0.1/24', 'dev', AP)
        run('ip', 'link', 'set', 'dev', AP, 'up')
        STEP = '接口启用后校验 csap0 隔离'
        state['isolation'] = settle_isolation('接口启用后')
        save_state(state)
        STEP = '接口启用后检查 AP/NM 状态'
        check_ap(state)
        STEP = '启动专用 hostapd/dnsmasq'
        service('hostapd', '/usr/bin/python', '-I', str(helper), '_serve', 'hostapd')
        service('dhcp', '/usr/bin/python', '-I', str(helper), '_serve', 'dhcp')
        time.sleep(2)
        if not all(active(PREFIX + name + '.service') for name in ('hostapd', 'dhcp')):
            raise HotspotError('专用 AP/DHCP 服务未启动；可能端口或管理权冲突，已安排回滚。')
        STEP = '专用服务启动后校验 csap0 隔离'
        state['isolation'] = settle_isolation('专用服务启动后')
        save_state(state)
        STEP = '专用服务启动后检查 AP/NM 状态'
        check_ap(state, enabled=True)
        service('monitor', '/usr/bin/python', '-I', str(helper), '_monitor')
        print('热点启动：CANScope-Test；10.77.0.1/24；WPA2-CCMP；同信道 20 MHz；20 分钟自动停止。')
        print('密码只写入用户 secret 下的 0600 文件；请用普通用户运行配网脚本读取，不要 cat。')
    except BaseException:
        down()
        raise


def down():
    """Only owned units/files/interface. Best effort all steps; retain state on error."""
    state = load_state()
    if state is None:
        print('没有本脚本拥有的热点会话；未操作任何接口。')
        return
    failures = []
    interface_removed = True
    # Do not kill this process if it is the rollback.service/monitor itself.
    current_unit = os.environ.get('CANSCOPE_UNIT', '')
    for name in ('monitor.service', 'hostapd.service', 'dhcp.service'):
        if name != current_unit:
            try:
                run('systemctl', 'stop', PREFIX + name, required=False)
                if active(PREFIX + name):
                    raise HotspotError('测试单元未停止。')
            except Exception:
                failures.append('停止测试单元失败。')
    path = Path('/sys/class/net/csap0/ifindex')
    if state['created'] and path.exists():
        try:
            index = int(path.read_text())
            if state['ifindex'] is None or index != state['ifindex']:
                raise HotspotError('csap0 已被其他会话替换；拒绝删除。')
            run('ip', 'link', 'set', 'dev', AP, 'down')
            if state['nm_changed']:
                run('nmcli', 'device', 'set', AP, 'managed', state['nm_before'])
            run('iw', 'dev', AP, 'del')
        except Exception:
            interface_removed = False
            failures.append('csap0 或 NM 管理状态回滚失败。')
    if interface_removed:
        try:
            remove_nm_rule(state)
        except Exception as exc:
            failures.append(str(exc) if isinstance(exc, HotspotError) else '测试 NM 临时规则回滚/重载失败。')
    # Only the three fixed filenames of this owner, never arbitrary JSON paths.
    _, expected = files(state['owner'])
    for path in expected.values():
        if str(path) in state['private_files']:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                failures.append('私有测试文件删除失败。')
    (RUNTIME / 'leases').unlink(missing_ok=True)
    try:
        check_unchanged(state['baseline'])
        print('回滚检查：wlan0 关联/信道/IP、默认路由、10.42.0.1 与启动前一致。')
    except Exception:
        print('回滚检查未通过：现有网络与启动前不同或查询失败；未自动重连或切换，请用户检查。')
    if failures:
        for message in sorted(set(failures)):
            print(message)
        raise HotspotError('回滚存在未完成项；保留 root 状态，修复后可重跑 down。')
    run('systemctl', 'stop', PREFIX + 'rollback.timer', required=False)
    if active(PREFIX + 'rollback.timer'):
        raise HotspotError('回滚定时器尚未停止；保留状态以便重跑 down。')
    shutil.rmtree(RUNTIME)
    print('测试热点已停止；临时密码、配置、租约和运行状态已删除。')


def safe_event(kind, line):
    """Translate allowlisted events; never print raw daemon output or hostnames."""
    if kind == 'hostapd':
        # Reconstruct events from a known prefix, never echo any daemon text.
        control = re.match(r'^(?:csap0: )?(AP-ENABLED|AP-DISABLED|AP-STA-CONNECTED|AP-STA-DISCONNECTED)\b(?:\s+(' + MAC + r'))?', line)
        if control:
            label = {'AP-ENABLED': 'AP 已启用', 'AP-DISABLED': 'AP 已停止',
                     'AP-STA-CONNECTED': '客户端连接', 'AP-STA-DISCONNECTED': '客户端断开'}[control[1]]
            if control[1].startswith('AP-STA-'):
                return label + ' MAC=' + control[2].lower() if control[2] else None
            return label
        sta = re.match(r'^csap0: STA (' + MAC + r') (?:IEEE 802\.11|WPA): (.*)$', line)
        if not sta:
            return None
        message = sta[2]
        labels = (
            (r'^deauth(?:enticated|entication|enticate)?\b', '去认证'),
            (r'^disassoc(?:iated|iation|iate)?\b', '解除关联'),
            (r'^(?:did not acknowledge|not ACKed) authentication response\b|^auth(?:entication)? response\b.*\bnot ACKed\b', '认证响应未 ACK'),
            (r'^(?:did not acknowledge|not ACKed) association response\b|^assoc(?:iation)? response\b.*\bnot ACKed\b', '关联响应未 ACK'),
            (r'^(?:auth(?:entication)?\b.*\b(?:fail\w*|reject\w*|denied|timeout|timed out)|not allowed to authenticate\b)', '认证失败'),
            (r'^(?:assoc(?:iation)?\b.*\b(?:fail\w*|reject\w*|denied|timeout|timed out)|not allowed to associate\b)', '关联失败'),
            (r'^authenticated\b', '认证成功'),
            (r'^associated\b', '关联成功'),
            (r'^pairwise key handshake completed\b', '四次握手完成'),
        )
        for pattern, label in labels:
            if re.search(pattern, message, re.IGNORECASE):
                result = label + ' MAC=' + sta[1].lower()
                for field in ('status', 'reason'):
                    code = re.search(r'\b' + field + r'(?:_code| code)?\s*(?:[=:]\s*|\s+)([0-9]{1,5})(?![0-9])', message)
                    if code and int(code[1]) <= 65535:
                        result += ' ' + field + '=' + str(int(code[1]))
                return result
    elif kind == 'dhcp':
        address = re.search(MAC, line)
        event = re.search(r'\b(DHCPDISCOVER|DHCPOFFER|DHCPREQUEST|DHCPACK|DHCPNAK)\(csap0\)', line)
        if event:
            ip = re.search(r'\b10\.77\.0\.\d{1,3}\b', line)
            return event[1] + (' MAC=' + address[0].lower() if address else '') + (' IP=' + ip[0] if ip else '')
    return None


def serve(kind):
    state = load_state()
    _, paths = files(state['owner'])
    command = (['/usr/bin/hostapd', str(paths[kind])] if kind == 'hostapd' else
               ['/usr/bin/dnsmasq', '--keep-in-foreground', '--conf-file=' + str(paths[kind])])
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env={'PATH': '/usr/bin:/usr/sbin', 'LC_ALL': 'C'}) as process:
        def terminate(*_):
            process.terminate()
        signal.signal(signal.SIGTERM, terminate)
        for line in process.stdout:
            event = safe_event(kind, line)
            if event:
                print(event, flush=True)
        code = process.wait()
        print('专用服务退出，code=' + str(code), flush=True)
        return code


def monitor():
    # This is the worker's own marker, unrelated to any credentials.
    os.environ['CANSCOPE_UNIT'] = 'monitor.service'
    while True:
        try:
            state = load_state()
            check_ap(state, enabled=True)
            if not all(active(PREFIX + name + '.service') for name in ('hostapd', 'dhcp')):
                raise HotspotError('专用服务退出。')
        except Exception as exc:
            detail = str(exc) if isinstance(exc, HotspotError) else '系统检查失败，后端正文已隐藏。'
            print('监控条件变化；停止测试热点，不重连 wlan0。' + detail, flush=True)
            # Separate rollback service; stopping monitor must not abort cleanup.
            run('systemctl', 'start', '--no-block', PREFIX + 'rollback.service')
            return 1
        time.sleep(2)


def status():
    # Ordinary user can inspect public interface/service metadata without secrets.
    print('热点接口存在：' + str(Path('/sys/class/net/csap0').exists()))
    print('测试 NM 临时规则存在：' + str(NM_RULE.exists()))
    print(nm_state_text(nm_ap_state()))
    for name in ('hostapd.service', 'dhcp.service', 'monitor.service', 'rollback.timer'):
        print(name + ' active=' + str(active(PREFIX + name)))
    print(json.dumps(snapshot(require_24=False), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'up', 'status', 'down', '_serve', '_monitor'))
    parser.add_argument('worker', nargs='?', choices=('hostapd', 'dhcp'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        if args.action == 'preflight':
            value = preflight()
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0 if value['ready_for_user_up'] else 1
        if args.action == 'status':
            status()
            return 0
        if os.geteuid() != 0:
            raise HotspotError('只有 up/down 和内部服务需要 sudo；preflight/status 请用普通用户。')
        if args.action == '_serve':
            return serve(args.worker)
        if args.action == '_monitor':
            return monitor()
        # Serialize manual/timer cleanup without using user-writable lock paths.
        fd = os.open('/run/canscope-hotspot.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            up() if args.action == 'up' else down()
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        # No args/repr/traceback or arbitrary daemon output, even on bad configs.
        message = str(exc) if isinstance(exc, HotspotError) else '系统操作失败；后端正文已隐藏。'
        print(f'失败步骤：{STEP}；类型：{type(exc).__name__}；{message}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
