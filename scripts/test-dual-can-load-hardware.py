#!/usr/bin/env python3
"""Opt-in sustained, sequence-checked physical CAN via two paired analyzers.

Requires the dual STM32H723 image with dual_load_bench, two independent CAN
buses and OpenOCD Tcl. Test IDs 0x6A0/0x6B0 carry only a run nonce and sequence.
No motor commands, configuration replay, firmware flash or counter reset.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'desktop'))
from can_scope.usb_transport import prepare_usb
from hardware_bench_lock import hardware_bench_lock
from usb_management import LocalUsb

STATS=('tx_queued tx_busy tx_errors skipped_slots tx_done_ms rx_unique '
       'rx_duplicates rx_out_of_order rx_gap_events rx_bad rx_stale '
       'rx_last_sequence rx_first_ms rx_last_ms').split()
HEAD='command rate_hz frames nonce request done result active started_ms finished_ms'.split()


def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/filename)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--a',required=True)
    parser.add_argument('--b',required=True)
    parser.add_argument('--rates',type=int,nargs='+',default=[20,50,100,200,500,1000])
    parser.add_argument('--seconds',type=int,default=10)
    parser.add_argument('--stop-file',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not 1<=args.seconds<=60 or any(not 1<=r<=1000 for r in args.rates):
        parser.error('Duration 1..60 seconds; rates 1..1000 frames/s/direction')
    ports=[args.a,args.b];fixture=None;base=None;identities=[]
    report=dict(ok=False,scope='STM32 generates simultaneous paced source streams; independent physical CAN sinks verify nonce, every sequence, duplicates and order. Requested rate is separate from observed rate; no distance/motor proof',phases=[])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def save():args.output.write_text(json.dumps(report,indent=2)+'\n')
    def usb_read(i):
        u=LocalUsb(ports[i])
        try:
            identity=u.identity()
            if identities and identity!=identities[i]:raise ValueError('Physical USB identity changed')
            return dict(identity=identity.hex(),dual=u.dual(6),diagnostics=u.diagnostics(),monitor=u.monitor_drops())
        finally:u.close()
    def snapshot():
        before=time.monotonic();words=fixture.read(base,38);after=time.monotonic()
        return dict(**dict(zip(HEAD,words[:10])),bus=[dict(zip(STATS,words[10+i*14:24+i*14]))for i in range(2)],host_before_s=before,host_after_s=after)
    def command(op,rate=0,count=0,nonce=0):
        request=fixture.next_request(base+16,base+20)
        config=[op,rate,count,nonce]if op else [0,*fixture.read(base+4,3)]
        fixture.write(base,config)
        if fixture.read(base,4)!=config:raise RuntimeError('Load configuration write mismatch')
        fixture.write(base+16,[request]);deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if fixture.read(base+20)[0]==request:
                result=fixture.read(base+24)[0]
                if result:raise RuntimeError('Load request rejected: '+str(result))
                return
            time.sleep(.005)
        raise TimeoutError('Load outcome unknown; refusing overwrite/replay')
    def delta(end,begin):return {k:(end[k]-v)&0xffffffff for k,v in begin.items()}
    hardware=module('sustained_fixture','test-dual-can-hardware.py')
    pairing=module('sustained_pairing','dual-can-pair.py')
    with hardware_bench_lock():
        fixture=hardware.Fixture(args.elf)
        if 'dual_load_bench'not in fixture.symbols:raise ValueError('Fixture has no continuous-load instrumentation')
        base=fixture.symbols['dual_load_bench'];report['fixture_image_sha256']=fixture.image_sha256
        # Verify both physical USB identities before any mode change/cleanup.
        for port in ports:
            u=LocalUsb(port)
            try:identities.append(u.identity())
            finally:u.close()
        if identities[0]==identities[1]:raise ValueError('Two independent analyzers required')
        report['identities']=[m.hex(':')for m in identities]
        report['health_before']=fixture.health()
        if any(report['health_before'].values()):raise RuntimeError('Existing CAN errors; counters not cleared')
        if snapshot()['active']:raise RuntimeError('Existing load run must be explicitly stopped by its owner')
        try:
            fixture.quiet(0)
            asyncio.run(pairing.select(args.b,3));asyncio.run(pairing.select(args.a,2))
            deadline=time.monotonic()+15
            while True:
                ready=[]
                for i,port in enumerate(ports):
                    u=LocalUsb(port)
                    try:
                        d=u.dual();ready.append(d['state']==3 and d['bound']and not d['uart']and d['role']==i+2 and d['peer']==identities[1-i])
                    finally:u.close()
                if all(ready):break
                if time.monotonic()>deadline:raise TimeoutError('Identity-matched stored binding not online')
                time.sleep(.1)
            for rate in args.rates:
                if args.stop_file and args.stop_file.exists():raise InterruptedError('User requested stop')
                phase=dict(requested_per_direction_hz=rate,seconds=args.seconds,frames_per_direction=rate*args.seconds,ticks=[],before=[usb_read(i)for i in range(2)])
                report['phases'].append(phase);save()
                nonce=secrets.randbelow(0xffffffff)+1
                if nonce==snapshot()['nonce']:nonce=nonce%0xffffffff+1
                phase['nonce']=nonce
                phase_error=None
                try:
                    command(1,rate,rate*args.seconds,nonce)
                    deadline=time.monotonic()+args.seconds+max(3,args.seconds*.25)
                    while True:
                        row=snapshot();phase['ticks'].append(row);save()
                        if row['result']:raise RuntimeError('Source load stopped: '+str(row['result']))
                        if args.stop_file and args.stop_file.exists():raise InterruptedError('User requested stop')
                        if row['active']==2 and all(b['rx_unique']==rate*args.seconds for b in row['bus']):break
                        if time.monotonic()>deadline:raise TimeoutError('Source or physical destination did not complete the paced stream')
                        time.sleep(.25)
                except Exception as e:
                    phase_error=e
                    phase['failure']=type(e).__name__
                    phase['failure_message']=str(e)[:240]
                    phase['before_stop']=snapshot()
                finally:
                    # Freeze counters only after explicit source STOP. Then let
                    # the bounded wireless/CAN queues settle before accounting.
                    command(0);phase['frozen']=snapshot();time.sleep(.2)
                    phase['after']=[usb_read(i)for i in range(2)]
                    phase['health']=fixture.health()
                    phase['deltas']=[{kind:delta(end[kind],begin[kind])for kind in ['diagnostics','monitor']}for begin,end in zip(phase['before'],phase['after'])]
                    phase['dual_deltas']=[{k:(end['dual'][k]-begin['dual'][k])&0xffffffff for k in ['sent','received','dropped']}for begin,end in zip(phase['before'],phase['after'])]
                    save()
                n=rate*args.seconds;frozen=phase['frozen']
                phase['physical_complete']=all(b['tx_queued']==n and b['rx_unique']==n and not any(b[k]for k in ['tx_errors','rx_duplicates','rx_out_of_order','rx_gap_events','rx_bad','rx_stale'])for b in frozen['bus'])
                phase['observed_per_direction_hz']=[]
                for b in frozen['bus']:
                    elapsed=(b['tx_done_ms']-frozen['started_ms'])&0xffffffff
                    receive=(b['rx_last_ms']-b['rx_first_ms'])&0xffffffff
                    phase['observed_per_direction_hz'].append(dict(source=(n-1)*1000/elapsed if elapsed else None,destination=(b['rx_unique']-1)*1000/receive if receive and b['rx_unique']>1 else None))
                phase['requested_rate_qualified']=all(b['source']is not None and b['source']>=rate*.98 for b in phase['observed_per_direction_hz'])
                phase['no_new_device_drops']=all(not any(d['monitor'].values())for d in phase['deltas'])
                phase['accounting_complete']=all(d==dict(sent=n,received=n,dropped=0)for d in phase['dual_deltas'])
                phase['ok']=phase_error is None and phase['physical_complete']and phase['requested_rate_qualified']and phase['no_new_device_drops']and phase['accounting_complete']and not any(phase['health'].values())
                save();print(json.dumps({k:phase[k]for k in ['requested_per_direction_hz','ok','observed_per_direction_hz','dual_deltas']}),flush=True)
                if phase_error:raise phase_error
                if not phase['ok']:raise AssertionError('Paced physical load qualification failed; see separate source, sink and accounting results')
            report['ok']=True
        except Exception as e:
            report.update(failure=type(e).__name__,failure_message=str(e)[:240])
            if isinstance(e,InterruptedError):report['user_stop']=True
        finally:
            try:command(0);report['load_stopped']=True
            except Exception as e:report['stop_failure']=type(e).__name__;report['ok']=False
            restored=[]
            for port in ports:
                try:asyncio.run(prepare_usb(port,data=0));restored.append(True)
                except Exception:restored.append(False)
            report['restored_usb_can']=restored
            if all(restored)and report.get('load_stopped'):fixture.quiet(1);report['periodic_restored']=True
            report['health_after']=fixture.health()
            report['ok']=report['ok']and all(restored)and not any(report['health_after'].values());save()
    print(json.dumps(dict(ok=report['ok'],phases=len(report['phases']),failure=report.get('failure'))))
    return 0 if report['ok']else 1


if __name__=='__main__':
    raise SystemExit(main())
