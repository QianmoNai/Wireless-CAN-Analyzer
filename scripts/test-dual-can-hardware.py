#!/usr/bin/env python3
"""Opt-in physical dual CAN acceptance using the explicit STM32H723 SWD fixture.

Two independent STM32 controllers send/observe actual CAN frames. USB admission
or ESP-NOW ACK alone cannot pass a case. No motor control commands are issued.
Run after dual-can-pair.py --keep-dual. Always attempts USB+CAN restoration.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import tempfile
import time

from usb_management import LocalUsb
from can_scope.usb_transport import prepare_usb


async def configure_rate(device,rate):
    """Submit one rate change and require its correlated completion."""
    from can_scope.usb_transport import UsbTransport
    from can_scope.protocol import packet,u32
    received=[]
    link=UsbTransport(received.append,lambda _:None)
    token=secrets.randbelow(0xffffffff)+1
    try:
        await link.connect(device)
        await link.write(packet(0x12,x=rate,token=token))
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            response=next((p for p in received if p[1]==5 and u32(p,16)==token),None)
            if response is not None:
                if response[2] or u32(response,4)!=rate:
                    raise RuntimeError('Analyzer rejected CAN bitrate')
                return
            await asyncio.sleep(.01)
        raise TimeoutError('Analyzer bitrate completion missing; outcome unknown')
    finally:
        await link.close()


class Fixture:
    def __init__(self, elf):
        output=subprocess.check_output(['arm-none-eabi-nm','-n',str(elf)],text=True)
        self.symbols={p[2]:int(p[0],16) for line in output.splitlines()
                      if len(p:=line.split())==3 and re.fullmatch('[0-9a-fA-F]+',p[0])}
        for key in ['can_bench','can1_periodic','can2_periodic','fatal_error']:
            if key not in self.symbols:
                raise ValueError('Expected instrumented dual-bus fixture ELF')
        image=elf.with_suffix('.bin').read_bytes()
        with tempfile.TemporaryDirectory(prefix='can-fixture-verify-') as d:
            target=Path(d)/'running.bin'
            self.command(f'dump_image {{{target}}} 0x08000000 {len(image)}')
            if target.read_bytes()!=image:
                raise ValueError('Running STM32 image differs from fixture ELF/bin')
        self.image_sha256=hashlib.sha256(image).hexdigest()

    @staticmethod
    def command(command):
        with socket.create_connection(('127.0.0.1',6666),2) as link:
            link.settimeout(5)
            link.sendall(command.encode()+b'\x1a')
            reply=b''
            while b'\x1a' not in reply:
                block=link.recv(8192)
                if not block:
                    raise ConnectionError('SWD control disconnected')
                reply+=block
        return reply.decode().rstrip('\x1a')

    def read(self,address,count=1):
        text=self.command(f'capture {{mdw 0x{address:x} {count}}}')
        words=[]
        for line in text.splitlines():
            if re.match(r'0x[0-9a-f]+:',line):
                words.extend(int(x,16) for x in line.split(':',1)[1].split())
        if len(words)!=count:
            raise ValueError('SWD response length mismatch')
        return words

    def write(self,address,values):
        for i,value in enumerate(values):
            self.command(f'mww 0x{address+4*i:x} 0x{value:x}')

    def quiet(self,value):
        for symbol in ['can1_periodic','can2_periodic']:
            self.write(self.symbols[symbol],[value])

    def set_rate(self,bus,rate):
        """Bench-only stop/start handshake; caller coordinates analyzer timing."""
        if bus not in (0,1)or rate not in (0,125000,250000,500000,1000000):
            raise ValueError('Unsupported fixture rate/bus')
        if 'can_bitrate_bench'not in self.symbols:
            raise ValueError('Fixture does not support bitrate control')
        base=self.symbols['can_bitrate_bench']+bus*24
        request=secrets.randbelow(0xffffffff)+1
        if request==self.read(base+8)[0]:request=(request%0xffffffff)+1
        self.write(base,[rate,request])
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            state=self.read(base,6)
            if state[2]==request:
                if state[3] or state[4]!=rate or bool(state[5])!=(rate==0):
                    raise RuntimeError('Fixture rejected bitrate transition')
                return
            time.sleep(.005)
        raise TimeoutError('Fixture bitrate handshake missing')

    def health(self):
        names=['fatal_error','can_bus_off','can_errors','can_rx_lost',
               'can2_bus_off','can2_errors','can2_rx_lost']
        return {key:self.read(self.symbols[key])[0] for key in names}

    def transfer(self,bus,identifier,dlc,flags,sequence):
        source=self.symbols['can_bench']+bus*52
        dest=self.symbols['can_bench']+(1-bus)*52
        before=self.read(dest+48)[0]
        remote=bool(flags&2)
        data=bytes(8) if remote else bytes((sequence*17+i*29)&255 for i in range(dlc))+bytes(8-dlc)
        frame=struct.pack('<IBB8sBB',identifier,flags&1,dlc,data,int(remote),0)
        self.active_case=dict(bus=bus,id=hex(identifier),dlc=dlc,flags=flags,
                              expected_frame=frame.hex())
        words=list(struct.unpack('<IIII',frame))
        self.write(source,words)
        if self.read(source,4)!=words:
            raise RuntimeError('SWD request was not written intact')
        started=time.monotonic()
        self.write(source+16,[sequence])
        deadline=started+2
        delivered=False
        while time.monotonic()<deadline:
            state=self.read(source+20,3)
            if state[0]==sequence:
                if state[1]:
                    raise RuntimeError('STM32 CAN enqueue failed')
                delivered=True
                break
            time.sleep(.005)
        if not delivered:
            raise TimeoutError('STM32 source did not accept fixture request')
        while time.monotonic()<deadline:
            actual=struct.pack('<IIII',*self.read(dest+32,4))
            received=self.read(dest+48)[0]
            # Byte 15 is C struct alignment padding, not part of a CAN frame.
            if received>before and actual[:15]==frame[:15]:
                if received!=before+1:
                    raise RuntimeError('Unexpected extra/duplicate CAN delivery')
                return dict(direction=f'{bus+1}->{2-bus}',id=hex(identifier),
                            dlc=dlc,flags=flags,exact_frame=True,
                            received_delta=received-before,
                            observer_upper_bound_ms=round((time.monotonic()-started)*1000,3))
            time.sleep(.005)
        self.active_case.update(source_state=self.read(source,8),
                                destination_state=self.read(dest,13))
        raise TimeoutError('Expected frame not observed on destination physical CAN')

    def transfer_pair(self,identifier,dlc,flags,sequence):
        """Start both physical controllers without waiting for either delivery."""
        bases=[self.symbols['can_bench']+bus*52 for bus in range(2)]
        before=[self.read(base+48)[0] for base in bases]
        frames=[]
        remote=bool(flags&2)
        for bus,base in enumerate(bases):
            data=bytes(8) if remote else bytes((sequence*17+bus*61+i*29)&255 for i in range(dlc))+bytes(8-dlc)
            frame=struct.pack('<IBB8sBB',identifier,flags&1,dlc,data,int(remote),0)
            frames.append(frame)
            self.write(base,list(struct.unpack('<IIII',frame)))
        self.active_case=dict(simultaneous=True,id=hex(identifier),dlc=dlc,flags=flags,
                              expected_frames=[frame.hex() for frame in frames])
        started=time.monotonic()
        for base in bases:
            self.write(base+16,[sequence])
        rows=[None,None]
        while time.monotonic()-started<2:
            for bus in range(2):
                state=self.read(bases[bus]+20,3)
                if state[0]!=sequence:
                    continue
                if state[1]:
                    raise RuntimeError('STM32 CAN enqueue failed')
                dest=1-bus
                actual=struct.pack('<IIII',*self.read(bases[dest]+32,4))
                count=self.read(bases[dest]+48)[0]
                if count>before[dest] and actual[:15]==frames[bus][:15]:
                    if count!=before[dest]+1:
                        raise RuntimeError('Unexpected extra/duplicate simultaneous delivery')
                    if rows[bus] is None:
                        rows[bus]=dict(direction=f'{bus+1}->{2-bus}',id=hex(identifier),dlc=dlc,
                                       flags=flags,exact_frame=True,received_delta=1,simultaneous=True,
                                       observer_upper_bound_ms=round((time.monotonic()-started)*1000,3))
            if all(rows):
                return rows
            time.sleep(.005)
        self.active_case['states']=[self.read(base,13) for base in bases]
        raise TimeoutError('Simultaneous frame not observed on destination physical CAN')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--fixture',required=True,choices=['stm32h723-dual'])
    ap.add_argument('--elf',type=Path,required=True)
    ap.add_argument('--a',required=True)
    ap.add_argument('--b',required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--repeat',type=int,default=1,help='Repeat the 50-case physical matrix (1..100)')
    ap.add_argument('--simultaneous',action='store_true',help='Start both controllers before awaiting physical delivery')
    ap.add_argument('--connect-stored',action='store_true',help='Quiet fixture first, then enter A/B mode with the saved binding')
    ap.add_argument('--rates',nargs=2,type=int,choices=[125000,250000,500000,1000000],metavar=('A','B'),
                    help='Rate-enabled fixture: independent CAN bitrates; requires --connect-stored; restores 1 Mbps')
    args=ap.parse_args()
    if not 1<=args.repeat<=100:
        ap.error('--repeat must be 1..100')
    if args.rates and not args.connect_stored:
        ap.error('--rates requires --connect-stored')
    report=dict(ok=False,scope='physical independent STM32 CAN1/CAN2 and two ESP32 analyzers',
                cases=[],latency_scope='SWD observer upper bound; not pure wireless latency')
    fixture=None
    try:
        fixture=Fixture(args.elf)
        report['fixture_image_sha256']=fixture.image_sha256
        report['before']=fixture.health()
        if any(report['before'].values()):
            raise RuntimeError('Fixture has existing CAN errors; no counters cleared')
        fixture.quiet(0)
        time.sleep(.3)
        report['dual_before']=[]
        report['diagnostics_before']=[]
        identities=[]
        for device in [args.a,args.b]:
            usb=LocalUsb(device)
            try:
                identities.append(usb.identity())
            finally:
                usb.close()
        if identities[0]==identities[1]:
            raise ValueError('Two independent USB identities are required')
        report['identities']=[m.hex(':') for m in identities]
        if args.rates:
            if 'can_bitrate_bench'not in fixture.symbols:
                raise ValueError('Fixture does not support coordinated bitrate changes')
            report['can_bitrates']=args.rates
            for bus,(device,rate)in enumerate(zip([args.a,args.b],args.rates)):
                asyncio.run(prepare_usb(device,data=0))
                fixture.set_rate(bus,0)
                asyncio.run(configure_rate(device,rate))
                fixture.set_rate(bus,rate)
        if args.connect_stored:
            spec=importlib.util.spec_from_file_location('pair_setup',Path(__file__).with_name('dual-can-pair.py'))
            pairing=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(pairing)
            asyncio.run(pairing.select(args.b,3))
            asyncio.run(pairing.select(args.a,2))
            deadline=time.monotonic()+15
            while True:
                online=[]
                for device in [args.a,args.b]:
                    usb=LocalUsb(device)
                    try:
                        online.append(usb.dual()['state']==3)
                    finally:
                        usb.close()
                if all(online):
                    break
                if time.monotonic()>=deadline:
                    raise TimeoutError('Stored dual binding did not reconnect')
                time.sleep(.1)
        for index,device in enumerate([args.a,args.b]):
            usb=LocalUsb(device)
            try:
                state=usb.dual()
                if state['state']!=3 or not state['bound'] or state['uart']:
                    raise RuntimeError('Both devices must be paired and online in CAN mode')
                if state['role']!=index+2 or state['peer']!=identities[1-index]:
                    raise RuntimeError('Stored peer/role differs from the attached fixture')
                report['dual_before'].append(usb.dual(6))
                report['diagnostics_before'].append(usb.diagnostics())
            finally:
                usb.close()
        sequence=secrets.randbelow(0xffff)+1
        vectors=[(0x6a0+n,n,0) for n in range(9)]+[(0x1abc000+n,n,1) for n in range(9)]
        vectors+=[(0,0,0),(0x7ff,8,0),(0x1fffffff,8,1),
                  (0x610,0,2),(0x611,8,2),(0x1abcd00,0,3),(0x1abcd01,8,3)]
        for repeat in range(args.repeat):
            if args.simultaneous:
                for identifier,dlc,flags in vectors:
                    sequence+=1
                    rows=fixture.transfer_pair(identifier,dlc,flags,sequence)
                    for row in rows:
                        row['round']=repeat+1
                        report['cases'].append(row)
                continue
            for bus in range(2):
                for identifier,dlc,flags in vectors:
                    sequence+=1
                    row=fixture.transfer(bus,identifier,dlc,flags,sequence)
                    row['round']=repeat+1
                    report['cases'].append(row)
        report['after']=fixture.health()
        report['diagnostics_after']=[]
        for device in [args.a,args.b]:
            usb=LocalUsb(device)
            try:
                report['diagnostics_after'].append(usb.diagnostics())
            finally:
                usb.close()
        if any(report['after'].values()):
            raise RuntimeError('Fixture CAN error during physical delivery')
        expected=len(vectors)*args.repeat
        deadline=time.monotonic()+1
        while True:
            report['dual_after']=[]
            for device in [args.a,args.b]:
                usb=LocalUsb(device)
                try:
                    report['dual_after'].append(usb.dual(6))
                finally:
                    usb.close()
            report['dual_delta']=[{k:(end[k]-begin[k])&0xffffffff
                for k in ['sent','received','dropped']} for begin,end in
                zip(report['dual_before'],report['dual_after'])]
            if all(d==dict(sent=expected,received=expected,dropped=0) for d in report['dual_delta']):
                break
            if time.monotonic()>=deadline:
                raise AssertionError('Dual CAN accounting differs from physical deliveries')
            time.sleep(.05)
        report['ok']=True
    except Exception as e:
        report['failure']=type(e).__name__
        if fixture:
            report['failed_case']=getattr(fixture,'active_case',None)
            try:
                report['failure_health']=fixture.health()
                report['dual_failure']=[]
                report['diagnostics_failure']=[]
                for device in [args.a,args.b]:
                    usb=LocalUsb(device)
                    try:
                        report['dual_failure'].append(usb.dual(6))
                        report['diagnostics_failure'].append(usb.diagnostics())
                    finally:
                        usb.close()
            except Exception as capture_error:
                report['failure_capture_error']=type(capture_error).__name__
    finally:
        restored=[]
        for device in [args.a,args.b]:
            try:
                asyncio.run(prepare_usb(device,data=0));restored.append(True)
            except Exception:
                restored.append(False)
        report['restored_usb_can']=restored
        if fixture and args.rates and all(restored):
            rate_restored=[]
            for bus,device in enumerate([args.a,args.b]):
                try:
                    fixture.set_rate(bus,0)
                    asyncio.run(configure_rate(device,1000000))
                    fixture.set_rate(bus,1000000)
                    rate_restored.append(True)
                except Exception:
                    rate_restored.append(False)
            report['restored_1mbps']=rate_restored
            if not all(rate_restored):report['ok']=False
        if fixture and all(restored) and all(report.get('restored_1mbps',[True])):
            fixture.quiet(1)
            report['periodic_restored']=True
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(ok=report['ok'],cases=len(report['cases']),
                          failure=report.get('failure'),restored=restored)))
    raise SystemExit(0 if report['ok'] and all(restored) else 1)


if __name__=='__main__':
    from hardware_bench_lock import hardware_bench_lock
    with hardware_bench_lock():
        main()
