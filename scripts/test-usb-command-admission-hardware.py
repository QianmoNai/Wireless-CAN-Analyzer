#!/usr/bin/env python3
"""Opt-in: first bitrate readback after each USB HELLO, without added delay.

Only requests the existing 1Mbps setting on the explicit simulator bench.
A type18 admission ACK alone is insufficient; exact token/type5 must complete.
"""
import argparse,asyncio,json,time
from pathlib import Path
from usb_management import LocalUsb
from hardware_bench_lock import hardware_bench_lock
from can_scope.usb_transport import UsbTransport,prepare_usb
from can_scope.protocol import packet,u32

async def run(args):
    report=dict(ok=False,cases=[])
    try:
        await prepare_usb(args.port,data=0)
        usb=LocalUsb(args.port)
        try:report['identity']=usb.identity().hex(':')
        finally:usb.close()
        for trial in range(args.repeat):
            events=[];errors=[];link=UsbTransport(events.append,errors.append)
            try:
                await link.connect(args.port)
                token=0x36000000+trial
                started=time.monotonic()
                await link.write(packet(0x12,x=1000000,token=token))
                deadline=started+1
                while time.monotonic()<deadline:
                    replies=[p for p in events if p[1]==5 and u32(p,16)==token]
                    if replies:break
                    await asyncio.sleep(.005)
                admitted=[p[2] for p in events if p[1]==18 and p[3]==0x12 and u32(p,4)==token]
                complete=bool(replies)and replies[0][2]==0 and u32(replies[0],4)==1000000
                row=dict(trial=trial+1,admission=admitted,completed=complete,errors=errors,
                         elapsed_ms=round((time.monotonic()-started)*1000,3))
                report['cases'].append(row)
                if admitted!=[0]or not complete or errors:
                    raise AssertionError('First USB command did not complete exactly once')
            finally:await link.close()
        report['ok']=True
    except Exception as error:report['failure']=type(error).__name__
    finally:
        try:await prepare_usb(args.port,data=0);report['restored_usb_can']=True
        except Exception:report['restored_usb_can']=False;report['ok']=False
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(ok=report['ok'],cases=len(report['cases']),failure=report.get('failure'))))
    return report['ok']

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    p.add_argument('--port',required=True)
    p.add_argument('--repeat',type=int,default=20)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    if not 1<=a.repeat<=100:p.error('--repeat must be1..100')
    with hardware_bench_lock():raise SystemExit(0 if asyncio.run(run(a))else 1)
