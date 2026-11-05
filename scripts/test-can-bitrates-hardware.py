#!/usr/bin/env python3
"""Opt-in four-rate CAN acceptance on the debugger-controlled STM32 simulator.

Stops the fixture controller during each analyzer rate change. Uses only
arbitrary test IDs, compares actual STM32 RX and analyzer RX in both directions,
and restores 1 Mbps plus USB CAN. Requires the rate-enabled fixture ELF/bin.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import secrets
import struct
import time

from usb_management import LocalUsb
from can_scope.connection_store import connection_folder,load_connection
from can_scope.protocol import packet,u32,setting_command,can_command
from can_scope.transports import BleTransport,LanTransport,discover
from can_scope.usb_transport import UsbTransport,prepare_usb
from hardware_bench_lock import hardware_bench_lock


def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(filename))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


async def main(args):
    hardware=load('physical_fixture','test-dual-can-hardware.py')
    bench=load('can_modes','test-can-hardware.py')
    fixture=hardware.Fixture(args.elf)
    if 'can_bitrate_bench'not in fixture.symbols:
        raise ValueError('Running fixture does not support safe bitrate changes')
    bus=args.bus-1;base=fixture.symbols['can_bench']+bus*52
    control=fixture.symbols['can_bitrate_bench']+bus*24
    sequence=secrets.randbelow(0x7fffffff)+1
    report=dict(ok=False,cases=[],fixture_image_sha256=fixture.image_sha256,
                scope='physical independent STM32/analyzer CAN at four rates; arbitrary bench IDs only')
    link=None
    errors=[]
    received=[]
    def rx(p):
        received.append(bytes(p))
        if len(received)>1000:del received[:500]
    def change_fixture(rate):
        fixture.set_rate(bus,rate)
    async def wait(predicate,timeout=5):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():return
            await asyncio.sleep(.005)
        raise TimeoutError('CAN rate acceptance response missing')
    async def set_rate(rate):
        nonlocal sequence
        sequence+=1;token=sequence
        command=packet(0x12,x=rate,token=token)
        setting_command(command.hex())
        await link.write(command)
        await wait(lambda:any(p[1]==5 and u32(p,16)==token for p in received))
        response=next(p for p in received if p[1]==5 and u32(p,16)==token)
        if response[2] or u32(response,4)!=rate:
            raise AssertionError('Analyzer did not apply requested CAN bitrate')
    try:
        if any(fixture.health().values()):raise RuntimeError('Existing fixture errors')
        fixture.quiet(0);await asyncio.sleep(.1)
        saved=load_connection(connection_folder()/f'connection-{args.channel}.json')
        for mode in args.modes:
            await bench.select(args.port,mode)
            link={'usb':UsbTransport,'ble':BleTransport,'wifi':LanTransport}[mode](rx,errors.append)
            if mode=='usb':await link.connect(args.port)
            elif mode=='ble':await link.connect(saved['address'])
            else:
                await asyncio.sleep(10);found=[];await discover(saved['id'],found.append)
                if len(found)!=1:raise RuntimeError('Expected one identity-matched LAN analyzer')
                await link.connect(found[0]['address'].removeprefix('wifi:'),saved['fp'],saved['token'],identity=saved['id'])
            for rate in [125000,250000,500000,1000000]:
                report['active']=dict(mode=mode,rate=rate,stage='stop-fixture')
                change_fixture(0)
                report['active']['stage']='configure-analyzer'
                await set_rate(rate)
                report['active']['stage']='start-fixture'
                change_fixture(rate)
                await asyncio.sleep(.03)
                physical=[]
                for extended,remote in [(False,False),(True,False),(False,True),(True,True)]:
                    report['active'].update(extended=extended,remote=remote,stage='analyzer-to-stm32')
                    sequence+=1;token=sequence
                    case=args.modes.index(mode)*16+[125000,250000,500000,1000000].index(rate)*4+int(extended)*2+int(remote)
                    identifier=(0x1abc000 if extended else 0x640)+case
                    data=struct.pack('<II',token,rate);before=fixture.read(base+48)[0]
                    await link.write(can_command(identifier,data if not remote else b'',extended=extended,remote=remote,dlc=8,token=token))
                    await wait(lambda:any(p[1]in(2,3)and u32(p,16)==token for p in received))
                    result=next(p for p in received if p[1]in(2,3)and u32(p,16)==token)
                    if result[1]!=2:raise AssertionError('Analyzer CAN transmit failed')
                    await wait(lambda:fixture.read(base+48)[0]==before+1)
                    actual=struct.pack('<IIII',*fixture.read(base+32,4))
                    expected=struct.pack('<IBB8sBB',identifier,int(extended),8,bytes(8)if remote else data,int(remote),0)
                    if actual[:15]!=expected[:15]:raise AssertionError('Physical CAN frame mismatch')
                    # Same formats in the reverse direction, observed by the app transport.
                    report['active']['stage']='stm32-to-analyzer'
                    received.clear();sequence+=1
                    fixture.write(base,list(struct.unpack('<IIII',expected)))
                    fixture.write(base+16,[sequence])
                    await wait(lambda:fixture.read(base+20,2)==[sequence,0])
                    await wait(lambda:any(p[1]==1 and u32(p,4)==identifier for p in received))
                    reverse=next(p for p in received if p[1]==1 and u32(p,4)==identifier)
                    if reverse[2]!=(int(extended)|int(remote)*2)or reverse[3]!=8 or (not remote and reverse[8:16]!=data):
                        raise AssertionError('Transport CAN receive mismatch')
                    physical.append(dict(extended=extended,remote=remote,both_directions=True))
                health=fixture.health()
                if any(health.values())or errors:raise AssertionError('CAN/link error at requested rate')
                report['cases'].append(dict(mode=mode,bitrate=rate,frames=physical,health=health))
                args.output.write_text(json.dumps(report,indent=2)+'\n')
            await link.close();link=None
        report['ok']=True
        report.pop('active',None)
    except Exception as error:
        report.update(failure=type(error).__name__,failure_message=str(error)[:300],ok=False)
        report['failure_fixture_state']=fixture.read(base,13)
        report['failure_rate_state']=fixture.read(control,6)
        report['failure_responses']=[p.hex() for p in received if p[1]in(1,2,3,5,18)][-12:]
    finally:
        if link:await link.close()
        try:
            change_fixture(0)
            await prepare_usb(args.port,data=0)
            received.clear();link=UsbTransport(rx,errors.append);await link.connect(args.port)
            try:await set_rate(1000000)
            finally:await link.close();link=None
            change_fixture(1000000);fixture.quiet(1)
            report['restored_usb_can_1mbps']=True
        except Exception as error:
            report.update(restored_usb_can_1mbps=False,cleanup_error=type(error).__name__,ok=False)
        report['after']=fixture.health();report['errors']=errors
        if any(report['after'].values()) or errors:
            report['ok']=False
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(ok=report['ok'],cases=len(report['cases']),failure=report.get('failure'),restored=report.get('restored_usb_can_1mbps'))))
    return report['ok']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    parser.add_argument('--elf',required=True,type=Path)
    parser.add_argument('--port',required=True)
    parser.add_argument('--bus',type=int,choices=[1,2],required=True)
    parser.add_argument('--channel',type=int,choices=range(1,5),required=True)
    parser.add_argument('--modes',nargs='+',choices=['usb','ble','wifi'],default=['usb','ble','wifi'])
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    with hardware_bench_lock():
        raise SystemExit(0 if asyncio.run(main(args))else 1)
