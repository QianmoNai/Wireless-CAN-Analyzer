"""Opt-in packaged Linux CAN operations with real Qt file dialogs and SWD.

Requires Playwright, xdotool, an X11 display and the instrumented dual STM32
fixture. Uses a disposable desktop profile. Sends only arbitrary bench frames,
never motion controls. GUI success alone cannot pass the physical frame check.
"""
import argparse
import ast
import csv
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'desktop'), str(ROOT/'scripts')]
from can_scope.connection_store import connection_folder, load_connection, save_connection


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def wait_until(predicate, timeout=10):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise TimeoutError('Expected desktop operation did not complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, choices=['stm32h723-dual'])
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--mode', choices=['usb', 'ble', 'wifi'], required=True)
    parser.add_argument('--port', default='/dev/ttyACM0')
    parser.add_argument('--channel', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--bus', type=int, choices=[1, 2], default=1)
    parser.add_argument('--display', default=':1')
    parser.add_argument('--niri-socket', help='Optional compositor IPC socket for focus on a Niri/Xwayland desktop')
    parser.add_argument('--screenshots', action='store_true', help='Capture native dialogs using ffmpeg')
    parser.add_argument('--reset-usb-reconnect',type=Path,metavar='ESPTOOL',
                        help='USB only: watchdog-reset this analyzer and test packaged identity reconnect')
    parser.add_argument('--all-bitrates',action='store_true',help='Rate-enabled fixture only: apply all four rates through the actual GUI')
    parser.add_argument('--monitor-seconds',type=int,default=0,help='Also exercise six physical motor/IMU display phases (5..60 seconds each)')
    parser.add_argument('--detail-window',choices=['0.1','0.5','1','5','10','30','60'],default='1',help='Visible detail history in seconds; sampling remains unchanged')
    parser.add_argument('--history-limit',type=int,choices=[300,10000],default=300,help='Independent RX/TX retention in the disposable profile')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.reset_usb_reconnect and args.mode!='usb':
        parser.error('--reset-usb-reconnect requires --mode usb')
    if args.monitor_seconds and not 5<=args.monitor_seconds<=60:
        parser.error('--monitor-seconds must be zero or 5..60')
    import asyncio
    bench = module('can_bench', ROOT/'scripts/test-can-hardware.py')
    fixture = module('dual_fixture', ROOT/'scripts/test-dual-can-hardware.py').Fixture(args.elf)
    saved = load_connection(connection_folder()/f'connection-{args.channel}.json')
    report = dict(ok=False, mode=args.mode, scope='packaged Qt/X11 and physical CAN; disposable profile',
                  binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),
                  fixture_image_sha256=fixture.image_sha256, frames=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    proc = browser = page = None
    try:
        report['fixture_health_before']=fixture.health()
        if any(report['fixture_health_before'].values()):
            raise RuntimeError('Fixture has existing CAN errors')
        asyncio.run(bench.select(args.port, args.mode))
        if args.mode == 'wifi':
            time.sleep(10)
        with tempfile.TemporaryDirectory(prefix='can-native-operations-') as temporary:
            folder = Path(temporary)
            saved.update(mode=args.mode, usb_port=args.port)
            save_connection(folder/'Qianmo/CAN Scope Desktop/connection-1.json', saved)
            target = ('usb:'+args.port if args.mode == 'usb' else
                      'wifi:'+saved['ip'] if args.mode == 'wifi' else saved['address'])
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, XDG_DATA_HOME=temporary, QT_QPA_PLATFORM='xcb', DISPLAY=args.display,
                       XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}',
                       DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{os.getuid()}/bus',
                       QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}')
            def debugger_ready():
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=.2) as r:
                        json.load(r)
                    return True
                except (OSError, ValueError):
                    return False

            with args.output.with_suffix('.qt.log').open('w') as log:
                proc = subprocess.Popen([str(args.binary.resolve())], env=env, stdout=log, stderr=subprocess.STDOUT)
                wait_until(debugger_ready)
                with sync_playwright() as pw:
                    browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', no_defaults=True)
                    page = browser.contexts[0].pages[0]
                    report['js_errors']=[]
                    page.on('pageerror',lambda error:report['js_errors'].append(str(error)))
                    page.wait_for_function("window.CanAndroid&&typeof CanAndroid.connect==='function'")
                    tree = ast.parse((ROOT/'desktop/tests/can_render_hardware.py').read_text())
                    setup = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                                 and any(isinstance(t, ast.Name) and t.id == 'setup' for t in n.targets))
                    page.evaluate(setup)
                    page.evaluate("""()=>{window.queryAudit=[];const prior=onNative;onNative=(kind,data)=>{
                        if(kind==='queryState'||kind==='error'){
                            queryAudit.push({kind,data,at:Date.now()});if(queryAudit.length>24)queryAudit.shift();
                        }return prior(kind,data)
                    }}""")
                    page.evaluate("target=>invoke('connect',target)", target)
                    page.wait_for_function('connected&&gotStatus&&frames.length>20', timeout=35000)
                    if args.all_bitrates:
                        fixture.quiet(0)
                        time.sleep(.1)
                        page.evaluate("()=>{window.rateRx=0;const ratePriorNative=onNative;onNative=(kind,data)=>{if(kind==='packets')for(const item of data.items||[]){const bytes=parsePacket(item.hex||item);if(bytes&&bytes[1]===1)rateRx++}return ratePriorNative(kind,data)}}")
                        report['gui_bitrates']=[]
                        for rate in [125000,250000,500000,1000000]:
                            fixture.set_rate(args.bus-1,0)
                            page.evaluate("showTab('settings')")
                            page.locator('#busBitrate').select_option(str(rate))
                            page.locator('#saveBitrate').click()
                            page.wait_for_function("rate=>!deviceSettingsBusy&&bridgeBitrate===rate&&$('rateResult').textContent==='波特率已应用'",arg=rate,timeout=10000)
                            before=page.evaluate('rateRx')
                            fixture.set_rate(args.bus-1,rate)
                            fixture.quiet(1)
                            page.evaluate("showTab('live')")
                            page.wait_for_function('before=>rateRx>before+20',arg=before,timeout=5000)
                            health=fixture.health()
                            if any(health.values()):raise AssertionError('CAN error at GUI bitrate')
                            report['gui_bitrates'].append(dict(bitrate=rate,received=page.evaluate('rateRx')-before,health=health))
                            fixture.quiet(0)
                            time.sleep(.1)
                        fixture.quiet(1)
                    if args.reset_usb_reconnect:
                        cache=folder/'Qianmo/CAN Scope Desktop/connection-1.json'
                        initial=load_connection(cache)
                        identity=initial.get('usb_identity')
                        if not identity or (identity['vid'],identity['pid'])!=(0x303a,0x1001):
                            raise ValueError('Expected a saved native USB hardware identity')
                        original=str(Path(args.port).resolve(strict=True))
                        page.evaluate("invoke('disconnect')")
                        page.wait_for_function('!connected')
                        # A single ROM read plus watchdog reset returns directly
                        # to the application. No Flash/NVS or CAN command write.
                        subprocess.run([str(args.reset_usb_reconnect),'--chip','esp32s3','--port',
                            args.port,'--before','usb-reset','--after','watchdog-reset','--no-stub','read-mac'],
                            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=25)
                        wait_until(lambda:Path(args.port).exists(),15)
                        page.evaluate("invoke('reconnect')")
                        page.wait_for_function('connected&&gotStatus&&frames.length>20',timeout=35000)
                        after=load_connection(cache)
                        if after.get('usb_identity')!=identity or after['usb_port']!=str(Path(args.port).resolve(strict=True)):
                            raise AssertionError('Packaged reconnect used a different hardware identity or port')
                        report['usb_reconnect']=dict(before_port=original,after_port=after['usb_port'],
                                                     same_identity=True,ready=True)
                    if args.monitor_seconds:
                        page.evaluate("limit=>{ $('rxLimit').value=limit;$('txLimit').value=limit;applyHistoryLimits()}",args.history_limit)
                        report.update(history_limit=args.history_limit,monitor_seconds=args.monitor_seconds,detail_window=args.detail_window)
                        page.evaluate("()=>{window.monitorRaw=0;window.monitorModeSeen=null;const prior=onNative;onNative=(k,d)=>{prior(k,d);if(k==='telemetryMode')monitorModeSeen=d.mode;if(k==='packets')for(const x of d.items||[]){const p=parsePacket(x.hex||x);if(p&&p[1]===1)monitorRaw++}}}")
                        report['monitor_phases']=[]
                        baseline=page.evaluate('firmwareDrops')
                        for name,action in [('raw',"showTab('live')"),
                                ('dji-detail','dashboardOpen(JSON.stringify(watchedMotors[0].config))'),
                                ('imu-detail','dashboardOpen(JSON.stringify(watchedMotors[7].config))'),
                                ('dm-detail','dashboardOpen(JSON.stringify(watchedMotors[3].config));setMotorPolling(JSON.stringify(watchedMotors[3].config),true)'),
                                ('pause-filter',"showTab('live');togglePause();$('filter').value='201';renderFrames()"),
                                ('home',"togglePause();$('filter').value='';showTab('motor')")]:
                            report['active_operation']='monitor-'+name
                            page.evaluate(action)
                            if name in ('dji-detail','imu-detail','dm-detail'):
                                page.locator('#detailWindow').select_option(args.detail_window)
                                page.wait_for_function("value=>$('detailWindow').value===value",arg=args.detail_window)
                            if name=='home':
                                page.wait_for_function('monitorModeSeen===1',timeout=5000)
                                page.wait_for_timeout(1000)
                                home_before=page.evaluate('monitorRaw')
                            if name=='pause-filter':
                                frozen_before=page.evaluate("({html:$('frames').innerHTML,raw:monitorRaw})")
                            page.evaluate('for(const key in perf)perf[key]=[]')
                            page.wait_for_timeout(args.monitor_seconds*1000)
                            row=page.evaluate("({connected,appDrops,firmwareDrops,raw:monitorRaw,history:{rx:frameHistory.rx.length,tx:frameHistory.tx.length},roster:dashboardSnapshot(),detail:detailMotor?{ready:detailReady,count:detailSamples.length,values:detailMotor.feedback&&detailMotor.feedback.values,canRate:$('detailCanRate').textContent,appRate:$('detailAppRate').textContent}:null,draw:perf.draw})")
                            row['name']=name
                            report['monitor_phases'].append(row)
                            if not row['connected']or row['appDrops']or row['firmwareDrops']!=baseline:
                                raise AssertionError('Packaged monitor lost connection or CAN data')
                            if name in ('dji-detail','imu-detail','dm-detail'):
                                detail=row['detail']or{}
                                if not detail.get('ready')or detail.get('count',0)<args.monitor_seconds*60 or not detail.get('values'):
                                    raise AssertionError('Packaged detail samples/parameters missing')
                            if name=='home'and row['raw']!=home_before:
                                raise AssertionError('Packaged overview did not silence raw CAN')
                            if name=='raw'and args.history_limit==10000 and args.monitor_seconds>=20 and row['history']['rx']!=10000:
                                raise AssertionError('Physical RX history did not reach its configured maximum')
                            if name=='pause-filter':
                                if not page.evaluate("paused&&$('filter').value==='201'")or page.evaluate("$('frames').innerHTML")!=frozen_before['html']or row['raw']<=frozen_before['raw']+20:
                                    raise AssertionError('Paused filter changed its display or stopped receiving physical CAN')
                                row['frozen_display_with_live_can']=True
                            args.output.write_text(json.dumps(report,indent=2)+'\n')
                        report['active_operation']='stop-motor-query'
                        report['query_stop_before']=page.evaluate("({polling:watchedMotors[3].polling,message:watchedMotors[3].queryMessage,events:queryAudit})")
                        page.evaluate('setMotorPolling(JSON.stringify(watchedMotors[3].config),false)')
                        try:
                            page.wait_for_function('!watchedMotors[3].polling',timeout=5000)
                        except Exception:
                            # Capture before Playwright's context closes its connection.
                            report['query_failure_state']=page.evaluate("({connected,hidden:document.hidden,focused:document.hasFocus(),events:queryAudit,motors:watchedMotors.map(m=>({id:m.config.id,profile:m.config.profile,polling:m.polling,message:m.queryMessage}))})")
                            raise
                        report['query_stop_after']=page.evaluate("({polling:watchedMotors[3].polling,message:watchedMotors[3].queryMessage,events:queryAudit})")
                        page.wait_for_timeout(200)
                        page.evaluate("showTab('live')")
                    base = fixture.symbols['can_bench']+(args.bus-1)*52
                    for identifier, extended, remote, dlc in [(0x6a0, False, False, 8),
                            (0x1abcde, True, False, 8), (0x6a1, False, True, 8), (0x1abcdf, True, True, 0)]:
                        data = bytes([0x12, 0x34, 0xab, 0xcd, 0, 0x55, 0xaa, 0xff])
                        before = fixture.read(base+48)[0]
                        previous = page.evaluate("$('sendResult').firstElementChild?.dataset.token||''")
                        page.locator('#sendId').fill(f'{identifier:x}')
                        page.locator('#sendFormat').select_option('ext' if extended else 'std')
                        page.locator('#sendKind').select_option('rtr' if remote else 'data')
                        page.locator('#sendDlc').fill(str(dlc))
                        if not remote:
                            page.locator('#sendData').fill(data.hex(' '))
                        page.locator('#sendOnce').click()
                        page.wait_for_function("previous=>$('sendResult').firstElementChild?.dataset.token!==previous&&$('sendResult').firstElementChild?.dataset.state==='success'", arg=previous, timeout=5000)
                        wait_until(lambda: fixture.read(base+48)[0] > before, 3)
                        actual = struct.pack('<IIII', *fixture.read(base+32, 4))
                        if (struct.unpack_from('<I', actual)[0] != identifier or actual[4] != int(extended)
                                or actual[5] != dlc or actual[14] != int(remote)
                                or (not remote and actual[6:14] != data)):
                            raise AssertionError('Desktop frame differs from physical CAN reception')
                        report['frames'].append(dict(id=hex(identifier), extended=extended, remote=remote,
                                                     dlc=dlc, physical_match=True))
                    # Pause display; retention continues, so capture export at submission.
                    page.evaluate('togglePause()')
                    def file_dialog(button, path, title):
                        report['active_operation'] = button
                        def handle_dialog():
                            result = subprocess.run(['xdotool', 'search', '--sync', '--onlyvisible', '--pid',
                                    str(proc.pid), '--name', '^'+title+'$'], env=env,
                                    capture_output=True, text=True, check=True, timeout=8)
                            windows = result.stdout.split()
                            if len(windows) != 1:
                                raise RuntimeError('Expected exactly one matching native file dialog')
                            def key(*items):
                                subprocess.run(['xdotool', *items], env=env, check=True, timeout=5,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            # Also works on the dedicated X11 test display,
                            # where no window manager provides activation.
                            key('windowfocus', '--sync', windows[0])
                            if args.niri_socket:
                                ipc_env = dict(env, NIRI_SOCKET=args.niri_socket)
                                deadline=time.monotonic()+3
                                while True:
                                    visible = json.loads(subprocess.check_output(['niri','msg','-j','windows'],env=ipc_env))
                                    # Satellite reports its PID; X11 search above
                                    # verifies the app. Compositor mapping is async.
                                    owned = [w for w in visible if w.get('app_id')=='CAN Scope Desktop']
                                    matches = [w for w in owned if w.get('title')==title]
                                    if matches or time.monotonic()>=deadline:
                                        break
                                    time.sleep(.05)
                                report['compositor_windows'] = [{k:w.get(k) for k in ('id','pid','title')} for w in owned]
                                if len(matches)!=1:
                                    raise RuntimeError('Expected one compositor dialog for this process')
                                subprocess.run(['niri','msg','action','focus-window','--id',str(matches[0]['id'])],
                                    env=ipc_env,check=True,stdout=subprocess.DEVNULL,timeout=5)
                                wanted_id=matches[0]['id']
                                wait_until(lambda:any(w.get('id')==wanted_id and w.get('is_focused')
                                    for w in json.loads(subprocess.check_output(
                                        ['niri','msg','-j','windows'],env=ipc_env))),3)
                            key('key', '--window', windows[0], '--clearmodifiers', '--delay', '80', 'alt+n', 'ctrl+a')
                            key('type', '--window', windows[0], '--clearmodifiers', '--delay', '1', str(path))
                            if args.screenshots:
                                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','x11grab',
                                    '-window_id',windows[0],'-i',args.display,'-frames:v','1','-y',str(args.output.with_suffix('.dialog.png'))],
                                    check=True,timeout=5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                            key('key', '--window', windows[0], '--clearmodifiers', 'Return')
                            print('native-dialog-submitted', button, flush=True)
                        # Qt's modal dialog can hold the originating WebChannel
                        # callback. Handle it concurrently with the browser click.
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
                            future = worker.submit(handle_dialog)
                            page.locator(button).click()
                            future.result(timeout=20)

                    config = folder/'bench-config.json'
                    file_dialog('#desktopExport', config, '导出当前通道')
                    wait_until(config.exists)
                    exported = json.loads(config.read_text())
                    if len(exported['channels'][0]['motors']) != 8:
                        raise AssertionError('Configuration export lost motors')
                    report['native_config_export'] = True
                    # Capture what the actual CSV button submits, then compare every written cell.
                    page.evaluate("()=>{const prior=invoke;window.exportExpected=null;window.invoke=(name,...args)=>{if(name==='exportCsv')exportExpected=args[0];return prior(name,...args)}}")
                    csv_path = folder/'bench-frames.csv'
                    file_dialog('#desktopCsv', csv_path, '导出当前通道')
                    wait_until(csv_path.exists)
                    from can_scope.csv_export import HEADER, rows
                    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
                        written = list(csv.reader(stream))
                    expected = page.evaluate('exportExpected')
                    if written != [HEADER]+[[str(v) for v in row] for row in rows(expected)]:
                        raise AssertionError('Native CSV export differs from retained CAN frames')
                    if args.history_limit==10000 and args.monitor_seconds>=20 and len(written)-1<10000:
                        raise AssertionError('Native CSV did not include the maximum physical RX history')
                    report.update(native_csv_export=True, csv_rows=len(written)-1)
                    report['active_operation']='disconnect'
                    page.evaluate("invoke('disconnect')")
                    page.wait_for_function('!connected')
                    # Change only the disposable profile; the native importer must restore it.
                    report['active_operation']='remove-test-roster'
                    page.evaluate("for(const motor of [...watchedMotors])removeWatchedMotor(motor)")
                    page.wait_for_function('watchedMotors.length===0')
                    file_dialog('#desktopImport', config, '导入当前通道配置')
                    page.wait_for_function('watchedMotors.length===8', timeout=10000)
                    actual = page.evaluate("ConfigIO.exportChannel(1,'','').motors")
                    if actual != exported['channels'][0]['motors']:
                        raise AssertionError('Native configuration import changed motor settings')
                    if page.evaluate('({rx:rxSaveLimit,tx:txSaveLimit})')!=exported['channels'][0]['historyLimits']:
                        raise AssertionError('Native configuration import changed history limits')
                    if report['js_errors']:
                        raise AssertionError('Packaged UI JavaScript errors')
                    report.update(native_config_import=True, ok=True)
                    browser.close()
                    browser = None
    except Exception as exc:
        report.update(ok=False, failure=type(exc).__name__, failure_message=str(exc)[:1200])
        report['failure_location']=[dict(file=Path(frame.filename).name,line=frame.lineno,function=frame.name)
                                    for frame in traceback.extract_tb(exc.__traceback__)
                                    if Path(frame.filename).resolve()==Path(__file__).resolve()]
        if page and 'query_failure_state' not in report:
            try:
                report['query_failure_state']=page.evaluate("({connected,events:window.queryAudit||[],motors:watchedMotors.map(m=>({id:m.config.id,profile:m.config.profile,polling:m.polling,message:m.queryMessage}))})")
            except Exception:
                pass
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        try:
            asyncio.run(bench.select(args.port, 'usb'))
            if args.all_bitrates:
                from can_scope.usb_transport import UsbTransport
                from can_scope.protocol import packet,u32
                async def restore_rate():
                    fixture.quiet(0)
                    fixture.set_rate(args.bus-1,0)
                    received=[]
                    link=UsbTransport(received.append,lambda _:None)
                    try:
                        await link.connect(args.port)
                        await link.write(packet(0x12,x=1000000,token=0x3ffffffe))
                        deadline=time.monotonic()+5
                        while time.monotonic()<deadline:
                            if any(p[1]==5 and p[2]==0 and u32(p,4)==1000000 and u32(p,16)==0x3ffffffe for p in received):break
                            await asyncio.sleep(.01)
                        else:raise TimeoutError('GUI test bitrate restoration missing')
                    finally:await link.close()
                    fixture.set_rate(args.bus-1,1000000)
                    fixture.quiet(1)
                asyncio.run(restore_rate())
                report['restored_1mbps']=True
            report['restored_usb_can'] = True
        except Exception as exc:
            report.update(ok=False, restore_failed=type(exc).__name__)
        try:
            report['fixture_health_after']=fixture.health()
            if any(report['fixture_health_after'].values()):
                report.update(ok=False,failure='Physical CAN error')
        except Exception as exc:
            report.update(ok=False,health_failed=type(exc).__name__)
        args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['ok'] else 1)


if __name__ == '__main__':
    from hardware_bench_lock import hardware_bench_lock
    with hardware_bench_lock():
        main()
