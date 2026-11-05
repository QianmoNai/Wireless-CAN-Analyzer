"""Opt-in two real analyzer channels in the installed Qt package; no motion.

Requires firmware 1.24.7 read-only drop diagnostics. Native fullscreen checks
additionally require an X11 window manager and xprop. Identity mismatches stop
before mode changes or restoration writes.
"""
import argparse
import ast
import asyncio
import hashlib
import importlib.util
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / 'desktop'), str(root / 'scripts')]
from hardware_bench_lock import hardware_bench_lock
from can_scope.connection_store import connection_folder, load_connection, save_connection
from can_scope.transports import discover
from usb_management import LocalUsb

def module(name, file):
    spec = importlib.util.spec_from_file_location(name, root / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def main(a):
    bench = module('bench', 'scripts/test-can-hardware.py')
    fixture = module('fixture', 'scripts/test-dual-can-hardware.py').Fixture(a.elf)
    report = dict(ok=False, modes=a.modes, scope='installed Qt package, two physical analyzers and independent STM32 CAN controllers', binary_sha256=hashlib.sha256(a.binary.read_bytes()).hexdigest(), fixture_image_sha256=fixture.image_sha256, phases=[], js_errors=[])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    proc = None
    verified = False
    try:
        if any(fixture.health().values()):
            raise RuntimeError('Existing CAN errors')
        identities = []
        report['monitor_before']=[]
        saved = []
        for i, port in enumerate(a.ports):
            usb = LocalUsb(port)
            try:
                identities.append(usb.identity().hex(':'))
                report['monitor_before'].append(usb.monitor_drops())
            finally:
                usb.close()
            saved.append(load_connection(connection_folder() / f'connection-{i + 1}.json'))
            if identities[-1].replace(':','')!=saved[-1].get('id'):
                raise ValueError('USB identity differs from the saved channel')
        if identities[0] == identities[1]:
            raise ValueError('Expected distinct analyzers')
        verified = True
        report['identities'] = identities
        for port, mode in zip(a.ports, a.modes):
            asyncio.run(bench.select(port, mode))
        if 'wifi' in a.modes:
            time.sleep(10)
        for i, mode in enumerate(a.modes):
            if mode == 'wifi':
                found = []
                asyncio.run(discover(saved[i]['id'], found.append))
                if len(found) != 1:
                    raise RuntimeError('Expected one identity-matched Wi-Fi device')
                saved[i]['ip'] = found[0]['address'].removeprefix('wifi:')
        with tempfile.TemporaryDirectory(prefix='can-two-real-channels-') as temporary:
            folder = Path(temporary)
            data = folder / 'Qianmo/CAN Scope Desktop'
            data.mkdir(parents=True)
            (data / 'channels.txt').write_text('2')
            for i in range(2):
                save_connection(data / f'connection-{i + 1}.json', dict(saved[i], mode=a.modes[i], usb_port=a.ports[i]))
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0))
                cdp = s.getsockname()[1]
            env = dict(os.environ, XDG_DATA_HOME=temporary, QT_QPA_PLATFORM='xcb', DISPLAY=':1', XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}', DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{os.getuid()}/bus', QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{cdp}')
            with a.output.with_suffix('.qt.log').open('w') as log:
                proc = subprocess.Popen([str(a.binary)], env=env, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{cdp}/json/version', timeout=0.2) as r:
                            json.load(r)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{cdp}', no_defaults=True)
                    deadline = time.monotonic() + 10
                    while sum((len(c.pages) for c in browser.contexts)) < 2 and time.monotonic() < deadline:
                        time.sleep(0.1)
                    pages = [p for c in browser.contexts for p in c.pages]
                    if len(pages) != 2:
                        raise AssertionError('Expected exactly two native channel pages')
                    for p in pages:
                        p.wait_for_function("window.CanAndroid&&typeof CanAndroid.connect==='function'", polling=100)
                    pages.sort(key=lambda p: p.evaluate('document.hidden'))
                    if [p.evaluate('document.hidden') for p in pages] != [False, True]:
                        raise AssertionError('Native initial tab visibility unclear')
                    source = ast.parse((root / 'desktop/tests/can_render_hardware.py').read_text())
                    setup = next((ast.literal_eval(n.value) for n in source.body if isinstance(n, ast.Assign) and any((isinstance(t, ast.Name) and t.id == 'setup' for t in n.targets))))
                    for i, p in enumerate(pages):
                        p.on('pageerror', lambda e: report['js_errors'].append(str(e)))
                        p.evaluate(setup)
                        p.evaluate("()=>{window.rawCount=0;window.nativeFailures=[];window.modes=[];window.sentinels=[];const prior=onNative;onNative=(k,d)=>{prior(k,d);if(k==='telemetryMode')modes.push(d.mode);if(k==='error')nativeFailures.push(d.message);if(k==='packets')for(const x of d.items||[]){const p=parsePacket(x.hex||x);if(p&&p[1]===1){rawCount++;if(u32(p,4)===0x6c1||u32(p,4)===0x6c2)sentinels.push({id:u32(p,4),data:Array.from(p.slice(8,16))})}}}}")
                        target = 'usb:' + a.ports[i] if a.modes[i] == 'usb' else 'wifi:' + saved[i]['ip'] if a.modes[i] == 'wifi' else saved[i]['address']
                        p.evaluate("target=>invoke('connect',target)", target)
                        p.wait_for_function('connected&&gotStatus', timeout=35000, polling=100)
                    window = subprocess.check_output(['xdotool', 'search', '--onlyvisible', '--pid', str(proc.pid), '--name', '^CAN Scope Desktop'], env=env, text=True).split()
                    if len(window) != 1:
                        raise RuntimeError('Expected one package window owned by this process')
                    baseline = [p.evaluate('firmwareDrops') for p in pages]
                    for turn in range(a.switches + 1):
                        active = turn % 2
                        hidden = 1 - active
                        if turn:
                            subprocess.run(['xdotool', 'windowfocus', '--sync', window[0]], env=env, check=True, timeout=5)
                            subprocess.run(['xdotool', 'key', '--window', window[0], '--clearmodifiers', 'ctrl+Tab'], env=env, check=True, timeout=5)
                        if a.detail_fullscreen:
                            pages[active].evaluate("showTab('live')")
                        pages[active].wait_for_function('!document.hidden&&modes.at(-1)===0', timeout=8000, polling=100)
                        pages[hidden].wait_for_function('document.hidden&&modes.at(-1)===3', timeout=8000, polling=100)
                        pages[active].wait_for_timeout(1000)
                        before = [p.evaluate('rawCount') for p in pages]
                        identifier = 1729 + active
                        payload = struct.pack('<II', turn + 1, identifier)
                        base = fixture.symbols['can_bench'] + active * 52
                        sequence = 0x39000000 + turn
                        fixture.write(base, list(struct.unpack('<IIII', struct.pack('<IBB8sBB', identifier, 0, 8, payload, 0, 0))))
                        fixture.write(base + 16, [sequence])
                        pages[active].wait_for_function('e=>sentinels.some(s=>s.id===e.id&&JSON.stringify(s.data)===JSON.stringify(e.data))', arg=dict(id=identifier, data=list(payload)), polling=100, timeout=5000)
                        if fixture.read(base + 20, 2) != [sequence, 0]:
                            raise AssertionError('Fixture CAN transmit failed')
                        pages[active].wait_for_timeout(2000)
                        states = [p.evaluate('({connected,rawCount,appDrops,firmwareDrops,errors:nativeFailures,mode:modes.at(-1),sentinels,motors:dashboardSnapshot()})') for p in pages]
                        delta = [v['rawCount'] - b for v, b in zip(states, before)]
                        if not all((s['connected'] and (not s['appDrops']) and (not s['errors']) and (s['firmwareDrops'] == baseline[i]) and all((x['id'] == 0x6c1 + i for x in s['sentinels'])) for i, s in enumerate(states))) or delta[active] < 300 or delta[hidden] != 0:
                            raise AssertionError('Channel switch did not isolate and resume physical telemetry')
                        report['phases'].append(dict(turn=turn, active_channel=active + 1, raw_delta=delta, states=states))
                        if a.detail_fullscreen:
                            page=pages[active]
                            page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[0].config))')
                            page.wait_for_function('detailReady&&detailSamples.length-detailSampleHead>=50',timeout=8000,polling=100)
                            page.evaluate('openDetailChart(detailSeries[0].key)')
                            page.wait_for_function('detailChartKey!==null',timeout=5000,polling=100)
                            deadline=time.monotonic()+3
                            while True:
                                state=subprocess.check_output(['xprop','-id',window[0],'_NET_WM_STATE'],env=env,text=True)
                                if '_NET_WM_STATE_FULLSCREEN' in state:break
                                if time.monotonic()>deadline:raise AssertionError('Native X11 window did not enter fullscreen')
                                time.sleep(.05)
                            page.wait_for_timeout(500)
                            detail=page.evaluate('({connected,ready:detailReady,count:detailSamples.length-detailSampleHead,key:detailChartKey,values:detailMotor.feedback&&detailMotor.feedback.values,appDrops,firmwareDrops})')
                            if not detail['connected']or not detail['ready']or not detail['values']or detail['appDrops']or detail['firmwareDrops']!=baseline[active]:
                                raise AssertionError('Physical fullscreen detail lost feedback')
                            if pages[hidden].evaluate('detailChartKey!==null'):
                                raise AssertionError('Hidden channel retained its fullscreen chart')
                            report['phases'][-1]['fullscreen_detail']=dict(detail,native_x11_fullscreen=True)
                        a.output.write_text(json.dumps(report, indent=2) + '\n')
                    for p in pages:
                        if a.detail_fullscreen:p.evaluate('closeMotorDetail()')
                        p.evaluate("invoke('disconnect')")
                        p.wait_for_function('!connected', polling=100)
                    if report['js_errors']:
                        raise AssertionError('JavaScript errors')
                    browser.close()
                    report['ok'] = True
    except Exception as e:
        report.update(ok=False, failure=type(e).__name__, failure_message=str(e)[:1000])
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        report['restored_usb_can'] = []
        for port in a.ports if verified else []:
            try:
                asyncio.run(bench.select(port, 'usb'))
                report['restored_usb_can'].append(True)
            except Exception:
                report['restored_usb_can'].append(False)
                report['ok'] = False
        report['health'] = fixture.health()
        report['monitor_after']=[]
        report['monitor_delta']=[]
        for i,port in enumerate(a.ports if verified else []):
            usb=None
            try:
                usb=LocalUsb(port)
                current=usb.monitor_drops();report['monitor_after'].append(current)
                delta={key:(value-report['monitor_before'][i][key])&0xffffffff for key,value in current.items()}
                report['monitor_delta'].append(delta)
                if any(delta.values()):report['ok']=False
            except Exception as error:
                report['ok']=False;report['monitor_failure']=type(error).__name__
            finally:
                if usb:usb.close()
        if any(report['health'].values()):
            report['ok'] = False
        a.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('ok', 'modes', 'failure_message', 'restored_usb_can')}))
    return report['ok']
if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', required=True, choices=['stm32h723-dual'])
    p.add_argument('--elf', required=True, type=Path)
    p.add_argument('--binary', required=True, type=Path)
    p.add_argument('--ports', nargs=2, required=True)
    p.add_argument('--modes', nargs=2, required=True, choices=['usb', 'ble', 'wifi'])
    p.add_argument('--switches', type=int, default=6)
    p.add_argument('--detail-fullscreen',action='store_true',help='Also receive physical DJI details in native X11 fullscreen and verify tab handoff')
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    if not 2 <= a.switches <= 30:
        p.error('--switches must be 2..30')
    with hardware_bench_lock():
        raise SystemExit(0 if main(a) else 1)
