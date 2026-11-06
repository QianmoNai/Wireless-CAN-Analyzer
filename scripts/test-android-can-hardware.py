#!/usr/bin/env python3
"""Opt-in Android debug-WebView CAN UI bench, with physical mode setup and cleanup.
Use a Python environment with desktop transport dependencies and Playwright.
Requires an already authorized ADB phone, forwarded CDP port and saved BLE/TLS trust.
"""
import argparse, ast, asyncio, importlib.util, json, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
p = argparse.ArgumentParser(description='Opt-in visible Android debug-WebView CAN bench; status queries only. Requires local Playwright and an ADB-forwarded CDP port.')
p.add_argument('--profile-folder', type=Path, default=Path.home() / '.local/share/Qianmo/CAN Scope Desktop')
p.add_argument('--fixture', required=True, choices=['stm32h723'])
p.add_argument('--channel', type=int, choices=range(1, 5), default=1)
p.add_argument('--port', required=True)
p.add_argument('--phone', required=True)
p.add_argument('--cdp', default='http://127.0.0.1:35013')
p.add_argument('--output', type=Path, required=True)
p.add_argument('--seconds', type=int, default=15)
p.add_argument('--mode', choices=['ble', 'wifi'], default='ble')
p.add_argument('--restore-wifi-trust', action='store_true', help='BLE only: use the actual settings read button to recover local TLS trust after config import')
p.add_argument('--verify-import-guard', action='store_true', help='Require native config import to refuse an active connection without disconnecting it')
p.add_argument('--history-limit',type=int,choices=[300,10000],default=300,help='Independent RX/TX retention in the bench app')
p.add_argument('--monitor-diagnostics',action='store_true',help='Require firmware 1.24.7 read-only USB drop components, including setup and cleanup')
a = p.parse_args()
if not 5 <= a.seconds <= 300:
    p.error('--seconds must be 5..300')
if a.restore_wifi_trust and a.mode != 'ble':
    p.error('--restore-wifi-trust requires --mode ble')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'desktop'))
from can_scope.connection_store import connection_folder, load_connection
from hardware_bench_lock import hardware_bench_lock
saved = load_connection(a.profile_folder / f'connection-{a.channel}.json')
spec = importlib.util.spec_from_file_location('can_bench', root / 'scripts/test-can-hardware.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
report = {'scope': 'Android11 RedmiK20Pro, independent debug bench app; physical CAN from STM32 simulator; only status queries', 'mode': a.mode, 'phases': [], 'js_errors': [], 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'ok': False}
a.output.parent.mkdir(parents=True, exist_ok=True)

def save():
    a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

def capture_monitor(phase):
    if not a.monitor_diagnostics:
        return
    from usb_management import LocalUsb
    usb=LocalUsb(a.port)
    try:
        if usb.identity().hex()!=saved['id']:
            raise ValueError('USB diagnostics identity differs from the selected wireless analyzer')
        components=usb.monitor_drops()
    finally:
        usb.close()
    rows=report.setdefault('monitor_diagnostics',[])
    baseline=rows[0]['components'] if rows else components
    delta={key:(value-baseline[key])&0xffffffff for key,value in components.items()}
    rows.append(dict(phase=phase,components=components,delta=delta))
    save()
    if any(delta.values()):
        raise AssertionError('CAN monitor drop component increased during '+phase)

with hardware_bench_lock():
    try:
        capture_monitor('before-mode-selection')
        asyncio.run(bench.select(a.port, a.mode))
        capture_monitor('after-mode-selection')
        if a.mode == 'wifi':
            time.sleep(10)
            from can_scope.transports import discover
            found = []
            asyncio.run(discover(saved['id'], found.append))
            if len(found) != 1:
                raise RuntimeError('Expected one discovered IP for the physical analyzer identity')
            saved['ip'] = found[0]['address'].removeprefix('wifi:')
        source = ast.parse((root / 'desktop/tests/can_render_hardware.py').read_text())
        setup = next((ast.literal_eval(n.value) for n in source.body if isinstance(n, ast.Assign) and any((isinstance(t, ast.Name) and t.id == 'setup' for t in n.targets))))
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(a.cdp, no_defaults=True)
            pages = [page for c in b.contexts for page in c.pages]
            page = next((page for page in pages if page.url.startswith('https://app.local/' if a.channel == 1 else f'https://channel-{a.channel}.local/')))
            home = next((page for page in pages if '/home.html' in page.url))
            page.on('pageerror', lambda e: report['js_errors'].append(str(e)))
            try:
                home.evaluate('(channel)=>HomeAndroid.openChannel(channel)', a.channel)
                page.wait_for_function('innerWidth>0')
                if page.evaluate('connected'):
                    raise RuntimeError('Bench channel must be disconnected before installing observers')
                # Earlier rounds must not remain in the observer call chain or
                # multiply raw counts/draw timers. Reload only while detached;
                # the native ready message follows stored-config restoration.
                page.reload(wait_until='load')
                page.wait_for_function("document.body.textContent.includes('准备连接 ESP32-CAN')",polling=100,timeout=10000)
                report['page_reloaded_before_instrumentation']=True
                if page.evaluate('watchedMotors.length') == 0:
                    page.evaluate(setup)
                else:
                    page.evaluate("window.perf={batch:[],draw:[],stream:[],long:[],raf:[]};const timedDraw=drawDetail;drawDetail=()=>{let t=performance.now();timedDraw();perf.draw.push(performance.now()-t)};showTab('live')")
                page.evaluate("()=>{window.nativeErrors=[];window.telemetryEvents=[];window.nativeTimeline=[];window.rawReceived=0;const original=onNative;onNative=(k,d)=>{original(k,d);if(['state','error','telemetryMode','writeDone','queryState'].includes(k)){const event={kind:k,at:performance.now()};for(const field of ['value','message','mode','hz','success','enabled','pending'])if(d[field]!==undefined)event[field]=d[field];nativeTimeline.push(event);if(nativeTimeline.length>128)nativeTimeline.shift();}if(k==='error')nativeErrors.push(d.message);if(k==='packets')for(const item of d.items||[]){const bytes=parsePacket(item.hex||item);if(bytes&&bytes[1]===1)rawReceived++;}if(k==='telemetryMode')telemetryEvents.push({mode:d.mode,hz:d.hz,at:performance.now()})}}")
                if a.mode == 'ble':
                    page.evaluate("invoke('connectionMode','ble');invoke('scan')")
                    page.wait_for_function('(address)=>deviceMap.has(address)', arg=saved['address'], timeout=25000)
                    page.evaluate("address=>invoke('connect',address)", saved['address'])
                else:
                    page.evaluate("ip=>invoke('connectLan',ip)", saved['ip'])
                page.wait_for_function('connected', timeout=30000)
                capture_monitor('wireless-ready')
                page.evaluate("closeDevices();showTab('live')")
                page.wait_for_function('gotStatus', timeout=10000)
                capture_monitor('first-can-status')
                if a.restore_wifi_trust:
                    report['wifi_trust_before'] = page.evaluate("$('wifiKnown').textContent.includes('已保存')")
                    page.evaluate("showTab('settings')")
                    from android_pairing import recover_wifi_trust
                    device_name=page.evaluate("currentBleName||$('deviceName').textContent")
                    report['wifi_pairing'] = recover_wifi_trust(page,a.phone,a.port,saved['id'],device_name)
                    report['wifi_trust_restored'] = True
                    page.evaluate("showTab('live')")
                if a.verify_import_guard:
                    home.evaluate('HomeAndroid.importConfig()')
                    home.wait_for_function("$('configStatus').textContent==='请先断开所有通道连接，再导入配置'",polling=100,timeout=3000)
                    if not page.evaluate('connected'):
                        raise AssertionError('Import guard interrupted the active connection')
                    focus=subprocess.check_output(['adb','-s',a.phone,'shell','dumpsys','window'],timeout=15).decode()
                    if any('documentsui'in line.lower()for line in focus.splitlines()if 'mCurrentFocus'in line):
                        raise AssertionError('Import guard opened the system file picker')
                    report['connected_import_blocked']=True
                capture_monitor('before-history-settings')
                page.evaluate("limit=>{$('rxLimit').value=limit;$('txLimit').value=limit;applyHistoryLimits()}",a.history_limit)
                report['history_limit']=a.history_limit
                report['initial_device_drops'] = page.evaluate('firmwareDrops')
                capture_monitor('connected-before-phases')
                for name, action in [('raw', None), ('dji-detail', 'dashboardOpen(JSON.stringify(watchedMotors[0].config))'), ('imu-detail', 'dashboardOpen(JSON.stringify(watchedMotors[7].config))'), ('dm-detail', 'dashboardOpen(JSON.stringify(watchedMotors[3].config));setMotorPolling(JSON.stringify(watchedMotors[3].config),true)'), ('pause-filter', "showTab('live');togglePause();$('filter').value='201';renderFrames()"), ('home', "togglePause();$('filter').value='';showTab('motor')")]:
                    if action:
                        page.evaluate(action)
                    if name == 'home':
                        page.wait_for_function('telemetryEvents.at(-1)?.mode===1', timeout=5000)
                        page.wait_for_timeout(1000)
                        report['home_raw_before'] = page.evaluate('rawReceived')
                    if name=='pause-filter':
                        frozen_before=page.evaluate("({html:$('frames').innerHTML,raw:rawReceived})")
                    page.evaluate('for(const key in perf)perf[key]=[]')
                    page.wait_for_timeout(a.seconds * 1000)
                    row = page.evaluate("({connected,appDrops,firmwareDrops,rawReceived,telemetryEvents:[...telemetryEvents],nativeTimeline:[...nativeTimeline],frames:frames.length,history:{rx:frameHistory.rx.length,tx:frameHistory.tx.length},errors:[...nativeErrors],roster:dashboardSnapshot(),detail:detailMotor?{ready:detailReady,count:detailSamples.length,feedbackAge:detailMotor.time?Date.now()-detailMotor.time:null,values:detailMotor.feedback&&detailMotor.feedback.values,state:$('detailState').textContent,canRate:$('detailCanRate').textContent,appRate:$('detailAppRate').textContent}:null,perf:Object.fromEntries(Object.entries(perf).map(([k,a])=>[k,{count:a.length,max:a.length?Math.max(...a):0,p95:[...a].sort((a,b)=>a-b)[Math.floor(a.length*.95)]||0}]))})")
                    row['name'] = name
                    row['device_drop_delta'] = row['firmwareDrops'] - report['initial_device_drops'] & 4294967295
                    report['phases'].append(row)
                    capture_monitor(name)
                    save()
                    print(name, row['connected'], row['appDrops'], row['firmwareDrops'], flush=True)
                    shot = a.output.with_name(a.output.stem + '-' + name + '.png')
                    subprocess.run(['adb', '-s', a.phone, 'exec-out', 'screencap', '-p'], stdout=shot.open('wb'), check=True)
                    if not row['connected']:
                        raise ConnectionError('Phone disconnected')
                    if name in ('dji-detail','imu-detail','dm-detail') and (row['detail'] is None or row['detail']['feedbackAge'] is None or row['detail']['feedbackAge'] >= 2000):
                        raise AssertionError('Physical detail feedback became stale during '+name)
                    if name=='raw'and a.history_limit==10000 and a.seconds>=20 and row['history']['rx']!=10000:
                        raise AssertionError('Physical RX history did not reach its configured maximum')
                    if name=='pause-filter':
                        if not page.evaluate("paused&&$('filter').value==='201'")or page.evaluate("$('frames').innerHTML")!=frozen_before['html']or row['rawReceived']<=frozen_before['raw']+20:
                            raise AssertionError('Paused filter changed its display or stopped receiving physical CAN')
                        row['frozen_display_with_live_can']=True
                report['home_extra_raw'] = report['phases'][-1]['rawReceived'] - report['home_raw_before']
                report['ok'] = report['home_extra_raw'] == 0 and (not report['js_errors']) and all((r['connected'] and (not r['appDrops']) and (not r['device_drop_delta']) and (not r['errors']) for r in report['phases'])) and all(((r['detail'] or {}).get('count', 0) >= a.seconds * 60 for r in report['phases'][1:4]))
            except Exception as e:
                report['failure'] = type(e).__name__
                report['failure_message'] = str(e)[:300]
                report['ok'] = False
                try:
                    report['failure_snapshot'] = page.evaluate("({connected,nativeTimeline:[...nativeTimeline],appDrops,firmwareDrops,detail:detailMotor?{ready:detailReady,feedbackAge:detailMotor.time?Date.now()-detailMotor.time:null}:null})")
                except Exception as snapshot_error:
                    report['failure_snapshot_error'] = type(snapshot_error).__name__
                save()
                raise
            finally:
                stop = time.monotonic()
                try:
                    page.evaluate("invoke('disconnect')")
                    page.wait_for_function('!connected', timeout=5000)
                    report['disconnected'] = True
                except Exception as e:
                    report['disconnect_failed'] = type(e).__name__
                    report['ok'] = False
                report['disconnect_seconds'] = round(time.monotonic() - stop, 3)
                try:
                    capture_monitor('after-wireless-disconnect')
                except Exception as e:
                    report.update(ok=False,monitor_disconnect_error=type(e).__name__)
                save()
                b.close()
    except Exception as error:
        report.update(ok=False, failure=type(error).__name__,failure_message=str(error)[:300])
    finally:
        try:
            asyncio.run(bench.select(a.port, 'usb'))
            report['restored_usb_can'] = True
            capture_monitor('after-usb-restore')
        except Exception as error:
            report.update(ok=False, restore_failed=type(error).__name__)
        save()
    raise SystemExit(0 if report['ok'] else 1)
