#!/usr/bin/env python3
"""Opt-in physical USB reply backpressure while BLE/TLS owns CAN monitoring.

Claims only the identity-verified analyzer's CDC interface temporarily, withholds
bulk IN reads, and submits read-only management queries. No CAN write is sent.
Requires PyUSB/libusb access, a debugger-verified periodic STM32 simulator, and
saved wireless trust. Reattaches CDC and restores USB CAN even after failure.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import time
import usb.core
import usb.util

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'desktop'))
from can_scope.connection_store import connection_folder,load_connection
from can_scope.protocol import packet,u32
from can_scope.transports import BleTransport,LanTransport,discover
from can_scope.usb_protocol import Decoder,encode
from can_scope.usb_transport import prepare_usb
from hardware_bench_lock import hardware_bench_lock
from usb_management import LocalUsb


def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


class RawCdc:
    def __init__(self,identity):
        found=[]
        for device in usb.core.find(find_all=True,idVendor=0x303a,idProduct=0x1001):
            if device.serial_number.replace(':','').lower()==identity:found.append(device)
            else:usb.util.dispose_resources(device)
        if len(found)!=1:
            for device in found:usb.util.dispose_resources(device)
            raise ValueError('Expected one physical USB identity')
        self.device=found[0];self.detached=[];self.claimed=False
        interfaces={i.bInterfaceNumber:i for i in self.device.get_active_configuration()}
        data=interfaces.get(1)
        if data is None or data.bInterfaceClass!=10 or {(e.bEndpointAddress,e.bmAttributes)for e in data}!={(1,2),(129,2)}:
            usb.util.dispose_resources(self.device)
            raise ValueError('Unexpected native CDC data interface')
        try:
            for number in (0,1):
                if self.device.is_kernel_driver_active(number):
                    self.device.detach_kernel_driver(number);self.detached.append(number)
            usb.util.claim_interface(self.device,1);self.claimed=True
        except BaseException:
            self.close();raise

    def close(self):
        failure=None
        try:
            if self.claimed:usb.util.release_interface(self.device,1);self.claimed=False
            for number in self.detached:
                try:
                    if not self.device.is_kernel_driver_active(number):self.device.attach_kernel_driver(number)
                except Exception as error:failure=error
        finally:usb.util.dispose_resources(self.device)
        if failure:raise RuntimeError('CDC kernel driver restore failed')from failure

    def probe(self):
        session=secrets.randbelow(0xffffffff)+1;token=secrets.randbelow(0xffffffff)+1
        request=packet(0x1c,token=token)
        if self.device.write(1,encode(session,request),timeout=1000)!=32:
            raise OSError('Incomplete read-only probe; no replay')
        decoder=Decoder();deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            try:data=bytes(self.device.read(129,4096,timeout=100))
            except usb.core.USBTimeoutError:continue
            if any(sid==session and p[1]==27 and u32(p,16)==token for sid,p in decoder.feed(data)):return
        raise TimeoutError('Raw CDC management probe missing')

    def pressure(self):
        session=secrets.randbelow(0xffffffff)+1;first=secrets.randbelow(0xffff0000)+1
        frames=b''.join(encode(session,packet(0x1c,token=first+i))for i in range(512))
        result=dict(queries=512,request_bytes=len(frames),in_reads_withheld_seconds=2.5,write_timeout=False,valid_events=0)
        # Submit once; timeout never triggers a replay, even for these reads.
        try:result['written_bytes']=int(self.device.write(1,frames,timeout=2000))
        except usb.core.USBTimeoutError:result['write_timeout']=True
        time.sleep(2.5)
        decoder=Decoder();deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try:data=bytes(self.device.read(129,4096,timeout=150))
            except usb.core.USBTimeoutError:break
            result['valid_events']+=sum(sid==session and p[1]==27 and first<=u32(p,16)<first+512 for sid,p in decoder.feed(data))
        result['scope']='Physical bulk IN reads withheld; firmware short-write count is not directly exposed'
        return result


async def main(args):
    bench=module('can_modes','test-can-hardware.py');fixture=module('fixture','test-dual-can-hardware.py').Fixture(args.elf)
    saved=load_connection(connection_folder()/f'connection-{args.channel}.json')
    report=dict(ok=False,mode=args.mode,fixture_image_sha256=fixture.image_sha256,scope='Physical management backpressure must preserve wireless quiet mode; no CAN sends')
    link=None;raw=None;received=[];errors=[]
    def rx(p):received.append(bytes(p))
    async def wait(predicate,seconds=5):
        deadline=time.monotonic()+seconds
        while not predicate():
            if time.monotonic()>deadline:raise TimeoutError('Wireless monitoring response missing')
            await asyncio.sleep(.01)
    async def telemetry(mode):
        token=secrets.randbelow(0xffffffff)+1
        await link.write(packet(0x17,a=mode,token=token))
        await wait(lambda:any(p[1]==10 and p[2]==mode and u32(p,16)==token for p in received))
    try:
        if any(fixture.health().values()):raise RuntimeError('Existing fixture CAN error')
        physical=LocalUsb(args.port)
        try:
            identity=physical.identity().hex()
            if identity!=saved['id']:raise ValueError('USB/wireless identity mismatch')
        finally:physical.close()
        await bench.select(args.port,args.mode)
        link={'ble':BleTransport,'wifi':LanTransport}[args.mode](rx,errors.append)
        if args.mode=='ble':await link.connect(saved['address'])
        else:
            await asyncio.sleep(10);found=[];await discover(identity,found.append)
            if len(found)!=1:raise ValueError('Expected one identity-matched Wi-Fi device')
            await link.connect(found[0]['address'].removeprefix('wifi:'),saved['fp'],saved['token'],identity=identity)
        await wait(lambda:sum(p[1]==1 for p in received)>50)
        await telemetry(3);await asyncio.sleep(1);received.clear();await asyncio.sleep(.5)
        if any(p[1]==1 for p in received):raise AssertionError('Wireless quiet baseline is not quiet')
        raw=RawCdc(identity);await asyncio.to_thread(raw.probe)
        await asyncio.sleep(.5)
        if any(p[1]==1 for p in received):raise AssertionError('Claiming CDC disturbed wireless quiet mode')
        received.clear();report['pressure']=await asyncio.to_thread(raw.pressure)
        report['unexpected_raw_during_pressure']=sum(p[1]==1 for p in received)
        raw.close();raw=None
        await telemetry(0);received.clear();await wait(lambda:sum(p[1]==1 for p in received)>50)
        report['wireless_raw_recovered']=True
        report['pressure_exercised']=(report['pressure'].get('written_bytes')==report['pressure']['request_bytes']
            and 0<report['pressure']['valid_events']<report['pressure']['queries'])
        report['ok']=not errors and not report['unexpected_raw_during_pressure']and report['pressure_exercised']
    except Exception as error:report.update(failure=type(error).__name__,failure_message=str(error)[:240],ok=False)
    finally:
        if raw:
            try:raw.close()
            except Exception as error:report.update(cdc_restore_error=type(error).__name__,ok=False)
        if link:await link.close()
        try:
            deadline=time.monotonic()+5
            while not Path(args.port).exists()and time.monotonic()<deadline:await asyncio.sleep(.1)
            await prepare_usb(args.port,data=0);report['restored_usb_can']=True
        except Exception as error:report.update(restored_usb_can=False,restore_error=type(error).__name__,ok=False)
        report['errors']=[type(error).__name__ for error in errors];report['fixture_health_after']=fixture.health()
        if any(report['fixture_health_after'].values()):report['ok']=False
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));return report['ok']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    parser.add_argument('--elf',type=Path,required=True)
    parser.add_argument('--port',required=True)
    parser.add_argument('--channel',type=int,choices=range(1,5),required=True)
    parser.add_argument('--mode',choices=['ble','wifi'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    options=parser.parse_args()
    with hardware_bench_lock():raise SystemExit(0 if asyncio.run(main(options))else 1)
