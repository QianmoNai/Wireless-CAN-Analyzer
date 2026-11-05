"""Opt-in physical Android CAN acceptance; requires a separate debug bench APK.

Prepare the analyzer's CAN/communication mode before running. This script uses
ADB and WebView CDP, adds simulator profiles only to an empty bench registry,
and enables only the STM32 simulator's status query. It never flashes devices.
"""
import argparse
import hashlib
import json
import os
import signal
import tempfile
from pathlib import Path
import re
import subprocess
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fixture', required=True, choices=['stm32h723'])
parser.add_argument('--serial', required=True, help='authorized ADB serial')
parser.add_argument('--package', default='dev.qianmo.canview.bench')
parser.add_argument('--mode', required=True, choices=['ble', 'wifi'])
parser.add_argument('--ble-address', default='90:DA:72:73:72:2E')
parser.add_argument('--ip', default='192.168.1.221')
parser.add_argument('--discover-wifi', action='store_true', help='require a new matching UDP response, beyond the cached-IP entry')
parser.add_argument('--all-dm-queries', action='store_true', help='query all four virtual DM motors at 100 Hz throughout the six phases')
parser.add_argument('--seconds', type=int, default=15)
parser.add_argument('--raw-seconds', type=int, default=0, help='override the raw-flow phase only, 5..600 seconds; zero uses --seconds')
parser.add_argument('--observe-rendering', action='store_true', help='record bounded callback/animation timing; not display frame rate')
parser.add_argument('--detail-window', choices=('0.1', '0.5', '1', '5', '10', '30', '60'), default='1', help='detail chart time window in seconds')
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]*\.bench', args.package):
    parser.error('use a separate .bench package with WebView debugging enabled')
if not 5 <= args.seconds <= 300:
    parser.error('--seconds must be 5..300')
if args.raw_seconds and not 5 <= args.raw_seconds <= 600:
    parser.error('--raw-seconds must be zero or 5..600')
if args.discover_wifi and args.mode != 'wifi':
    parser.error('--discover-wifi requires --mode wifi')
# Linux benches must not compete for one phone/channel. The OS releases this
# advisory lock even after an unexpected process exit. Other hosts retain the
# explicit already-connected guard below.
bench_lock = None
if os.name == 'posix':
    import fcntl
    lock_id = hashlib.sha256((args.serial + ':' + args.package).encode()).hexdigest()[:24]
    bench_lock = open(Path(tempfile.gettempdir()) / ('can-scope-android-' + lock_id + '.lock'), 'a')
    try:
        fcntl.flock(bench_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('another hardware test owns this phone/package; no ADB action was taken')


stop_requested = False


class TestInterrupted(Exception):
    pass


def interrupted(signum, frame):
    global stop_requested
    stop_requested = True


def check_stop():
    if stop_requested:
        raise TestInterrupted('test stop requested')


def pause(milliseconds):
    end = time.monotonic() + milliseconds / 1000
    while time.monotonic() < end:
        check_stop()
        # Let the CDP dispatcher finish normally before testing the stop flag.
        # Raising from its greenlet would leave cleanup unable to use the page.
        page.wait_for_timeout(min(100, max(0, (end-time.monotonic())*1000)))
    check_stop()


def wait(expression, *, arg=None, timeout=30000, stoppable=True):
    end = time.monotonic() + timeout / 1000
    while True:
        if stoppable:
            check_stop()
        if page.evaluate(expression, arg):
            return
        if time.monotonic() >= end:
            raise TimeoutError('WebView condition timed out')
        time.sleep(.05)


# Background shells may inherit SIGINT as ignored. Request cooperative stop;
# never interrupt Playwright in its dispatcher greenlet during a CDP call.
signal.signal(signal.SIGINT, interrupted)
signal.signal(signal.SIGTERM, interrupted)
adb = ['adb', '-s', args.serial]
report = dict(scope='real Android, physical CAN from STM32 simulator; status queries only',
              mode=args.mode, package=args.package, all_dm_queries=args.all_dm_queries, phases=[], js_errors=[], ok=False,
              harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
args.output.parent.mkdir(parents=True, exist_ok=True)


def save():
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def adb_text(*command):
    return subprocess.check_output(adb + list(command), timeout=15).decode().strip()


setup = """
commitWatch({profile:'c620',id:1,name:'模拟 M3508'});
commitWatch({profile:'c610',id:2,name:'模拟 M2006'});
commitWatch({profile:'gm6020',id:1,name:'模拟 GM6020'});
for(let id=1;id<=4;id++)commitWatch({profile:'dm4310',id,name:'模拟达妙 '+id,master:0x10+id,mode:'mit',limits:{p:12.5,v:30,t:10}});
commitWatch({profile:'dm_imu_l1',id:0x50,name:'模拟 IMU',feedbackId:0x60});
"""
port = None
channel_owned = False
render_observer_installed = False
try:
    pid = adb_text('shell', 'pidof', args.package)
    if not pid.isdecimal():
        raise RuntimeError('start the bench app before running this test')
    report.update(pid=pid, model=adb_text('shell', 'getprop', 'ro.product.model'),
                  android=adb_text('shell', 'getprop', 'ro.build.version.release'))
    apk = adb_text('shell', 'pm', 'path', args.package).removeprefix('package:')
    if re.fullmatch(r'/data/app/[A-Za-z0-9/_~.+=-]+/base\.apk', apk):
        report['installed_apk_sha256'] = adb_text('shell', 'sha256sum', apk).split()[0]
    port = int(adb_text('forward', 'tcp:0', 'localabstract:webview_devtools_remote_' + pid))
    report['source_sha256'] = {str(path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                              for path in ('android/app/src/main/java/dev/qianmo/canview/MainActivity.java',
                                           'android/app/src/main/java/dev/qianmo/canview/LanTransport.java',
                                           'android/app/src/main/java/dev/qianmo/canview/LanNetwork.java',
                                           'android/app/src/main/java/dev/qianmo/canview/LanSubnet.java',
                                           'android/app/src/main/java/dev/qianmo/canview/BleNotifications.java',
                                           'android/app/src/main/assets/motor_detail.js',
                                           'android/app/src/main/assets/monitor.js',
                                           'android/app/src/main/assets/network_settings.js',
                                           'android/app/src/main/assets/index.html')}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', no_defaults=True)
        pages = [page for context in browser.contexts for page in context.pages]
        page = next(page for page in pages if '/index.html' in page.url)
        home = next(page for page in pages if '/home.html' in page.url)
        page.on('pageerror', lambda error: report['js_errors'].append(str(error)))
        try:
            report['stage'] = 'setup'
            home.evaluate('HomeAndroid.openChannel(1)')
            wait('innerWidth>0')
            if page.evaluate('connected'):
                raise RuntimeError('disconnect the bench channel before running')
            if page.evaluate('watchedMotors.length') == 0:
                page.evaluate(setup)
            expected = [['c620', 1], ['c610', 2], ['gm6020', 1]] + [['dm4310', i] for i in range(1, 5)] + [['dm_imu_l1', 0x50]]
            if page.evaluate('watchedMotors.map(m=>[m.config.profile,m.config.id])') != expected:
                raise RuntimeError('bench registry differs; existing profiles were preserved')
            page.evaluate("""()=>{
              window.canBenchNativeBase ||= onNative;
              window.canBench = {errors:[], states:[], startedAt:performance.now(), modes:[], devices:[], initialNetworkIp:null, raw:0, canStatuses:[], streamCounts:{}, streamWindow:null};
              onNative=(kind,data)=>{
                canBenchNativeBase(kind,data);
                if(kind==='error')canBench.errors.push(data.message);
                if(kind==='state'){
                  canBench.states.push({value:data.value,at_ms:performance.now()-canBench.startedAt});
                  if(canBench.states.length>128)canBench.states.shift();
                }
                if(kind==='telemetryMode')canBench.modes.push(data.mode);
                if(kind==='networkMode'&&canBench.initialNetworkIp===null)canBench.initialNetworkIp=data.ip||'';
                if(kind==='device'&&(data.address||'').startsWith('wifi:'))canBench.devices.push(data.address);
                const rawPackets=kind==='packets'?(data.items||[]):kind==='motorSamples'?(data.packets||[]):[];
                for(const item of rawPackets){
                  const bytes=parsePacket(item.hex||item);
                  if(bytes&&bytes[1]===1){
                    canBench.raw++;
                    const id=u32(bytes,4),stream=id===0x60?id+':'+bytes[8]:String(id);
                    canBench.streamCounts[stream]=(canBench.streamCounts[stream]||0)+1;
                    if(canBench.streamWindow){
                      const w=canBench.streamWindow,now=performance.now(),old=w.seen[stream];
                      w.seen[stream]={last:now,maxGap:Math.max(old?old.maxGap:0,now-(old?old.last:w.started))};
                    }
                  }
                  if(bytes&&bytes[1]===4)canBench.canStatuses.push({errors:u32(bytes,12),drops:u32(bytes,16),state:bytes[2]});
                }
              };
              showTab('live');
            }""")
            report['stage'] = 'connect'
            channel_owned = True
            if args.mode == 'ble':
                page.evaluate("deviceMap.clear();invoke('connectionMode','ble');invoke('scan')")
                wait('(address)=>deviceMap.has(address)', arg=args.ble_address, timeout=25000)
                page.evaluate("address=>invoke('connect',address)", args.ble_address)
            else:
                target = args.ip
                if args.discover_wifi:
                    report['stage'] = 'discover'
                    page.evaluate("invoke('connectionMode','wifi');invoke('scan')")
                    pause(3500)
                    discovery = page.evaluate('({cachedIp:canBench.initialNetworkIp,devices:[...canBench.devices]})')
                    # A previously saved address is emitted before any UDP reply.
                    discovery['responses'] = len(discovery['devices']) - int(bool(discovery['cachedIp']))
                    report['wifi_discovery'] = discovery
                    if discovery['responses'] < 1:
                        raise RuntimeError('no new matching UDP discovery reply; cached IP is insufficient')
                    target = discovery['devices'][-1].removeprefix('wifi:')
                report['stage'] = 'connect'
                report['connection_ip'] = target
                page.evaluate("ip=>invoke('connectLan',ip)", target)
            wait('connected&&gotStatus&&window.devicePollingSupported===true', timeout=30000)
            report['initial_device_drops'] = page.evaluate('firmwareDrops')
            if args.all_dm_queries:
                page.evaluate('for(const m of watchedMotors.slice(3,7))setMotorPolling(JSON.stringify(m.config),true)')
                wait('watchedMotors.slice(3,7).every(m=>m.polling)', timeout=10000)

            page.evaluate("closeDevices();showTab('live')")
            page.evaluate("value=>$('detailWindow').value=value", args.detail_window)
            report['detail_window_seconds'] = float(args.detail_window)
            if args.observe_rendering:
                observer = ROOT / 'tests/render_observer.js'
                page.evaluate(observer.read_text())
                render_observer_installed = True
                report['render_observation'] = {
                    'scope': 'WebView JS callbacks and visible-page requestAnimationFrame intervals; excludes compositor/display timing',
                    'observer_sha256': hashlib.sha256(observer.read_bytes()).hexdigest(),
                    'histogram_bin_ms': 1 / 16,
                    'overflow_from_ms': 1000,
                }
            phases = [('raw', None),
                      ('dji-detail', 'dashboardOpen(JSON.stringify(watchedMotors[0].config))'),
                      ('imu-detail', 'dashboardOpen(JSON.stringify(watchedMotors[7].config))'),
                      ('dm-detail', 'dashboardOpen(JSON.stringify(watchedMotors[3].config));setMotorPolling(JSON.stringify(watchedMotors[3].config),true)'),
                      ('pause-filter', "showTab('live');togglePause();$('filter').value='201';renderFrames()"),
                      ('home', "togglePause();$('filter').value='';showTab('motor')")]
            for name, action in phases:
                report['stage'] = name
                if action:
                    page.evaluate(action)
                if name == 'home':
                    wait('canBench.modes.at(-1)===1', timeout=5000)
                    pause(1000)
                    report['home_raw_before'] = page.evaluate('canBench.raw')
                status_before=page.evaluate('canBench.canStatuses.length')
                streams_before=page.evaluate('({...canBench.streamCounts})')
                page.evaluate("enabled=>{canBench.streamWindow=enabled?{started:performance.now(),seen:{}}:null}",name in ('raw','pause-filter'))
                phase_seconds=(args.raw_seconds or args.seconds) if name=='raw' else args.seconds
                if render_observer_installed:
                    page.evaluate('canRenderObserver.reset()')
                save()
                pause(phase_seconds * 1000)
                rendering = page.evaluate('canRenderObserver.snapshot()') if render_observer_installed else None
                row = page.evaluate("""({connected,appDrops,firmwareDrops,raw:canBench.raw,
                  activeDmQueries:watchedMotors.slice(3,7).filter(m=>m.polling).length,canStatuses:[...canBench.canStatuses],
                  streamCounts:{...canBench.streamCounts},
                  streamSilence:canBench.streamWindow?Object.fromEntries(Object.entries(canBench.streamWindow.seen).map(([key,value])=>[key,Math.max(value.maxGap,performance.now()-value.last)])):{},
                  errors:[...canBench.errors],states:[...canBench.states],modes:[...canBench.modes],
                  detail:detailMotor?{ready:detailReady,count:detailSamples.length,
                    values:detailMotor.feedback&&detailMotor.feedback.values}:null})""")
                row.update(name=name, seconds=phase_seconds, can_status_expected=name in ('raw','pause-filter','home'), fresh_can_statuses=len(row['canStatuses'])-status_before, device_drop_delta=(row['firmwareDrops'] - report['initial_device_drops']) & 0xffffffff)
                if rendering is not None:
                    row['rendering'] = rendering
                if name in ('raw','pause-filter'):
                    streams=['513','514','517']+['96:'+str(kind) for kind in range(1,5)]
                    if args.all_dm_queries:
                        streams += [str(0x10+motor) for motor in range(1,5)]
                    row['required_stream_deltas']={key:row['streamCounts'].get(key,0)-streams_before.get(key,0) for key in streams}
                    row['required_stream_silence_ms']={key:row['streamSilence'].get(key,phase_seconds*1000) for key in streams}
                report['phases'].append(row)
                save()
                print(name, row['connected'], row['appDrops'], row['device_drop_delta'], flush=True)
                if not row['connected']:
                    raise ConnectionError('phone disconnected; see phase errors')
            report['home_extra_raw'] = report['phases'][-1]['raw'] - report['home_raw_before']
            report['ok'] = (not report['js_errors'] and report['home_extra_raw'] == 0
                            and all(row['connected'] and not row['appDrops'] and not row['device_drop_delta'] and not row['errors'] for row in report['phases'])
                            and all((not row['can_status_expected'] or row['fresh_can_statuses']>0) and all(status['errors']==0 and status['state']==0 for status in row['canStatuses']) for row in report['phases'])
                            and (not args.all_dm_queries or all(row['activeDmQueries']==4 for row in report['phases']))
                            and all(all(count>0 for count in row.get('required_stream_deltas',{}).values()) for row in report['phases'])
                            and all(all(gap<=3000 for gap in row.get('required_stream_silence_ms',{}).values()) for row in report['phases'])
                            and all((row['detail'] or {}).get('count', 0) >= min(args.seconds, 60) * 60 for row in report['phases'][1:4]))
        except Exception as error:
            report.update(ok=False, failure=type(error).__name__)
            raise
        finally:
            start = time.monotonic()
            if render_observer_installed:
                try:
                    page.evaluate('window.canRenderObserver?.stop()')
                except Exception as error:
                    report.update(ok=False, render_cleanup_failure=type(error).__name__)
            try:
                if channel_owned:
                    page.evaluate("invoke('disconnect')")
                    wait('!connected', timeout=5000, stoppable=False)
                    report['disconnected'] = True
                else:
                    report['disconnect_skipped'] = 'channel was not acquired by this test'
                report['same_pid'] = adb_text('shell', 'pidof', args.package) == pid
                report['ok'] = report['ok'] and report['same_pid']
            except Exception as error:
                report.update(ok=False, disconnect_failure=type(error).__name__)
            report['disconnect_seconds'] = round(time.monotonic() - start, 3)
            save()
            browser.close()
except KeyboardInterrupt:
    report.update(ok=False, failure='Interrupted')
    raise
finally:
    if port is not None:
        subprocess.run(adb + ['forward', '--remove', f'tcp:{port}'], check=False, timeout=10)
    save()
    if bench_lock is not None:
        bench_lock.close()
raise SystemExit(0 if report['ok'] else 1)
