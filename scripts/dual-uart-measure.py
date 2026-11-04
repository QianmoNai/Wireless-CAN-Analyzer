#!/usr/bin/env python3
"""Opt-in PC/CH340 -> A -> wireless/B echo -> A -> CH340 measurement.

Default is a dry plan. --no-counters uses only CH340 and a short loopback gate.
64-byte CRC records carry run ID, sequence and a host monotonic timestamp.
KB here means 1024 bytes; wire load includes record headers and CRC.
"""
import argparse
import bisect
import json
import math
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import threading
import time
import zlib

MAGIC = b'DUART01\x00'
SIZE = 64
HEADER = struct.Struct('<8sQIQ')
PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'


def record(run, seq, stamp):
    # Deterministic binary payload includes 00/FF and detects byte corruption.
    body = HEADER.pack(MAGIC, run, seq, stamp) + bytes((0, 255)) + bytes((seq+i) & 255 for i in range(30))
    return body + struct.pack('<I', zlib.crc32(body))


class Receiver:
    def __init__(self, run):
        self.run = run
        self.buffer = bytearray()
        self.frames = []
        self.raw_bytes = self.unframed_bytes = self.crc_errors = 0

    def feed(self, data, arrival):
        self.raw_bytes += len(data)
        self.buffer.extend(data)
        while len(self.buffer) >= len(MAGIC):
            start = self.buffer.find(MAGIC)
            if start < 0:
                n = len(self.buffer)-len(MAGIC)+1
                self.unframed_bytes += n
                del self.buffer[:n]
                break
            if start:
                self.unframed_bytes += start
                del self.buffer[:start]
            if len(self.buffer) < SIZE:
                break
            f = bytes(self.buffer[:SIZE])
            _, run, seq, stamp = HEADER.unpack_from(f)
            if run != self.run or f != record(run, seq, stamp):
                self.crc_errors += 1
                self.unframed_bytes += 1
                del self.buffer[0]
                continue
            self.frames.append((seq, stamp, arrival))
            del self.buffer[:SIZE]

    def metrics(self, sent, start_ns, stop_ns):
        seen, delays, events = set(), [], []
        duplicates = reordered = unexpected = 0
        highest = -1
        for seq, stamp, arrival in self.frames:
            if seq >= len(sent) or stamp != sent[seq]:
                unexpected += 1
                continue
            if seq in seen:
                duplicates += 1
                continue
            if seq < highest:
                reordered += 1
            highest = max(highest, seq)
            seen.add(seq)
            delays.append((arrival-stamp)/1e6)
            events.append(arrival)
        ordered = sorted(delays)
        def percentile(p):
            return ordered[max(0, math.ceil(len(ordered)*p)-1)] if ordered else None
        unknown = self.unframed_bytes + len(self.buffer)
        missing = (len(sent)-len(seen))*SIZE
        # Unframed bytes may be partial records, corrupt bytes or noise. Do not
        # silently equate an unverifiable record to 64 physically lost bytes.
        loss_exact = missing if not unknown and not unexpected else None
        windows = []
        for second in range(1, max(1, int((stop_ns-start_ns)/1e9)-1)):
            a = start_ns+second*1_000_000_000
            b = a+1_000_000_000
            windows.append((bisect.bisect_left(events,b)-bisect.bisect_left(events,a))*SIZE)
        span = (stop_ns-start_ns)/1e9
        return dict(sent_records=len(sent), sent_bytes=len(sent)*SIZE,
                    received_raw_bytes=self.raw_bytes, valid_unique_records=len(seen),
                    valid_unique_bytes=len(seen)*SIZE, missing_records=len(sent)-len(seen),
                    missing_verified_record_bytes=missing, byte_loss_exact=loss_exact,
                    byte_loss_bounds=[max(0,missing-unknown),missing],
                    duplicate_records=duplicates, duplicate_bytes=duplicates*SIZE,
                    out_of_order_records=reordered, out_of_order_bytes=reordered*SIZE,
                    unexpected_records=unexpected, unframed_bytes=unknown,
                    rejected_record_candidates=self.crc_errors,
                    rtt_ms=dict(p50=percentile(.5),p99=percentile(.99),max=max(delays,default=None)),
                    offered_Bps=len(sent)*SIZE/span, unique_echo_Bps=len(seen)*SIZE/span,
                    steady_echo_Bps_mean=sum(windows)/len(windows) if windows else None,
                    steady_echo_Bps_min=min(windows,default=None),
                    tail_after_load_bytes=sum(SIZE for t in events if t>stop_ns))


def counters(reader, manual):
    if manual:
        result = {}
        print('Read UART overview 丢弃 on both screens; do not reboot/change mode.', flush=True)
        for name in ('A','B'):
            result[name] = dict(uart_dropped=int(input(name+' UART 丢弃 (bytes): ')), source='screen-manual')
        return result
    p = subprocess.run([sys.executable, str(reader)], capture_output=True, text=True, timeout=40)
    if p.returncode:
        raise RuntimeError('Counter reader failed: '+p.stderr[-2400:]+p.stdout[-2400:])
    result = json.loads(p.stdout)
    for name, role in (('A',2),('B',3)):
        c = result[name]
        if not c['online'] or c['role'] != role or not c['uart_selected']:
            raise RuntimeError(name+' is not online in the expected dual/UART role')
    return result


def delta(before, after):
    return {name:{k:(after[name][k]-before[name][k]) & 0xffffffff
                  for k in ('uart_dropped','uart_local_dropped','uart_link_dropped',
                            'dual_general_dropped','uart_rx','uart_tx','uart_errors')
                  if k in before[name] and k in after[name]}
            for name in ('A','B')}


def measure(port, rate, seconds, drain, stage):
    run = secrets.randbits(64)
    rx = Receiver(run)
    sent = []
    stop = threading.Event()
    errors = []
    def read_loop():
        try:
            while not stop.is_set():
                data = port.read(max(1,min(port.in_waiting,8192)))
                if data:
                    rx.feed(data,time.monotonic_ns())
        except Exception as e:
            errors.append(repr(e))
            stop.set()
    port.reset_input_buffer()  # after idle and BEFORE the measured baseline/load
    reader = threading.Thread(target=read_loop,name='dual-echo-reader')
    reader.start()
    began_ns = time.monotonic_ns()
    end = began_ns/1e9 + seconds
    send_error = None
    accepted_unknown = False
    try:
        while not stop.is_set() and time.monotonic() < end:
            # Max is an unpaced, bounded-backlog UART saturation run. Fixed
            # rates never catch up in an unbounded burst after a host stall.
            due = began_ns/1e9 + len(sent)*SIZE/rate if rate else 0
            if due > time.monotonic():
                time.sleep(min(.005,due-time.monotonic()))
                continue
            if port.out_waiting > SIZE*2:
                time.sleep(.001)
                continue
            if time.monotonic() >= end:
                break
            stamp = time.monotonic_ns()
            data = record(run,len(sent),stamp)
            try:
                n = port.write(data)
            except Exception:
                accepted_unknown = True
                raise
            if n != SIZE:
                accepted_unknown = True
                raise OSError('Short write; no replay; accepted bytes uncertain')
            sent.append(stamp)
            if rate and time.monotonic()-due > .25:
                raise RuntimeError('Host pacing fell behind >250 ms; stage invalid')
        ended_ns = time.monotonic_ns()
        # Keep the same single serial handle reading while the radio/TX queues
        # drain. No serial resets or settings requests are sent during load.
        deadline = time.monotonic()+drain
        while time.monotonic()<deadline and not stop.is_set():
            time.sleep(.02)
    except Exception as e:
        ended_ns = time.monotonic_ns()
        send_error = repr(e)
    finally:
        stop.set()
        reader.join(timeout=2)
        if reader.is_alive():
            raise RuntimeError('Reader did not stop; do not open this port elsewhere')
    result = rx.metrics(sent,began_ns,ended_ns)
    result.update(stage=stage, target_Bps=rate or 'UART saturation', duration_requested_s=seconds,
                  write_span_s=(ended_ns-began_ns)/1e9, drain_s=drain,
                  write_error=send_error, reader_errors=errors,
                  accepted_bytes_unknown=accepted_unknown,
                  stage_valid=not (send_error or errors or accepted_unknown))
    return result


def self_test():
    run, stamp = 123, 1_000_000_000
    sent = [stamp+i*1000 for i in range(5)]
    frames = [record(run,i,t) for i,t in enumerate(sent)]
    rx = Receiver(run)
    data = frames[0]+frames[2]+frames[2]+frames[1]+frames[4]
    for i in range(0,len(data),7): rx.feed(data[i:i+7],stamp+10_000_000)
    m=rx.metrics(sent,stamp,stamp+60_000_000_000)
    assert (m['missing_records'],m['duplicate_records'],m['out_of_order_records'])==(1,1,1)
    assert m['byte_loss_exact']==64 and m['rtt_ms']['p99']==10
    rx=Receiver(run)
    damaged=bytearray(frames[0]);damaged[40]^=1
    rx.feed(bytes(damaged)+frames[1][:20]+frames[2],stamp+10_000_000)
    m=rx.metrics(sent,stamp,stamp+60_000_000_000)
    assert m['valid_unique_records']==1 and m['unframed_bytes']==84 and m['byte_loss_exact'] is None
    assert record(run,0,stamp)[28:30]==b'\x00\xff'
    print('PASS: fragmented framing, binary payload, gap/duplicate/reorder, corruption, partial-record resync and RTT; no hardware opened')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',action='store_true',help='Explicitly enable four 60-second hardware loads')
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--port',default=PORT)
    ap.add_argument('--baud',type=int,default=115200)
    ap.add_argument('--seconds',type=float,default=60)
    ap.add_argument('--drain',type=float,default=5)
    ap.add_argument('--counter-reader',type=Path,default=Path.home()/'grokbot/dual-uart-counters.py')
    ap.add_argument('--manual-counters',action='store_true',help='Read both screen UART drop totals before/after each stage')
    ap.add_argument('--no-counters',action='store_true',help='CH340 only: ten-record gate, then PC end-to-end metrics; never invoke counter reader')
    ap.add_argument('--output',type=Path,default=Path.home()/'grokbot/dual-uart-measurements.json')
    a=ap.parse_args()
    if a.self_test:
        self_test();return
    if not a.run:
        print(json.dumps(dict(status='DRY PLAN: no serial opened',port=a.port,baud=a.baud,
                              rates_Bps=[1024,4096,8192,'UART saturation'],seconds=a.seconds,
                              counter_reader=None if a.no_counters else str(a.counter_reader),manual_counters=a.manual_counters,
                              no_counters=a.no_counters),indent=2));return
    if a.seconds<=0 or a.drain<3 or a.baud/10<8192:
        ap.error('Need seconds>0, drain>=3 s and baud >=81920 (8N1)')
    if a.output.exists(): ap.error('Output exists; choose a new output filename')
    if a.no_counters and a.manual_counters: ap.error('Choose no-counters or manual-counters')
    if not a.no_counters and not a.manual_counters and not a.counter_reader.is_file(): ap.error('Counter reader missing')
    import serial
    output=dict(scope='physical PC/CH340/A/ESP-NOW/B software echo/A/CH340/PC',baud=a.baud,
                KB_definition=1024,record_bytes=SIZE,latency_definition='host pre-write to complete valid echo record; includes host and UART queues',
                max_definition='bounded-backlog saturation at the configured UART baud, not a search across baud rates',stages=[])
    a.output.parent.mkdir(parents=True,exist_ok=True)
    # A single process owns one serial handle; reader thread shares that handle.
    class PreserveLinesSerial(serial.Serial):
        # No explicit modem-line ioctls, including pyserial's open defaults.
        def _update_dtr_state(self): pass
        def _update_rts_state(self): pass
        def _reconfigure_port(self, *args, **kwargs):
            super()._reconfigure_port(*args, **kwargs)
            import termios
            attrs=termios.tcgetattr(self.fd)
            attrs[2] &= ~termios.HUPCL
            termios.tcsetattr(self.fd,termios.TCSANOW,attrs)
    serial_class=PreserveLinesSerial if a.no_counters else serial.Serial
    with serial_class(port=None,baudrate=a.baud,timeout=.02,write_timeout=.5,exclusive=True) as port:
        if not a.no_counters: port.dtr=port.rts=False
        port.port=a.port;port.open()
        if a.no_counters:
            output['device_counters_available']=False
            output['device_role_baud_generation_verified']=False
            gate=measure(port,640,1,3,'ten-record-gate')
            output['gate']=gate
            a.output.write_text(json.dumps(output,indent=2)+'\n')
            print(json.dumps(dict(gate=gate),ensure_ascii=False),flush=True)
            if not gate['stage_valid'] or gate['sent_records']!=10 or gate['valid_unique_records']!=10:
                raise RuntimeError('需要用户检查对端在线/重新配对；门禁失败，停止负载')
        for name,rate in [('1KBps',1024),('4KBps',4096),('8KBps',8192),('max',0)]:
            time.sleep(3)
            before=None if a.no_counters else counters(a.counter_reader,a.manual_counters)
            if not a.no_counters and not a.manual_counters:
                for c in before.values():
                    if c['uart_config']>>1!=a.baud: raise RuntimeError('UART baud mismatch')
            r=measure(port,rate,a.seconds,a.drain,name)
            r['counters_before']=before
            output['stages'].append(r)
            # Preserve the measured data even if a post-load JTAG probe fails.
            a.output.write_text(json.dumps(output,indent=2)+'\n')
            if a.no_counters:
                r.update(counters_after=None,counter_delta=None)
                a.output.write_text(json.dumps(output,indent=2)+'\n')
                print(json.dumps(r,ensure_ascii=False),flush=True)
                if not r['stage_valid']: raise RuntimeError('Invalid stage; stopped before next load')
                continue
            # Counter reads occur only outside the load, after all drain time.
            try:
                after=counters(a.counter_reader,a.manual_counters)
            except Exception as e:
                r.update(stage_valid=False,counter_error=str(e))
                a.output.write_text(json.dumps(output,indent=2)+'\n')
                raise
            r.update(counters_before=before,counters_after=after,counter_delta=delta(before,after))
            if not a.manual_counters:
                for dev in ('A','B'):
                    if after[dev]['uart_rx']<before[dev]['uart_rx']:
                        r['stage_valid']=False;r['counter_reset_suspected']=True
                    if after[dev]['dual_generation']!=before[dev]['dual_generation']:
                        r['stage_valid']=False;r['session_changed']=True
            a.output.write_text(json.dumps(output,indent=2)+'\n')
            print(json.dumps(r,ensure_ascii=False),flush=True)
            if not r['stage_valid']: raise RuntimeError('Invalid stage; stopped before next load')


if __name__=='__main__':
    try: main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e: sys.exit(str(e))
