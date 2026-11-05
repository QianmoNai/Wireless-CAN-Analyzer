#!/usr/bin/env python3
"""Opt-in actual query switch UI with physical STM32 simulated DM feedback.

The toggle must preserve the requested state while native confirmation is pending,
then resolve to confirmed state. No motor enable/motion commands are used.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'desktop')]
from hardware_bench_lock import hardware_bench_lock
from can_scope.connection_store import connection_folder,load_connection
from can_scope.transports import discover
from usb_management import LocalUsb


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--surface',required=True,choices=['desktop','android'])
    parser.add_argument('--binary',type=Path)
    parser.add_argument('--mode',required=True,choices=['usb','ble','wifi'])
    parser.add_argument('--port',required=True)
    parser.add_argument('--channel',type=int,choices=[1,2],required=True)
    parser.add_argument('--bus',type=int,choices=[1,2],required=True)
    parser.add_argument('--display',default=':1')
    parser.add_argument('--cdp',default='http://127.0.0.1:35013')
    parser.add_argument('--repeat',type=int,default=20)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.repeat<=100:parser.error('--repeat must be 1..100')
    if args.surface=='android'and args.mode=='usb':parser.error('Android USB Host is not available on this bench')
    if args.surface=='desktop'and(not args.binary or not args.binary.is_file()):parser.error('--binary must name the actual desktop executable')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    report=dict(ok=False,surface=args.surface,mode=args.mode,channel=args.channel,bus=args.bus,
                scope='Actual packaged/shared UI, physical analyzer and STM32 simulated DM status query, no motion',rounds=[],js_errors=[])
    def save():args.output.write_text(json.dumps(report,indent=2)+'\n')
    fixture=None;verified=False
    saved=load_connection(connection_folder()/f'connection-{args.channel}.json')
    def monitor():
        usb=LocalUsb(args.port)
        try:
            if usb.identity().hex()!=saved.get('id'):raise ValueError('USB identity differs from saved channel')
            return usb.monitor_drops()
        finally:usb.close()
    bench=module('query_bench',ROOT/'scripts/test-can-hardware.py')
    ui=module('query_ui',ROOT/'scripts/test-can-feedback-hardware.py')
    with hardware_bench_lock():
        try:
            report['before']=monitor();verified=True
            fixture=module('query_fixture',ROOT/'scripts/test-dual-can-hardware.py').Fixture(args.elf)
            report['fixture_image_sha256']=fixture.image_sha256
            if any(fixture.health().values()):raise RuntimeError('Existing CAN errors')
            if args.binary:report['binary_sha256']=hashlib.sha256(args.binary.read_bytes()).hexdigest()
            report['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ['android/app/src/main/assets/motor_roster.js','android/app/src/main/assets/motor_detail.js','android/app/src/main/java/dev/qianmo/canview/MainActivity.java','desktop/can_scope/engine.py']}
            asyncio.run(bench.select(args.port,args.mode))
            if args.mode=='wifi':
                time.sleep(10);found=[];asyncio.run(discover(saved['id'],found.append))
                if len(found)!=1:raise RuntimeError('Expected one identity-matched Wi-Fi peer')
                saved['ip']=found[0]['address'].removeprefix('wifi:')
            surface=ui.android_page if args.surface=='android'else ui.desktop_page
            with surface(args,saved)as page:
                page.on('pageerror',lambda e:report['js_errors'].append(str(e)))
                backup=page.evaluate('watchedMotors.map(m=>m.config)')
                try:
                    page.evaluate("()=>{window.queryAudit=[];window.queryMode=null;window.queryPriorNative=onNative;onNative=(k,d)=>{if(k==='queryState'||k==='error'){queryAudit.push({kind:k,data:d,at:Date.now()});if(queryAudit.length>32)queryAudit.shift()}if(k==='telemetryMode')queryMode=d.mode;return queryPriorNative(k,d)};watchedMotors.splice(0);commitWatch({profile:'dm4310',id:1,master:17,pollHz:100,mode:'mit',limits:{p:12.5,v:30,t:10},name:'Query switch bench'});publishDashboard();showTab('live')}")
                    page.wait_for_function('window.devicePollingSupported',polling=100,timeout=8000)
                    def state():
                        return page.evaluate("({connected,hidden:document.hidden,focused:document.hasFocus(),appDrops,firmwareDrops,polling:detailMotor&&detailMotor.polling,pending:detailMotor&&detailMotor.queryPending,target:detailMotor&&detailMotor.queryTarget,checked:$('detailQuery').checked,disabled:$('detailQuery').disabled,message:$('detailQueryNote').textContent,events:queryAudit})")
                    def wait(expression,**kw):
                        try:return page.wait_for_function(expression,polling=100,timeout=8000,**kw)
                        except Exception:report['failure_state']=state();save();raise
                    for turn in range(args.repeat):
                        row=dict(turn=turn+1);report['rounds'].append(row);save()
                        page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[0].config))')
                        wait('detailReady');page.wait_for_function("!$('detailQuery').disabled",polling=100,timeout=8000)
                        for enabled in [True,False]:
                            label='enable'if enabled else'stop';start=time.monotonic()
                            initial=page.evaluate("()=>{$('detailQuery').click();return {pending:!!detailMotor.queryPending,checked:$('detailQuery').checked,disabled:$('detailQuery').disabled}}")
                            row[label+'_initial']=initial
                            if not initial['pending']or initial['checked']!=enabled or not initial['disabled']:
                                raise AssertionError('Query switch did not preserve/lock the requested state')
                            wait('target=>!detailMotor.queryPending&&detailMotor.polling===target',arg=enabled)
                            row[label+'_confirmation_ms']=round((time.monotonic()-start)*1000,2)
                            # Native state schedules drawing; observe its DOM result
                            # separately rather than assuming a synchronous repaint.
                            wait("target=>$('detailQuery').checked===target&&!$('detailQuery').disabled",arg=enabled)
                            row[label+'_ms']=round((time.monotonic()-start)*1000,2);row[label+'_state']=state();save()
                            if enabled:
                                wait('detailSamples.length-detailSampleHead>=20&&detailMotor.feedback!==null')
                                page.wait_for_timeout(200)
                            if row[label+'_state']['checked']!=enabled or row[label+'_state']['disabled']or not row[label+'_state']['connected']or row[label+'_state']['appDrops']:
                                raise AssertionError('Confirmed query UI state differs from actual channel')
                        page.evaluate("showTab('motor')");wait('queryMode===1');page.wait_for_timeout(200)
                        print(json.dumps({key:row[key]for key in ['turn','enable_ms','stop_ms']}),flush=True)
                    if report['js_errors']:raise AssertionError('JavaScript error')
                    report['ok']=True
                finally:
                    page.evaluate("backup=>{closeMotorDetail();onNative=queryPriorNative;watchedMotors.splice(0);for(const c of backup)commitWatch(c);publishDashboard();showTab('motor')}",backup)
            report['after']=monitor();report['delta']={key:(value-report['before'][key])&0xffffffff for key,value in report['after'].items()}
            if any(report['delta'].values()):raise AssertionError('Device drop component increased')
        except Exception as error:report.update(ok=False,failure=type(error).__name__,failure_message=str(error)[:500])
        finally:
            if verified:
                try:
                    asyncio.run(bench.select(args.port,'usb'))
                    if fixture:fixture.quiet(1);report['fixture_health_after']=fixture.health()
                    if fixture and any(report['fixture_health_after'].values()):report['ok']=False
                    report['restored_usb_can_periodic']=True
                    report['after_restore']=monitor()
                    report['restore_delta']={key:(value-report['before'][key])&0xffffffff for key,value in report['after_restore'].items()}
                    if any(report['restore_delta'].values()):report['ok']=False
                except Exception as error:report.update(ok=False,restore_error=type(error).__name__)
            save()
    print(json.dumps({key:report.get(key)for key in ['ok','surface','mode','failure','failure_message']}))
    return 0 if report['ok']else 1


if __name__=='__main__':sys.exit(main())
