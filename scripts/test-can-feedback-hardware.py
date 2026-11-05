#!/usr/bin/env python3
"""Opt-in numeric feedback checks through physical STM32 CAN and actual UI.

Requires the dual-bus STM32H723 simulator, OpenOCD Tcl on localhost:6666,
Playwright and desktop transport dependencies. Android requires an already
authorized debug bench APK, saved trust and its forwarded WebView CDP port.
Only simulated feedback is sent; no motor query or motion command is issued.
"""
import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'desktop'))
from can_scope.connection_store import connection_folder,load_connection,save_connection
from hardware_bench_lock import hardware_bench_lock
from motor_feedback_reference import exercise
from usb_management import LocalUsb


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def disconnect(page):
    if not page.is_closed():
        page.evaluate("invoke('disconnect')")
        page.wait_for_function('!connected',polling=100,timeout=5000)


@contextmanager
def android_page(args,saved):
    with sync_playwright()as pw:
        browser=pw.chromium.connect_over_cdp(args.cdp,no_defaults=True)
        try:
            pages=[p for c in browser.contexts for p in c.pages]
            origin='https://app.local/'if args.channel==1 else f'https://channel-{args.channel}.local/'
            page=next(p for p in pages if p.url.startswith(origin))
            home=next(p for p in pages if '/home.html'in p.url)
            home.evaluate('(channel)=>HomeAndroid.openChannel(channel)',args.channel)
            if page.evaluate('connected'):
                raise RuntimeError('Bench channel must be disconnected')
            page.reload(wait_until='load')
            page.wait_for_function("document.body.textContent.includes('准备连接 ESP32-CAN')",polling=100)
            page.evaluate("showTab('live')")
            try:
                if args.mode=='ble':
                    page.evaluate("invoke('connectionMode','ble');invoke('scan')")
                    page.wait_for_function('(address)=>deviceMap.has(address)',arg=saved['address'],timeout=25000)
                    page.evaluate("address=>invoke('connect',address)",saved['address'])
                else:
                    page.evaluate("ip=>invoke('connectLan',ip)",saved['ip'])
                page.wait_for_function('connected&&gotStatus',polling=100,timeout=30000)
                yield page
            finally:
                disconnect(page)
        finally:
            browser.close()


@contextmanager
def desktop_page(args,saved):
    with tempfile.TemporaryDirectory(prefix='can-feedback-')as temporary:
        saved=dict(saved,mode=args.mode,usb_port=args.port)
        save_connection(Path(temporary)/'Qianmo/CAN Scope Desktop/connection-1.json',saved)
        with socket.socket()as sock:
            sock.bind(('127.0.0.1',0));debug=sock.getsockname()[1]
        env=dict(os.environ,XDG_DATA_HOME=temporary,QT_QPA_PLATFORM='xcb',DISPLAY=args.display,
                 XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}',
                 DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{os.getuid()}/bus',
                 QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{debug}')
        with args.output.with_suffix('.qt.log').open('w')as log:
            process=subprocess.Popen([str(args.binary.resolve())],env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+15
                while True:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{debug}/json/version',timeout=.2)as response:
                            json.load(response)
                        break
                    except (OSError,ValueError):
                        if process.poll()is not None or time.monotonic()>deadline:
                            raise TimeoutError('Desktop debugger unavailable')
                        time.sleep(.1)
                with sync_playwright()as pw:
                    browser=pw.chromium.connect_over_cdp(f'http://127.0.0.1:{debug}',no_defaults=True)
                    try:
                        page=browser.contexts[0].pages[0]
                        page.wait_for_function("window.CanAndroid&&typeof CanAndroid.connect==='function'",polling=100)
                        page.evaluate("showTab('live')")
                        target=('usb:'+args.port if args.mode=='usb'else
                                'wifi:'+saved['ip']if args.mode=='wifi'else saved['address'])
                        try:
                            page.evaluate("target=>invoke('connect',target)",target)
                            page.wait_for_function('connected&&gotStatus',polling=100,timeout=35000)
                            yield page
                        finally:
                            disconnect(page)
                    finally:
                        browser.close()
            finally:
                process.terminate()
                try:process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    parser.add_argument('--elf',required=True,type=Path)
    parser.add_argument('--surface',required=True,choices=['android','desktop'])
    parser.add_argument('--mode',required=True,choices=['usb','ble','wifi'])
    parser.add_argument('--port',required=True)
    parser.add_argument('--bus',type=int,choices=[1,2],required=True)
    parser.add_argument('--channel',type=int,choices=range(1,5),default=1)
    parser.add_argument('--binary',type=Path,help='Required for desktop; actual installed/package executable')
    parser.add_argument('--display',default=':1')
    parser.add_argument('--cdp',default='http://127.0.0.1:35013',help='Android debug bench WebView only')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--invalid-frames',action='store_true',help='Also require physical receipt of EXT/RTR/short/mismatched frames without updating any monitored parameter')
    args=parser.parse_args()
    if args.surface=='android'and args.mode=='usb':parser.error('Android USB host is not supported by this bench')
    if args.surface=='desktop'and(not args.binary or not args.binary.is_file()):parser.error('--binary must name a desktop executable')
    if not args.elf.is_file():parser.error('--elf must name the running dual-bus fixture ELF')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report=dict(ok=False,surface=args.surface,mode=args.mode,channel=args.channel,bus=args.bus,
                scope='Known simulated motor/IMU feedback over physical CAN, actual UI and independent Python reference; not actual motors/IMU',
                cases=[],js_errors=[],visible_number_precision_verified=False)
    report['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                             for name in ['scripts/test-can-feedback-hardware.py','scripts/motor_feedback_reference.py']}
    def save(rows=None):
        if rows is not None:report['cases']=rows
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    saved=load_connection(connection_folder()/f'connection-{args.channel}.json')
    def monitor():
        usb=LocalUsb(args.port)
        try:
            if usb.identity().hex()!=saved['id']:
                raise ValueError('Physical USB identity differs from selected UI channel')
            return usb.monitor_drops()
        finally:usb.close()
    with hardware_bench_lock():
        fixture=module('feedback_fixture',ROOT/'scripts/test-dual-can-hardware.py').Fixture(args.elf)
        report['fixture_image_sha256']=fixture.image_sha256
        if args.binary:report['binary_sha256']=hashlib.sha256(args.binary.read_bytes()).hexdigest()
        bench=module('feedback_transport',ROOT/'scripts/test-can-hardware.py')
        # Refuse a mismatched physical identity before entering any mode-change
        # or cleanup path that could mutate the wrong analyzer.
        report['before']=monitor()
        try:
            report['fixture_health_before']=fixture.health()
            if any(report['fixture_health_before'].values()):raise RuntimeError('Fixture has existing CAN errors')
            fixture.quiet(0);time.sleep(.15)
            asyncio.run(bench.select(args.port,args.mode))
            if args.mode=='wifi':
                time.sleep(10)
                from can_scope.transports import discover
                found=[];asyncio.run(discover(saved['id'],found.append))
                if len(found)!=1:raise RuntimeError('Expected one identity-verified Wi-Fi peer')
                saved['ip']=found[0]['address'].removeprefix('wifi:')
            surface=android_page if args.surface=='android'else desktop_page
            with surface(args,saved)as page:
                page.on('pageerror',lambda e:report['js_errors'].append(str(e)))
                exercise(page,fixture,args.bus-1,save,invalid_frames=args.invalid_frames)
            report['after']=monitor()
            report['delta']={k:(v-report['before'][k])&0xffffffff for k,v in report['after'].items()}
            if any(report['delta'].values())or report['js_errors']:
                raise AssertionError('Drop or JavaScript error during feedback validation')
            report.update(ok=True,visible_number_precision_verified=True)
        except Exception as error:
            report.update(failure=type(error).__name__,failure_message=str(error)[:300])
        finally:
            try:
                asyncio.run(bench.select(args.port,'usb'));fixture.quiet(1)
                report['restored_usb_can_periodic']=True
                report['fixture_health_after']=fixture.health()
                if any(report['fixture_health_after'].values()):report['ok']=False
            except Exception as error:report.update(ok=False,restore_error=type(error).__name__)
            save()
    print(json.dumps({key:report.get(key)for key in ['ok','surface','mode','failure','failure_message']}))
    return 0 if report['ok']else 1


if __name__=='__main__':sys.exit(main())
