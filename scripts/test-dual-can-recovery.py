#!/usr/bin/env python3
"""Opt-in physical peer restart, offline suppression and dual CAN recovery.

Holds one explicitly identified analyzer in its ROM bootloader, without writing
Flash, then restarts it. Both independent STM32 periodic senders are first
paused. No old frame may appear after reconnect; fresh frames must cross both
physical buses. Requires the same explicit SWD fixture as the delivery bench.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import re
import secrets
import struct
import subprocess
import time

from usb_management import LocalUsb
from can_scope.usb_transport import prepare_usb


def load(name, filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def state(device, op=0):
    usb=LocalUsb(device)
    try:
        return usb.dual(op)
    finally:
        usb.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--a',required=True)
    parser.add_argument('--b',required=True)
    parser.add_argument('--esptool',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    pairing=load('dual_pair','dual-can-pair.py')
    hardware=load('dual_hardware','test-dual-can-hardware.py')
    report=dict(ok=False,scope='physical ROM restart without Flash/NVS writes; no replay; independent CAN observation')
    fixture=None
    held=False
    peer=None

    def reset(before, after):
        result=subprocess.run([str(args.esptool),'--chip','esp32s3','--port',args.b,
            '--before',before,'--after',after,'--no-stub','read-mac'],capture_output=True,text=True,timeout=25)
        if result.returncode:
            raise RuntimeError('Read-only ROM operation failed')
        observed=re.search(r'MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})',result.stdout)
        if not observed or observed[1].replace(':','').lower()!=peer.hex():
            raise ValueError('ROM MAC differs from the physical USB identity')

    def online():
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                rows=[state(args.a),state(args.b)]
                if all(r['bound']and r['state']==3 and not r['uart']for r in rows):
                    if rows[0]['peer']!=peer or rows[1]['peer']!=local:
                        raise ValueError('Recovered peer identity changed')
                    return
            except (OSError,TimeoutError):
                pass
            time.sleep(.2)
        raise TimeoutError('Stored dual binding did not recover')

    try:
        local=pairing.identify(args.a)
        peer=pairing.identify(args.b)
        if local==peer:
            raise ValueError('Two independent physical analyzers required')
        fixture=hardware.Fixture(args.elf)
        report['fixture_image_sha256']=fixture.image_sha256
        report['before']=fixture.health()
        if any(report['before'].values()):
            raise RuntimeError('Fixture has existing errors; counters not cleared')
        fixture.quiet(0)
        time.sleep(.3)
        asyncio.run(pairing.select(args.b,3))
        asyncio.run(pairing.select(args.a,2))
        online()
        report['stored_pair_online']=True
        destination=fixture.symbols['can_bench']+52
        before=fixture.read(destination+48)[0]
        held=True # Reset outcome may be unknown even if the tool fails.
        reset('usb-reset','no-reset')
        time.sleep(1.5)
        if state(args.a)['state']!=2: # DUAL_UI_OFFLINE
            raise RuntimeError('A did not report the missing peer offline')
        report['offline_detected']=True
        source=fixture.symbols['can_bench']
        sequence=secrets.randbelow(0xffff)+1
        frame=struct.pack('<IBB8sBB',0x6b0,0,8,b'OFFLINE!',0,0)
        fixture.write(source,list(struct.unpack('<IIII',frame)))
        fixture.write(source+16,[sequence])
        deadline=time.monotonic()+2
        while fixture.read(source+20)[0]!=sequence:
            if time.monotonic()>=deadline:
                raise TimeoutError('Fixture did not send the offline probe')
            time.sleep(.01)
        if fixture.read(source+24)[0]:
            raise RuntimeError('Offline probe was not accepted by source CAN')
        if fixture.read(destination+48)[0]!=before:
            raise AssertionError('Offline probe unexpectedly reached destination')
        reset('no-reset','watchdog-reset')
        online()
        held=False
        time.sleep(1)
        if fixture.read(destination+48)[0]!=before:
            raise AssertionError('Old offline CAN frame replayed on recovery')
        report['old_frame_not_replayed']=True
        report['fresh_frames']=[fixture.transfer(0,0x6b1,8,0,sequence+1),
                                fixture.transfer(1,0x1abcff,8,1,sequence+2)]
        report['after']=fixture.health()
        if any(report['after'].values()):
            raise RuntimeError('CAN error during restart recovery')
        report['ok']=True
    except Exception as exc:
        report['failure']=type(exc).__name__
    finally:
        if held and peer:
            try:
                reset('no-reset','watchdog-reset')
                held=False
            except Exception as exc:
                report.update(ok=False,rom_restore_failed=type(exc).__name__)
        restored=[]
        for device in [args.a,args.b]:
            try:
                asyncio.run(prepare_usb(device,data=0))
                restored.append(True)
            except Exception:
                restored.append(False)
        report['restored_usb_can']=restored
        if fixture and all(restored) and not held:
            fixture.quiet(1)
            report['periodic_restored']=True
        if not all(restored):
            report['ok']=False
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['ok']else 1)


if __name__=='__main__':
    main()
