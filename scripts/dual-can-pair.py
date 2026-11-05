#!/usr/bin/env python3
"""Pair two physically attached analyzers without LCD/button interaction.

Both USB identities, peer MACs and independently derived SAS values must match.
Existing pairing survives failed attempts. No CAN command is sent by this tool.
Use desktop/.venv/bin/python. Close other owners before starting.
"""
import argparse
import asyncio
import json
from pathlib import Path
import secrets
import time

from usb_management import LocalUsb
from can_scope.protocol import device_settings_command, u32
from can_scope.usb_transport import UsbTransport, prepare_usb


async def select(device, role):
    await prepare_usb(device, data=0)
    done = asyncio.Event()
    token = secrets.randbelow(0xffffffff)+1
    result = []
    def received(p):
        if p[1] == 22 and u32(p,16) == token:
            result.append(p)
            done.set()
    link = UsbTransport(received, lambda _: None)
    try:
        await link.connect(device)
        await link.write(device_settings_command(network=role, data=0, token=token))
        await asyncio.wait_for(done.wait(), 3)
        if result[0][2] != 1 or result[0][3] != role or result[0][4]:
            raise RuntimeError('Dual CAN setting did not commit')
    finally:
        await link.close()


def identify(device):
    usb = LocalUsb(device)
    try:
        return usb.identity()
    finally:
        usb.close()


def pair(a, b):
    macs = [identify(a), identify(b)]
    if macs[0] == macs[1]:
        raise ValueError('Two independent analyzers are required')
    asyncio.run(select(b, 3))
    asyncio.run(select(a, 2))
    time.sleep(1)
    ports = [LocalUsb(a), LocalUsb(b)]
    tokens = [secrets.randbelow(0xffffffff)+1 for _ in ports]
    try:
        def command(i, op, code=0):
            return ports[i].dual(op, role=i+2, peer=macs[1-i], token=tokens[i], code=code)
        command(1, 1)
        command(0, 1)
        deadline = time.monotonic()+40
        selected = False
        while time.monotonic()<deadline:
            states = [p.dual() for p in ports]
            if not selected and states[0]['state']==4:
                # Selection names the known physically attached B, never index 0.
                try:
                    command(0, 2)
                    selected = True
                except ValueError:
                    # Only selection admission may be rejected before its beacon.
                    # It has no effect on rejection; BEGIN/CONFIRM are never replayed.
                    pass
            if all(s['state']==7 for s in states):
                if states[0]['code']!=states[1]['code'] or any(s['peer']!=macs[1-i] for i,s in enumerate(states)):
                    raise ValueError('Pairing transcript/peer mismatch; refusing confirmation')
                command(0, 3, states[0]['code'])
                command(1, 3, states[1]['code'])
                break
            time.sleep(.15)
        else:
            raise TimeoutError('Pairing did not reach independently matching confirmations')
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            states = [p.dual() for p in ports]
            if all(s['bound'] and s['state']==3 and not s['uart'] for s in states):
                return dict(ok=True, peers=[m.hex(':') for m in macs],
                            identity_checked=True, sas_matched=True,
                            statistics=[p.dual(6) for p in ports])
            time.sleep(.15)
        raise TimeoutError('Both devices did not persist and become online')
    finally:
        for p in ports:
            p.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True)
    ap.add_argument('--keep-dual', action='store_true')
    ap.add_argument('--output', type=Path, required=True)
    args=ap.parse_args()
    report=dict(ok=False)
    try:
        report=pair(args.a,args.b)
    except Exception as e:
        report['failure']=type(e).__name__
    finally:
        if not report['ok'] or not args.keep_dual:
            restored=[]
            for device in [args.a,args.b]:
                try:
                    asyncio.run(prepare_usb(device,data=0));restored.append(True)
                except Exception:
                    restored.append(False)
            report['restored_usb_can']=restored
            if not all(restored):
                report['ok']=False
                report['restore_failed']=True
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    raise SystemExit(0 if report['ok'] else 1)


if __name__=='__main__':
    main()
