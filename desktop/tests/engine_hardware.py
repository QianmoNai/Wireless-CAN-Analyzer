"""Opt-in UART load through Channel + UsbTransport + JSON delivery, no CAN TX.

This uses a delayed UI receipt, not Qt or a renderer. Run serial_hardware.py
separately for real GUI acceptance. Overload loss is reported, never hidden.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import subprocess
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.engine import Channel
from can_scope.protocol import packet, u32
from can_scope.usb_transport import prepare_usb, open_port


def percentile(values):
    values = sorted(values)
    return {k: round(values[min(len(values)-1, int((len(values)-1)*q))], 3) if values else None
            for k, q in [('p50', .5), ('p95', .95), ('p99', .99)]}


def rss():
    return int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                    if line.startswith('VmRSS:')))*1024


async def run(args, report):
    loop = asyncio.get_running_loop()
    active = None
    errors = []
    timers = set()
    def consume_packet(p, age=0):
        if active is None:
            return
        if p[1] == 19:
            active['received'].extend(p[4:4+p[2]])
            active['ages'].append(age)
            seq = u32(p,16)
            previous = active['last_seq']
            if previous is not None:
                distance = (seq-previous)&0xffffffff
                if distance == 0: active['duplicates'] += 1
                elif distance >= 0x80000000: active['reordered'] += 1
                else: active['gaps'] += distance-1
            active['last_seq'] = seq
        elif p[1] == 21:
            active['statuses'].append(p)
    def consume_run(row,delay):
        if active is None:return
        active['received'].extend(bytes.fromhex(row['uart']))
        # The run's oldest age is conservative for all packets in this run.
        active['ages'].extend([row['age']+delay]*row['count'])
        previous=active['last_seq']
        if previous is not None:
            distance=(row['first']-previous)&0xffffffff
            if distance==0:active['duplicates']+=1
            elif distance>=0x80000000:active['reordered']+=1
            else:active['gaps']+=distance-1
        active['last_seq']=row['last']
    def emit(kind, payload):
        # Real serialization/deserialization boundary, same receipt gate as app.
        payload = json.loads(json.dumps(payload))
        if kind == 'error': errors.append(payload)
        if kind not in ('packets', 'motorSamples'): return
        delivery_started = time.monotonic()
        def deliver():
            timers.discard(handle)
            delay = (time.monotonic()-delivery_started)*1000
            for item in payload.get('packets', [])+payload.get('items', []):
                if 'uart' in item:
                    consume_run(item,delay)
                else:consume_packet(bytes.fromhex(item['hex']),item.get('age',0)+delay)
            channel.submit('delivered', [payload['delivery']] if 'delivery' in payload else [])
        handle=loop.call_later(args.ui_delay_ms/1000,deliver)
        timers.add(handle)
    class MeasuredChannel(Channel):
        def receive(self,p):
            super().receive(p)
            if active is not None:
                active['high_water']=max(active['high_water'],len(self.packets))
                active['rss_max']=max(active['rss_max'],rss()) if time.monotonic()-active['rss_at']>.25 else active['rss_max']
                if time.monotonic()-active['rss_at']>.25:active['rss_at']=time.monotonic()
    channel=MeasuredChannel(emit)
    peer=None
    try:
        report['initial_settings']=await prepare_usb(args.usb,data=1,baud=115200)
        peer=open_port(args.peer)
        channel.start()
        await channel.handle('connect',['usb:'+args.usb])
        for baud in args.bauds:
            await channel.handle('deviceSettings',[dict(data=1,baud=baud)])
            peer.baudrate=baud
            await asyncio.sleep(.5)
            peer.reset_input_buffer()
            for pattern,duration in [('continuous',args.duration),('burst',args.burst_duration)]:
                await asyncio.sleep(.3)
                active=dict(received=bytearray(),ages=[],last_seq=None,gaps=0,duplicates=0,reordered=0,
                            statuses=[],high_water=0,rss_max=rss(),rss_at=time.monotonic())
                before_drop=channel.dropped
                expected,tx_expected,tx_actual=bytearray(),bytearray(),bytearray()
                rng=random.Random(baud)
                rate=baud/10*.98
                start=time.monotonic(); cpu=time.process_time(); due=start
                pending=None
                async def tx():
                    while time.monotonic()<start+duration:
                        data=rng.randbytes(12)
                        p=bytearray(packet(0x18,a=12,token=channel.next_token()));p[4:16]=data
                        tx_expected.extend(data)
                        ack=await channel.acknowledge(p,20,channel.token)
                        if ack[2] or ack[3]!=12 or ack[4:16]!=data:raise AssertionError('type20 mismatch')
                tx_task=asyncio.create_task(tx())
                try:
                    while time.monotonic()<start+duration:
                        now=time.monotonic()
                        tx_actual.extend(peer.read(65536))
                        if pending is not None and pending.done():
                            data,n=pending.result()
                            expected.extend(data[:n])
                            if n!=len(data):raise OSError('fixture short write; no retry')
                            pending=None
                        if now>=due and pending is None:
                            size=min(4096,max(128,int(rate*.25))) if pattern=='burst' else max(12,min(256,int(rate/100)))
                            data=rng.randbytes(size)
                            async def write(data):return data,await asyncio.to_thread(peer.write,data)
                            pending=asyncio.create_task(write(data))
                            due=max(due+size/rate,now)
                        await asyncio.sleep(.001)
                finally:
                    if pending is not None:
                        data,n=await pending;expected.extend(data[:n])
                        if n!=len(data):raise OSError('fixture short write; no retry')
                    await tx_task
                # Drain through the SAME engine and delivery gate, not raw USB.
                await asyncio.sleep(2)
                tx_actual.extend(peer.read(65536))
                elapsed=time.monotonic()-start
                row=dict(baud=baud,pattern=pattern,group='overload',duration_s=duration,
                         input_bytes=len(expected),output_bytes=len(active['received']),
                         offered_actual_Bps=round(len(expected)/duration,2),
                         effective_rx_Bps=round(len(active['received'])/duration,2),
                         missing_rx_bytes=len(expected)-len(active['received']),
                         strict_rx_equal=expected==active['received'],strict_tx_equal=tx_expected==tx_actual,
                         tx_bytes=len(tx_actual),effective_tx_Bps=round(len(tx_actual)/duration,2),
                         input_sha256=hashlib.sha256(expected).hexdigest(),
                         output_sha256=hashlib.sha256(active['received']).hexdigest(),
                         sequence_gaps=active['gaps'],duplicates=active['duplicates'],reordered=active['reordered'],
                         queue_age_ms=percentile(active['ages']),queue_high_water_packets=active['high_water'],
                         engine_drops=channel.dropped-before_drop,remaining_queue=len(channel.packets),
                         cpu_percent=round((time.process_time()-cpu)/elapsed*100,2),rss_max_bytes=active['rss_max'])
                row['ok']=row['strict_tx_equal'] and not (row['duplicates'] or row['reordered'] or errors or row['remaining_queue'])
                report['loads'].append(row)
                args.output.write_text(json.dumps(report,indent=2)+'\n')
                print(json.dumps(row),flush=True)
                active=None
        for cycle in range(args.recovery_cycles):
            for mode in ('bye','heartbeat','reopen'):
                old=channel.transport.session
                if mode=='heartbeat':
                    channel.transport.tasks[-1].cancel()
                    await asyncio.sleep(4.5)
                await channel.handle('disconnect',[])
                await channel.handle('connect',['usb:'+args.usb])
                await asyncio.sleep(.3)
                setup_noise=peer.read(65536)
                data=b'engine-recovery-'+bytes([cycle])+mode.encode()
                actual=bytearray()
                # Physical output once, and quiet tail detects duplicate replay.
                for offset in range(0,len(data),12):
                    part=data[offset:offset+12]
                    p=bytearray(packet(0x18,a=len(part),token=channel.next_token()));p[4:4+len(part)]=part
                    ack=await channel.acknowledge(p,20,channel.token)
                    if ack[2] or ack[3]!=len(part) or ack[4:4+len(part)]!=part:raise AssertionError('recovery ACK')
                await asyncio.sleep(.2)
                actual.extend(peer.read(65536))
                row=dict(cycle=cycle,mode=mode,old_session=old,new_session=channel.transport.session,
                         setup_noise_hex=setup_noise.hex(),strict_tx_equal=actual==data,ok=actual==data and old!=channel.transport.session)
                report['recoveries'].append(row)
                print(json.dumps(row),flush=True)
    finally:
        active=None
        await channel.close()
        for handle in timers:handle.cancel()
        if peer:peer.close()
        report['restored_settings']=await prepare_usb(args.usb,data=1,baud=115200)
        report['errors']=errors


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usb',default='/dev/ttyACM0');ap.add_argument('--peer',default='/dev/ttyACM1')
    ap.add_argument('--bauds',type=int,nargs='+',choices=(115200,460800,921600),default=[115200,460800,921600])
    ap.add_argument('--duration',type=float,default=20);ap.add_argument('--burst-duration',type=float,default=5)
    ap.add_argument('--recovery-cycles',type=int,default=2);ap.add_argument('--ui-delay-ms',type=float,default=25)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.duration<=0 or args.burst_duration<=0 or args.recovery_cycles<0 or args.ui_delay_ms<0:
        ap.error('durations must be positive; cycles and UI delay must be nonnegative')
    report=dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),scope='real Channel/USB/UART with JSON + delayed UI receipt; no renderer, no CAN TX',
                uid=os.getuid(),ui_delay_ms=args.ui_delay_ms,loads=[],recoveries=[])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    try:
        asyncio.run(run(args,report))
        report['ok']=all(r['ok'] for r in report['loads']+report['recoveries']) and not report['errors']
    except BaseException as exc:
        report.update(ok=False,error=repr(exc))
        raise
    finally:args.output.write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
