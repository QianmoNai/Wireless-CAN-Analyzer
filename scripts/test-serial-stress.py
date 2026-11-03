#!/usr/bin/env python3
"""Opt-in real CUSB/UART load test. No CAN TX and no physical disconnect.

The lossless group is deliberately paced (RX <=1200 B/s, one TX in flight).
The overload group defaults to 98% UART line rate; loss is measured, not concealed
or treated as a lossless pass. Neither path includes the desktop/UI pipeline.
Use --fixture-thread to decouple writes from USB polling and --rx-only to isolate
RX. --rx-fraction 0 measures unpaced fixture supply, not physical line rate.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import os
import secrets
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uart_stress_fixture import FixtureWriter, low_latency, restore_low_latency, rss_bytes

spec = importlib.util.spec_from_file_location('serial_acceptance', Path(__file__).with_name('test-serial-hardware.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)


def percentiles(values):
    ordered = sorted(values)
    return {name: (round(ordered[min(len(ordered)-1, int((len(ordered)-1)*q))]*1000, 3)
                   if ordered else None) for name, q in [('p50', .5), ('p95', .95), ('p99', .99)]}


def command(probe, data):
    token = probe.next_token()
    p = bytearray(base.packet(0x18, a=len(data), token=token))
    p[4:4+len(data)] = data
    return token, p


def poll(probe):
    packets = list(probe.pending)
    probe.pending.clear()
    packets.extend(probe.decoder.feed(probe.port.read(65536)))
    return packets


def quiet(probe, peer, seconds=.3):
    """Setup-only drain. Return all bytes/packets to make the boundary explicit."""
    data, packets = bytearray(), []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        data.extend(peer.read(65536))
        packets.extend((sid, p.hex()) for sid, p in poll(probe))
        time.sleep(.001)
    return dict(peer_hex=data.hex(), packets=packets)


def run_load(probe, peer, baud, group, duration, pattern='continuous', progress=None,
             fixture=None, rx_only=False):
    if fixture is not None:
        return _run_load(probe, peer, baud, group, duration, pattern, progress,
                         fixture=fixture, rx_only=rx_only)
    if pattern != 'burst':
        return _run_load(probe, peer, baud, group, duration, pattern, progress)
    # Large fixture writes can block while its USB adapter drains. Keep USB
    # reads/heartbeats on this thread, and join the sole writer before callers
    # restore settings or close either serial port, including on exceptions.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='uart-fixture') as writer:
        return _run_load(probe, peer, baud, group, duration, pattern, progress, writer)


def _run_load(probe, peer, baud, group, duration, pattern, progress, writer=None, fixture=None, rx_only=False):
    rng = random.Random(baud + (0 if group == 'lossless' else 1))
    expected_rx, received_rx, expected_tx, received_tx = (bytearray() for _ in range(4))
    rx_rate = min(1200., baud / 20) if group == 'lossless' else baud / 10 * .98
    sender = None
    cpu_start = time.process_time()
    rss_max = 0
    rss_due = 0
    rx_events = []
    inflight = None
    ack_latencies, ping_rtts, statuses, rejections = [], [], [], []
    peer_write_latencies = []
    pending_write = None
    fixture_failure = None
    max_poll_gap = 0
    last_poll = None
    gaps = duplicates = reordered = foreign = 0
    last_seq = None
    ping_started = None
    last_ping = 0
    rx_due = time.monotonic()
    tx_due = rx_due
    start = time.monotonic()
    end = start + duration
    rx_chunk = max(12, min(256, int(rx_rate / 100)))
    checkpoint_at = start
    # Capture type21 at both boundaries; no settings/session reset within case.
    before = probe.status(1, baud)
    before_rx, before_tx, before_drop = (base.u32(before, o) for o in (8, 12, 16))
    quiet(probe, peer, .05)
    start = time.monotonic()
    end = start + duration
    rx_due = tx_due = start
    if fixture is not None:
        rx_rate = baud/10*fixture['fraction']
        sender = FixtureWriter(peer, duration, rx_rate, fixture['chunk'], baud, pattern).start(start)
        expected_rx = sender.expected
    def write_burst(data):
        started = time.monotonic()
        try:
            n = peer.write(data)
        except BaseException as exc:
            return None, time.monotonic()-started, exc
        return n, time.monotonic()-started, None

    def settle_write():
        nonlocal pending_write, fixture_failure
        data, future = pending_write
        pending_write = None
        n, elapsed, error = future.result()
        peer_write_latencies.append(elapsed)
        if error is not None or not isinstance(n, int) or not 0 <= n <= len(data):
            # pyserial write exceptions do not expose an accepted byte count.
            # Preserve that uncertainty; never count the whole attempted burst
            # as input, retry it, or emit a successful strict comparison.
            fixture_failure = dict(attempted_bytes=len(data), accepted_bytes=None,
                                   accepted_bytes_unknown=True, error=repr(error))
            raise OSError('UART fixture write failed; accepted byte count unknown') from error
        expected_rx.extend(data[:n])
        if n != len(data):
            fixture_failure = dict(attempted_bytes=len(data), accepted_bytes=n,
                                   accepted_bytes_unknown=False, error='short write')
            raise OSError('Short UART fixture write; accepted %d/%d bytes' % (n, len(data)))

    def consume(now):
        nonlocal inflight, last_seq, gaps, duplicates, reordered, foreign, ping_started, max_poll_gap, last_poll
        if last_poll is not None:
            max_poll_gap = max(max_poll_gap, now-last_poll)
        last_poll = now
        received_tx.extend(peer.read(65536))
        for sid, p in poll(probe):
            if sid != probe.session:
                foreign += 1
                continue
            if p[1] == 19:
                if not 1 <= p[2] <= 12 or p[3]:
                    raise AssertionError('Malformed type19: ' + p.hex())
                seq = base.u32(p, 16)
                if last_seq is not None:
                    difference = (seq-last_seq) & 0xffffffff
                    if difference == 0:
                        duplicates += 1
                    elif difference >= 0x80000000:
                        reordered += 1
                    else:
                        gaps += difference-1
                last_seq = seq
                received_rx.extend(p[4:4+p[2]])
            elif p[1] == 20:
                if inflight is None or base.u32(p, 16) != inflight[0]:
                    raise AssertionError('Unmatched/duplicate type20: ' + p.hex())
                token, sent_at, data = inflight
                if p[2] or p[3] != len(data) or p[4:4+len(data)] != data:
                    raise AssertionError('Failed/mismatched type20: ' + p.hex())
                ack_latencies.append(now-sent_at)
                inflight = None
            elif p[1] == 18 and p[3] == 0x18 and p[2]:
                rejections.append(p.hex())
            elif p == base.PING and ping_started is not None:
                ping_rtts.append(now-ping_started)
                ping_started = None
            elif p[1] == 21:
                statuses.append(p.hex())
    try:
        while True:
            now = time.monotonic()
            consume(now)
            if now >= rss_due:
                rss_max = max(rss_max, rss_bytes() or 0)
                rss_due = now+.25
            if not rx_events or now-start-rx_events[-1][0] >= .1:
                rx_events.append((now-start, len(received_rx)))
            if sender is not None and sender.done.is_set() and sender.error:
                sender.join()
            if pending_write is not None and pending_write[1].done():
                settle_write()
            if progress and now >= checkpoint_at:
                progress(dict(baud=baud, group=group, pattern=pattern,
                              elapsed_s=round(now-start, 3), input_bytes=len(expected_rx),
                              output_bytes=len(received_rx), tx_input_bytes=len(expected_tx),
                              tx_output_bytes=len(received_tx), sequence_gaps=gaps,
                              duplicate_sequences=duplicates, reordered_sequences=reordered,
                              type20_count=len(ack_latencies), type20_ms=percentiles(ack_latencies),
                              ping_rtt_ms=percentiles(ping_rtts),
                              input_sha256=hashlib.sha256(bytes(expected_rx)).hexdigest(),
                              output_sha256=hashlib.sha256(received_rx).hexdigest(),
                              latest_type21=statuses[-1] if statuses else before.hex(),
                              peer_write_max_ms=round(max(peer_write_latencies, default=0)*1000, 3),
                              max_poll_gap_ms=round(max_poll_gap*1000, 3)))
                checkpoint_at = now+1
            sending = now < end
            if sender is None and sending and now >= rx_due and pending_write is None:
                if pattern == 'burst':
                    size = min(4096, max(128, int(rx_rate*.25)))
                    rx_due = now + size/rx_rate
                else:
                    size = rx_chunk
                    # Never catch up with an unbounded burst after a host pause.
                    rx_due = max(rx_due+size/rx_rate, now)
                data = rng.randbytes(size)
                if writer is not None:
                    pending_write = data, writer.submit(write_burst, data)
                else:
                    write_started = time.monotonic()
                    n = peer.write(data)
                    peer_write_latencies.append(time.monotonic()-write_started)
                    expected_rx.extend(data[:n])
                    if n != len(data):
                        raise OSError('Short UART fixture write; load outcome unknown')
            if not rx_only and sending and inflight is None and now >= tx_due:
                data = rng.randbytes(12)
                token, p = command(probe, data)
                probe.send(p)
                expected_tx.extend(data)
                inflight = token, time.monotonic(), data
                tx_due = now + (12/min(600., baud/20) if group == 'lossless' else 0)
            if ping_started is None and now-last_ping >= .8:
                probe.send(base.PING)
                ping_started = time.monotonic()
                probe.last_ping = last_ping = ping_started
            if inflight is not None and now-inflight[1] > 4:
                raise TimeoutError('type20 timeout under load')
            if not sending and now > end+1.5 and inflight is None and pending_write is None and (sender is None or sender.done.is_set()):
                break
            if now > end+5:
                raise TimeoutError('Load drain timeout')
            time.sleep(.0005)
    finally:
        primary_error = sys.exc_info()[0] is not None
        sender_error = None
        if sender is not None:
            sender.stop.set()
            try:
                sender.join()
            except OSError as exc:
                sender_error = exc
                fixture_failure = dict(accepted_bytes=len(sender.expected),
                                       accepted_bytes_unknown=sender.unknown_accepted_bytes,
                                       error=sender.error, fixture=sender.metrics())
            peer_write_latencies = sender.latencies
        try:
            if pending_write is not None:
                settle_write()  # Wait before the executor/ports are closed.
            if sender_error is not None and not primary_error:
                raise sender_error
        except BaseException:
            if not primary_error:
                raise
        finally:
            if progress and (writer is not None or sender is not None) and (primary_error or fixture_failure):
                progress(dict(baud=baud, group=group, pattern=pattern,
                              elapsed_s=round(time.monotonic()-start, 3),
                              input_bytes=len(expected_rx), output_bytes=len(received_rx),
                              tx_input_bytes=len(expected_tx), tx_output_bytes=len(received_tx),
                              sequence_gaps=gaps, duplicate_sequences=duplicates,
                              reordered_sequences=reordered, type20_count=len(ack_latencies),
                              type20_ms=percentiles(ack_latencies), ping_rtt_ms=percentiles(ping_rtts),
                              input_sha256=hashlib.sha256(bytes(expected_rx)).hexdigest(),
                              output_sha256=hashlib.sha256(received_rx).hexdigest(),
                              latest_type21=statuses[-1] if statuses else before.hex(),
                              peer_write_max_ms=round(max(peer_write_latencies, default=0)*1000, 3),
                              max_poll_gap_ms=round(max_poll_gap*1000, 3),
                              fixture_write_failure=fixture_failure,
                              pending_fixture_write=False))
    # Keep collecting ALL packets while waiting for a fresh final type21.
    # Probe.wait would discard raw packets encountered before its match.
    previous_status_count = len(statuses)
    deadline = time.monotonic()+2
    while len(statuses) == previous_status_count:
        consume(time.monotonic())
        if time.monotonic() > deadline:
            raise TimeoutError('Missing final type21')
        time.sleep(.0005)
    after = bytes.fromhex(statuses[-1])
    elapsed = time.monotonic()-start
    delta = lambda offset, value: (base.u32(after, offset)-value) & 0xffffffff
    rx_equal, tx_equal = expected_rx == received_rx, expected_tx == received_tx
    row = dict(baud=baud, group=group, pattern=pattern, duration_s=duration,
               elapsed_with_drain_s=round(elapsed, 3), offered_rx_Bps=round(rx_rate, 2),
               input_bytes=len(expected_rx), output_bytes=len(received_rx),
               effective_rx_Bps=round(len(received_rx)/duration, 2),
               tx_input_bytes=len(expected_tx), tx_output_bytes=len(received_tx),
               effective_tx_Bps=round(len(received_tx)/duration, 2),
               missing_rx_bytes=len(expected_rx)-len(received_rx), missing_tx_bytes=len(expected_tx)-len(received_tx),
               strict_rx_equal=rx_equal, strict_tx_equal=tx_equal,
               input_sha256=hashlib.sha256(bytes(expected_rx)).hexdigest(),
               output_sha256=hashlib.sha256(received_rx).hexdigest(),
               tx_input_sha256=hashlib.sha256(expected_tx).hexdigest(),
               tx_output_sha256=hashlib.sha256(received_tx).hexdigest(),
               sequence_gaps=gaps, duplicate_sequences=duplicates, reordered_sequences=reordered,
               foreign_session_frames=foreign, type20_ms=percentiles(ack_latencies),
               ping_rtt_ms=percentiles(ping_rtts), type20_count=len(ack_latencies), ping_count=len(ping_rtts),
               type21_rx_delta=delta(8, before_rx), type21_tx_delta=delta(12, before_tx),
               type21_drop_delta=delta(16, before_drop), type21_before=before.hex(), type21_after=after.hex(),
               command_rejections=rejections, peer_write_ms=percentiles(peer_write_latencies),
               max_poll_gap_ms=round(max_poll_gap*1000, 3))
    interior = [(t,n) for t,n in rx_events if 1 <= t <= duration-1]
    row.update(rx_only=rx_only, uid=os.getuid(),
               cpu_percent=round((time.process_time()-cpu_start)/elapsed*100,2),
               rss_max_bytes=rss_max or None, queue_age_ms=None,
               steady_rx_Bps=round((interior[-1][1]-interior[0][1])/(interior[-1][0]-interior[0][0]),2) if len(interior)>1 else None,
               rx_events=rx_events, fixture=sender.metrics() if sender is not None else None)
    row['unaccounted_rx_bytes'] = row['missing_rx_bytes']-row['type21_drop_delta']
    passed = tx_equal and not (duplicates or reordered or foreign or rejections)
    if group == 'lossless':
        passed = passed and rx_equal and not gaps and row['type21_drop_delta'] == 0
    row['status'] = 'PASS' if passed else 'FAIL'
    row['interpretation'] = 'lossless comparison' if group == 'lossless' else 'offered-load measurement; RX loss allowed and reported'
    return row


def recovery_cycle(probe, peer, mode, cycle):
    old_session = probe.session
    # Establish active RX traffic BEFORE terminating the session, then keep
    # traffic arriving during recovery. This is a software lifecycle test.
    warmup_bytes = 0
    warmup_until = time.monotonic()+.5
    while time.monotonic() < warmup_until:
        warmup_bytes += peer.write(b'pre-break-rx'*8)
        peer.read(65536)
        poll(probe)
        time.sleep(.025)
    # Keep real UART RX arriving while the old session is terminated/expired.
    token, p = command(probe, b'old-inflight')
    probe.send(p)
    until = time.monotonic()+(.8 if mode != 'heartbeat' else 4.6)
    if mode == 'bye':
        probe.send(base.BYE)
    elif mode == 'reopen':
        probe.port.close()
    probe.active = False
    sent = 0
    while time.monotonic() < until:
        sent += peer.write(b'old-rx-load\x00'*8)
        peer.read(65536)
        if mode != 'reopen':
            poll(probe)  # Explicitly stop heartbeat, still drain the host.
        time.sleep(.025)
    if mode == 'reopen':
        probe.port.open()
    boundary = quiet(probe, peer, .4)
    probe.pending.clear()
    probe.decoder = base.Decoder()
    probe.session = secrets.randbelow(0xffffffff)+1
    received_before_hello = len(probe.received)
    probe.hello()
    # wait() retains packets it consumes before the HELLO ACK; inspect those
    # too, otherwise an early leak could disappear from the recovery verdict.
    leaked = [p.hex() for p in probe.received[received_before_hello:]
              if p[1] == 19 or (p[1] == 20 and base.u32(p, 16) == token)]
    until = time.monotonic()+.3
    while time.monotonic() < until:
        leaked.extend(p.hex() for sid, p in poll(probe)
                      if sid == probe.session and (p[1] == 19 or (p[1] == 20 and base.u32(p, 16) == token)))
        time.sleep(.001)
    # Reject data and heartbeat bearing the actual previous session ID.
    stale_token, stale = command(probe, b'stale-TX-000')
    probe.send(stale, old_session)
    probe.send(base.PING, old_session)
    stale_bytes = bytearray()
    stale_replies = []
    until = time.monotonic()+.3
    while time.monotonic() < until:
        stale_bytes.extend(peer.read(4096))
        stale_replies.extend(p.hex() for sid, p in poll(probe) if p == base.PING or
                             (p[1] in (18, 20) and (base.u32(p, 16) == stale_token or base.u32(p, 4) == stale_token)))
        time.sleep(.001)
    sync_noise = probe.uart_sync(peer)
    payload = ('new-%s-%d🙂' % (mode, cycle)).encode()
    peer.write(payload)
    probe.uart_rx(payload)
    probe.uart_tx(payload, peer)
    tail = quiet(probe, peer, .1)
    duplicate_packets = [p for sid, p in tail['packets']
                         if sid == probe.session and bytes.fromhex(p)[1] in (19, 20)]
    duplicates = len(bytes.fromhex(tail['peer_hex']))
    assert not (leaked or stale_bytes or stale_replies or duplicates or duplicate_packets), (leaked, stale_bytes.hex(), stale_replies, tail)
    return dict(mode=mode, cycle=cycle, old_session=old_session, new_session=probe.session,
                load_input_bytes=sent, warmup_input_bytes=warmup_bytes, old_session_leaks=len(leaked), duplicate_output_bytes=duplicates,
                stale_command_output_bytes=len(stale_bytes), duplicate_packets=duplicate_packets, stale_replies=stale_replies,
                setup_drain=boundary, sync_noise=sync_noise,
                reset_banner_observed=b'ESP-ROM:' in bytes.fromhex(boundary['peer_hex']), status='PASS')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usb', default='/dev/ttyACM0')
    ap.add_argument('--uart', default='/dev/ttyACM1')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--bauds', nargs='+', type=int, choices=BAUDS, default=[115200, 230400, 460800, 921600])
    ap.add_argument('--duration', type=float, default=60)
    ap.add_argument('--burst-duration', type=float, default=5)
    ap.add_argument('--groups', nargs='+', choices=['lossless', 'overload'], default=['lossless', 'overload'])
    ap.add_argument('--patterns', nargs='+', choices=['continuous', 'burst'], default=['continuous', 'burst'])
    ap.add_argument('--recovery-cycles', type=int, default=2)
    ap.add_argument('--recovery-only', action='store_true', help='Run lifecycle load cycles without baud sweeps')
    ap.add_argument('--fixture-thread', action='store_true', help='Dedicated writer, independent of USB polling')
    ap.add_argument('--fixture-chunk', type=int, default=4096)
    ap.add_argument('--rx-fraction', type=float, default=.98, help='8N1 line fraction; 0 = unpaced (thread only)')
    ap.add_argument('--fixture-low-latency', action='store_true')
    ap.add_argument('--rx-only', action='store_true', help='No reverse UART TX load')
    args = ap.parse_args()
    if args.fixture_chunk <= 0 or not 0 <= args.rx_fraction <= 1:
        ap.error('chunk must be positive; RX fraction must be in [0,1]')
    if (args.rx_only or args.rx_fraction != .98) and not args.fixture_thread:
        ap.error('RX-only and custom fractions require --fixture-thread')
    if args.duration <= 0 or args.burst_duration <= 0 or args.recovery_cycles < 0:
        ap.error('durations must be positive; recovery cycles must be nonnegative')
    report = dict(started=time.strftime('%Y-%m-%d %H:%M:%S'), tests=[], usb=args.usb, uart=args.uart,
                  bauds=args.bauds, duration_s=args.duration,
                  scope='physical UART/native USB only; desktop/UI bypassed; no CAN TX; software close/reopen')
    probe = peer = None
    def record(name, status='PASS', **detail):
        row = dict(name=name, status=status, **detail)
        report['tests'].append(row)
        base.save_report(report, args.output)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    try:
        probe = base.Probe(args.usb)
        ready_deadline = time.monotonic()+8
        while True:
            try:
                initial = probe.settings(expected=0)
                break
            except BlockingIOError:
                if time.monotonic() >= ready_deadline:
                    raise
                time.sleep(.2)
        report['initial_settings'] = dict(network=initial[3], data=initial[4], baud=base.u32(initial, 8))
        base.save_report(report, args.output)
        if initial[3] == 4:
            probe.hello()
        probe.settings(7, network=4, data=1, baud=115200, expected=1)
        probe.settled(4, 1, 115200)
        probe.hello()
        peer = base.serial.Serial(args.uart, 115200, timeout=0, write_timeout=2, exclusive=True)
        report['fixture_low_latency'] = low_latency(peer, args.fixture_low_latency)
        for baud in ([] if args.recovery_only else args.bauds):
            probe.settings(6, data=1, baud=baud, expected=1)
            probe.settled(4, 1, baud)
            peer.baudrate = baud
            probe.status(1, baud)
            noise = base.drain_setup_bytes(peer) + probe.uart_sync(peer)
            record('setup', baud=baud, pre_boundary_hex=noise)
            for group in args.groups:
                for pattern in args.patterns:
                    duration = args.duration if pattern == 'continuous' else args.burst_duration
                    def checkpoint(partial):
                        report['current_load'] = partial
                        base.save_report(report, args.output)
                    try:
                        row = run_load(probe, peer, baud, group, duration, pattern, checkpoint,
                                       fixture=dict(fraction=args.rx_fraction, chunk=args.fixture_chunk) if args.fixture_thread else None,
                                       rx_only=args.rx_only)
                    except Exception as exc:
                        # Preserve the partial case; never replay unknown TX.
                        # Subsequent cases get a fresh session and setup boundary.
                        partial = report.pop('current_load', {})
                        same_session_ping = False
                        probe.send(base.PING)
                        try:
                            probe.wait(lambda p: p == base.PING, .3, False)
                            same_session_ping = True
                        except TimeoutError:
                            pass
                        record('load_failure', 'FAIL', baud=baud, group=group, pattern=pattern,
                               duration_s=duration, error=repr(exc), partial=partial,
                               same_session_ping_alive=same_session_ping)
                        probe.send(base.BYE)
                        probe.active = False
                        quiet(probe, peer, .3)
                        probe.session = secrets.randbelow(0xffffffff)+1
                        probe.hello()
                        probe.status(1, baud)
                        record('recover_after_load_failure', baud=baud,
                               setup_noise=probe.uart_sync(peer), replayed_commands=0)
                        continue
                    report.pop('current_load', None)
                    record('load', **row)
        probe.settings(6, data=1, baud=115200, expected=1)
        probe.settled(4, 1, 115200)
        peer.baudrate = 115200
        probe.status(1, 115200)
        for cycle in range(args.recovery_cycles):
            for mode in ('bye', 'heartbeat', 'reopen'):
                record('recovery', **recovery_cycle(probe, peer, mode, cycle))
    except BaseException as exc:
        record('exception', 'FAIL', error=repr(exc))
        raise
    finally:
        primary_error = sys.exc_info()[0] is not None
        if peer:
            try:
                try:restore_low_latency(peer, report.get('fixture_low_latency', {}))
                finally:peer.close()
            except BaseException as exc:
                record('peer_close', 'FAIL', error=repr(exc))
        if probe:
            try:
                if not probe.port.is_open:
                    probe.port.open()
                probe.hello()
                probe.settings(7, network=4, data=1, baud=115200, expected=1)
                probe.settled(4, 1, 115200)
                record('restore_USB_UART_115200')
            except BaseException as exc:
                record('restore_USB_UART_115200', 'FAIL', error=repr(exc))
        errors = base.finish_report(report, args.output, probe, record)
        if not primary_error and (errors or any(r['status'] == 'FAIL' for r in report['tests'])):
            raise RuntimeError('Stress/cleanup failed; see saved report')


if __name__ == '__main__':
    main()
