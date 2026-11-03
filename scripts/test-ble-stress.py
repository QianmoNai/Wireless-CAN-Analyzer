#!/usr/bin/env python3
"""Opt-in physical BLE/UART stress; all exit paths restore USB/UART/115200.

Use desktop/.venv/bin/python. Supply and BLE reception run independently.
Does not exercise Channel/Qt or prove CAN/PHY/air retransmission performance.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import time
import traceback

from ble_stress import Owner, ChannelReceiver, digest, percentiles, split_prefix
from uart_stress_fixture import FixtureWriter, rss_bytes
from can_scope.protocol import u32
from can_scope.transports import scan_ble
import serial

ROOT = Path(__file__).resolve().parents[1]


def save(report, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


async def usb(args, report, *settings):
    # Native USB is opened only after previous owners/fixture/BLE are closed,
    # except the explicit read-only bootstrap check before opening the fixture.
    command = [sys.executable, str(ROOT/'scripts/serial-settings.py'), '--port', args.usb, *settings]
    process = await asyncio.create_subprocess_exec(*command, stdout=-1, stderr=-1)
    try:
        out, err = await asyncio.wait_for(process.communicate(), 18)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    row = dict(args=list(settings), rc=process.returncode, out=out.decode().strip(), err=err.decode().strip())
    report['settings_log'].append(row)
    if process.returncode:
        raise RuntimeError('USB settings failed; do not replay SET: ' + str(row))
    return json.loads(out)


async def restore(args, report):
    report['pre_restore_query'] = await usb(args, report)
    try:
        report['restore_set'] = await usb(args, report, '--mode', 'usb', '--data', 'uart', '--baud', '115200')
    except Exception as exc:
        report['restore_set_error'] = repr(exc)
        # An unknown SET is followed by QUERY, never another SET.
    await asyncio.sleep(.5)
    report['final_query'] = await usb(args, report)
    report['restored'] = all(report['final_query'][k] == v for k, v in
                           [('network_mode', 4), ('data_mode', 1), ('baudrate', 115200)])
    if not report['restored']:
        raise RuntimeError('Independent final settings readback did not match')


async def cleanup(args, report, owner, peer):
    errors = []
    for name, action in [('peer', lambda: close_peer(peer)), ('ble', owner.close),
                         ('restore', lambda: restore(args, report))]:
        try:
            await action()
        except BaseException as exc:
            errors.append(dict(step=name, error=repr(exc)))
    return errors


async def sync(owner, peer, rng):
    marker = b'BS' + rng.randbytes(10)
    await owner.send(marker)
    stream = bytearray()
    deadline = time.monotonic() + 3
    while marker not in stream and time.monotonic() < deadline:
        stream.extend(peer.read(4096))
        await asyncio.sleep(.002)
    prefix = split_prefix(stream, marker)
    received = bytearray()
    owner.sink = lambda p: received.extend(p[4:4+p[2]]) if p[1] == 19 else None
    try:
        if peer.write(marker) != len(marker):
            raise OSError('Short sync write; not replayed')
        deadline = time.monotonic() + 3
        while len(received) < len(marker) and time.monotonic() < deadline:
            await asyncio.sleep(.002)
        if received != marker:
            raise AssertionError('UART to BLE sync failed: ' + received.hex())
    finally:
        owner.sink = None
    return dict(peer_prefix_hex=prefix.hex(), rom_prefix=prefix.startswith(b'ESP-ROM:'), marker_hex=marker.hex())


async def run_case(args, report, owner, peer, baud, rate, duplex, number):
    row = dict(baud=baud, target_Bps=rate, duplex=duplex, duration_s=args.duration,
               duplicates=0, out_of_order=0, sequence_missing=0, packets=0, errors=[])
    report['cases'].append(row)
    writer = None
    acks, queries = [], []
    channel = ChannelReceiver() if args.channel_rx else None
    channel_task = None
    received, peer_rx = bytearray(), bytearray()
    tx_start = len(owner.tx)
    rng = random.Random(20261103 + number)
    last_sequence = None
    rss_max = rss_bytes() or 0
    cpu_start = time.process_time()
    def receive(p):
        nonlocal last_sequence
        if channel:
            channel.receive(p)
        if p[1] != 19:
            return
        if not 1 <= p[2] <= 12 or p[3]:
            raise ValueError('Invalid UART RX event')
        seq = u32(p, 16)
        if last_sequence is not None:
            delta = (seq-last_sequence) & 0xffffffff
            if delta == 0:
                row['duplicates'] += 1
            elif delta < 0x80000000:
                row['sequence_missing'] += delta-1
            else:
                row['out_of_order'] += 1
        last_sequence = seq
        received.extend(p[4:4+p[2]])
        row['packets'] += 1
    try:
        await owner.query(baud=baud)
        peer.baudrate = baud
        await owner.status(baud)
        row['sync'] = await sync(owner, peer, rng)
        row['before'] = await owner.status(baud)
        row['diagnostics_before'] = await owner.snapshot()
        if args.require_diagnostics and row['diagnostics_before'] is None:
            raise RuntimeError('Diagnostic characteristic missing; stop and refresh only this device GATT cache')
        owner.sink = receive
        if channel:
            channel_task = asyncio.create_task(channel.flush())
        started = time.monotonic()
        # Fixed 120B bursts at paced loads, same supply shape across candidates.
        writer = FixtureWriter(peer, args.duration, rate, chunk=120 if rate else 4096,
                               seed=baud + int(rate)).start(started)
        tx_due, query_due = started, started+.1
        while time.monotonic() < started + args.duration or not writer.done.is_set():
            peer_rx.extend(peer.read(4096))
            now = time.monotonic()
            if duplex and now >= tx_due and now < started + args.duration:
                acks.append(await owner.send(rng.randbytes(12)))
                tx_due = time.monotonic() + .2
            if now >= query_due and now < started + args.duration:
                _, ms = await owner.query()
                queries.append(ms)
                query_due = time.monotonic() + .5
            if writer.error:
                raise OSError(writer.error)
            rss_max = max(rss_max, rss_bytes() or 0)
            await asyncio.sleep(.002)
        writer.join()
        # Keep collecting tail, then use a new type21 snapshot, not cached state.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            peer_rx.extend(peer.read(4096))
            await asyncio.sleep(.002)
        row['after'] = await owner.status(baud)
        row['diagnostics_after'] = await owner.snapshot()
        row.update(type20_ms=percentiles(acks), settings_rtt_ms=percentiles(queries),
                   elapsed_with_drain_s=time.monotonic()-started)
    except BaseException as exc:
        row['errors'].append(repr(exc))
        raise
    finally:
        # Never close/reconfigure a port with a fixture write still in progress.
        if writer:
            writer.stop.set()
            while not writer.done.is_set():
                await asyncio.sleep(.01)
            try:
                writer.join()
            except Exception as exc:
                row['errors'].append(repr(exc))
            row['fixture'] = writer.metrics()
            expected = bytes(writer.expected)
        else:
            expected = b''
        if row['errors'] and writer:
            # No new GATT commands after failure. Receive the finite tail and
            # retain an independently arriving status for the unknown-TX ledger.
            status_serial = owner.status_serial
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                peer_rx.extend(peer.read(4096))
                await asyncio.sleep(.002)
            if owner.status_serial > status_serial and owner.latest:
                row['after'] = dict(owner.latest)
                row['diagnostics_after'] = None
        owner.sink = None
        if channel_task:
            channel_task.cancel()
            await asyncio.gather(channel_task, return_exceptions=True)
            row['channel_rx'] = channel.finish()
            row['channel_rx']['equal_transport'] = channel.data == received
        peer_rx.extend(peer.read(4096))
        tx = owner.tx[tx_start:]
        # Sync TX precedes tx_start measurement; retain it separately in owner log.
        if row.get('sync') and tx:
            tx = tx[1:]
        submitted = b''.join(bytes.fromhex(t['hex']) for t in tx)
        confirmed = b''.join(bytes.fromhex(t['hex']) for t in tx if t['outcome'] == 'confirmed')
        row.update(type20_ms=percentiles(acks), settings_rtt_ms=percentiles(queries))
        row.update(input_bytes=len(expected), rx_bytes=len(received), missing_bytes=len(expected)-len(received),
                   input_Bps=len(expected)/args.duration, rx_Bps=len(received)/args.duration,
                   rx_equal=expected == received, input_sha256=digest(expected), rx_sha256=digest(received),
                   tx=tx, tx_submitted_bytes=len(submitted), tx_confirmed_bytes=len(confirmed),
                   tx_unknown_bytes=sum(len(bytes.fromhex(t['hex'])) for t in tx if t['outcome'] == 'unknown'),
                   peer_rx_bytes=len(peer_rx), all_submitted_equal_peer=submitted == peer_rx,
                   tx_confirmed_equal_peer=confirmed == peer_rx, peer_rx_hex=peer_rx.hex(),
                   tx_submitted_hex=submitted.hex(), tx_submitted_sha256=digest(submitted),
                   tx_confirmed_sha256=digest(confirmed), peer_sha256=digest(peer_rx),
                   cpu_s=time.process_time()-cpu_start, rss_max=rss_max)
        if 'after' in row:
            for key in ('rx', 'tx', 'drop'):
                row['delta_'+key] = (row['after'][key]-row['before'][key]) & 0xffffffff
            row['input_accounting_difference'] = len(expected)-row['delta_rx']
            row['drop_accounting_difference'] = row['missing_bytes']-row['delta_drop']
            d0, d1 = row['diagnostics_before'], row['diagnostics_after']
            if d0 and d1:
                if 'uart' in d0 and 'uart' in d1:
                    row['uart_delta'] = {k: (d1['uart'][k]-v) & 0xffffffff
                                         for k, v in d0['uart'].items() if k != 'event_high_water'}
                causes = ('full_bytes', 'drop_alloc_bytes', 'drop_reserve_bytes', 'drop_submit_bytes', 'reset_bytes', 'other_bytes')
                row['drop_causes'] = {k: (d1['raw'][k]-d0['raw'][k]) & 0xffffffff for k in causes}
                row['cause_accounting_difference'] = row['delta_drop'] - sum(row['drop_causes'].values())
        artifact_dir = args.output.with_suffix('.streams')
        artifact_dir.mkdir(exist_ok=True)
        for name, data in [('input', expected), ('rx', received), ('peer', peer_rx), ('tx', submitted)]:
            file = artifact_dir / f'{number:03d}-{name}.bin'
            file.write_bytes(data)
        save(report, args.output)
        print(json.dumps({k: row.get(k) for k in ('baud', 'target_Bps', 'duplex', 'input_bytes', 'rx_bytes', 'delta_drop', 'drop_causes', 'type20_ms', 'settings_rtt_ms', 'errors')}), flush=True)
    if any(row[k] for k in ('duplicates', 'out_of_order', 'input_accounting_difference', 'drop_accounting_difference')):
        raise AssertionError('RX ledger/sequence mismatch')
    if row.get('cause_accounting_difference', 0) or not row['all_submitted_equal_peer'] or row['tx_unknown_bytes']:
        raise AssertionError('Reason ledger or physical TX mismatch')
    if channel and (row['channel_rx']['dropped'] or not row['channel_rx']['equal_transport']):
        raise AssertionError('Channel RX delivery mismatch')


async def main(args):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    report = dict(started=time.strftime('%Y-%m-%d %H:%M:%S %z'), uid=os.getuid(),
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  worktree_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)),
                  settings_log=[], cases=[], reconnect=[], note='BLE transport only; HELLO/PING N/A; no CAN TX, pairing, or data replay')
    owner = Owner()
    peer = None
    try:
        report['initial'] = await usb(args, report)
        if report['initial']['network_mode'] != 4:
            raise ValueError('Start in USB mode; first restore explicitly')
        await usb(args, report, '--mode', 'ble', '--data', 'uart', '--baud', '115200')
        await asyncio.sleep(.5)
        report['usb_query_while_ble'] = await usb(args, report)
        if report['usb_query_while_ble']['network_mode'] != 0:
            raise AssertionError('Bootstrap USB query while BLE failed')
        devices = await scan_ble(lambda _: None)
        targets = [d for addr, d in devices.items() if not args.address or addr.upper() == args.address.upper()]
        if len(targets) != 1:
            raise ValueError('Require exactly one matching analyzer: ' + str(list(devices)))
        device = targets[0]
        report['address'] = device.address
        await owner.connect(device)
        report['connection_diagnostics'] = await owner.snapshot()
        await owner.status(115200)
        peer = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=2, exclusive=True)
        peer.dtr = peer.rts = False
        peer.port = args.uart
        peer.open()
        number = 0
        for baud in args.bauds:
            for rate in args.rates:
                for duplex in args.directions:
                    number += 1
                    await run_case(args, report, owner, peer, baud, rate, duplex == 'duplex', number)
        peer.baudrate = 115200
        peer.close()
        peer = None
        for cycle in range(2):
            started = time.monotonic()
            await owner.close()
            await asyncio.sleep(.3)
            await owner.connect(device)
            r, ms = await owner.query()
            report['reconnect'].append(dict(cycle=cycle+1, elapsed_ms=(time.monotonic()-started)*1000,
                                             query_ms=ms, response=r.hex(), diagnostics=await owner.snapshot()))
        if owner.lost:
            raise AssertionError('Unexpected disconnects: ' + str(owner.lost))
    except BaseException as exc:
        report.update(error=repr(exc), traceback=traceback.format_exc())
    finally:
        # run_case has already stopped and joined the supply thread.
        cleanup_errors = await cleanup(args, report, owner, peer)
        report.update(cleanup_errors=cleanup_errors, tx_log=owner.tx, unexpected_disconnects=owner.lost,
                      stale_callbacks=owner.stale_callbacks, notification_types=owner.types,
                      finished=time.strftime('%Y-%m-%d %H:%M:%S %z'))
        save(report, args.output)
        print('RESTORED', report.get('restored'), 'ERROR', report.get('error'), 'CLEANUP', cleanup_errors, flush=True)
    return int(bool(report.get('error') or cleanup_errors))


async def close_peer(peer):
    if peer:
        try:
            peer.baudrate = 115200
        finally:
            peer.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usb', default='/dev/ttyACM0')
    ap.add_argument('--uart', default='/dev/ttyACM1')
    ap.add_argument('--address')
    ap.add_argument('--channel-rx', action='store_true', help='Also measure real Channel receive/flush with immediate harness receipts; no Qt')
    ap.add_argument('--require-diagnostics', action='store_true', help='Fail before supply if test characteristic is absent')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--duration', type=float, default=10)
    ap.add_argument('--bauds', type=int, nargs='+', choices=(115200, 921600), default=[115200, 921600])
    ap.add_argument('--rates', type=float, nargs='+', default=[1000, 2000, 4000, 8000, 0], help='B/s; zero = maximum fixture')
    ap.add_argument('--directions', nargs='+', choices=('rx', 'duplex'), default=['rx', 'duplex'])
    args = ap.parse_args()
    if not 0 < args.duration <= 300 or any(not 0 <= r <= 100000 for r in args.rates):
        ap.error('Duration/rate out of bounded test range')
    sys.exit(asyncio.run(main(args)))
