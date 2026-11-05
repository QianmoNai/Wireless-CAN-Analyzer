#!/usr/bin/env python3
"""Opt-in CAN bench for the connected STM32H723 motor/IMU simulator.

Switches the analyzer between USB/BLE/Wi-Fi, receives real CAN traffic and
optionally leases 0x7FF status queries to virtual motors 1..4. No enable or
motion command is sent. Always attempts to restore USB + CAN on exit.
Run with desktop/.venv/bin/python; close other owners of the analyzer first.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'desktop'))
sys.path.insert(0, str(ROOT/'scripts'))
from linux_tcp_info import read as read_tcp_info
from can_scope.connection_store import connection_folder, load_connection
from can_scope.protocol import device_settings_command, ipv4, packet, u32
from can_scope.transports import BleTransport, LanTransport
from can_scope.usb_transport import UsbTransport, prepare_usb


def percentiles(values):
    values = sorted(values)
    return {key: values[round((len(values)-1)*p)] if values else None
            for key, p in [('p50', .5), ('p95', .95), ('max', 1)]}


async def select(port, mode):
    await prepare_usb(port, data=0)
    if mode == 'usb':
        return
    confirmed = asyncio.Event()
    link = UsbTransport(lambda p: confirmed.set() if p[1] == 22 and u32(p, 16) == 99 else None, lambda _: None)
    try:
        await link.connect(port)
        await link.write(device_settings_command(network={'ble': 0, 'wifi': 1}[mode], data=0, token=99))
        await asyncio.wait_for(confirmed.wait(), 3)
    finally:
        await link.close()
    await asyncio.sleep(1)


async def connect_wifi(link, saved, interface=None):
    """Choose a real, identity-verified socket route for a controlled A/B.

    The route selector is overridden only during this bench's connection setup;
    UDP, TCP, pinned TLS and authentication all use the production transport.
    """
    from can_scope import lan_routes
    original = lan_routes.interface_for
    if interface:
        if interface not in lan_routes.interfaces() or not saved.get('id'):
            raise ValueError('Wi-Fi A/B requires an available Linux interface and saved device identity')
        async def selected(ip, identity, port):
            if await lan_routes._probe(interface, ip, identity, port, .75) != interface:
                raise ConnectionError('Device did not answer identity discovery on the requested interface')
            return interface
        lan_routes.interface_for = selected
    try:
        await link.connect(saved['ip'], saved['fp'], saved['token'], identity=saved.get('id'))
    finally:
        lan_routes.interface_for = original
    link.source_ip = link.writer.get_extra_info('sockname')[0]
    sock = link.writer.get_extra_info('socket')
    bound = None
    if hasattr(socket, 'SO_BINDTODEVICE'):
        bound = sock.getsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, 256).split(b'\0')[0].decode() or None
    link.bound_interface = bound
    if interface and bound != interface:
        raise ConnectionError('Socket interface binding was not applied; refusing a different A/B path')


async def observe_wifi_failure(link, kinds, statuses, seconds):
    """Observe recovery without sending, retrying or renewing any CAN query."""
    # Keep the receiver alive, but stop the transport's periodic heartbeat too:
    # merely stopping query renewals would still write every three seconds.
    heartbeat_code = getattr(getattr(link, 'heartbeat', None), '__code__', None)
    heartbeats = [task for task in getattr(link, 'tasks', ())
                  if heartbeat_code is not None
                  and getattr(task.get_coro(), 'cr_code', None) is heartbeat_code]
    for task in heartbeats:
        task.cancel()
    if heartbeats:
        await asyncio.gather(*heartbeats, return_exceptions=True)
    started = time.monotonic()
    initial_rx = kinds[1]
    rows = []
    while True:
        elapsed = time.monotonic()-started
        writer = getattr(link, 'writer', None)
        rows.append(dict(at_s=elapsed, additional_rx=kinds[1]-initial_rx,
            last_status=dict(statuses[-1]) if statuses else None,
            tcp=read_tcp_info(writer.get_extra_info('socket')) if writer else None))
        if elapsed >= seconds:
            break
        await asyncio.sleep(min(.5, seconds-elapsed))
    return dict(scope='Passive after failure: no writes, retries or lease renewals; '
                'original measurement ends at failure',
                heartbeat_tasks_stopped=len(heartbeats), samples=rows)


async def measure(options, mode, saved):
    await select(options.port, mode)
    kinds, ids, gaps = Counter(), Counter(), Counter()
    stamps, pending, statuses, errors, command_ms = {}, {}, [], [], []
    ordering = Counter()
    arrivals, host_gaps = {}, Counter()
    traffic_start = None
    types = (UsbTransport, BleTransport, LanTransport)
    link_type = types[('usb', 'ble', 'wifi').index(mode)]
    started = time.monotonic()
    case_started_utc = datetime.now(timezone.utc).isoformat()

    def received(p):
        kinds[p[1]] += 1
        if p[1] == 1:
            identifier, stamp = u32(p, 4), u32(p, 16)
            ids[f'{identifier:03X}'] += 1
            key = f'{identifier:03X}'+('/'+str(p[8]) if identifier == 0x60 else '')
            if traffic_start is not None:
                now = time.monotonic()
                host_gaps[key] = max(host_gaps[key], now-arrivals.get(key, traffic_start))
                arrivals[key] = now
            if key in stamps:
                delta = (stamp-stamps[key]) & 0xffffffff
                if delta == 0:
                    ordering['same_timestamp'] += 1
                elif delta >= 0x80000000:
                    ordering['backwards_timestamp'] += 1
                if delta < 0x80000000:
                    gaps[key] = max(gaps[key], delta)
            stamps[key] = stamp
        elif p[1] == 4:
            statuses.append(dict(at_s=time.monotonic()-started, rx=u32(p, 4), tx=u32(p, 8),
                                 failures=u32(p, 12), drops=u32(p, 16), state=p[2], delivered=kinds[1]))
        elif p[1] == 8:
            waiter = pending.get(u32(p, 16))
            if waiter and not waiter.done():
                waiter.set_result(p)
            if p[2] not in (0, 1):
                errors.append(dict(event='poll', result=p[2], detail=p[3], motor=u32(p, 4)))

    def disconnected(_):
        exc = sys.exception()
        errors.append(dict(event='disconnect', type=type(exc).__name__ if exc else None))
    link = link_type(received, disconnected)
    token = 0x50000000

    async def query(identifier, hz):
        nonlocal token
        token += 1
        future = asyncio.get_running_loop().create_future()
        pending[token] = future
        sent = time.monotonic()
        try:
            await link.write(packet(0x14 if identifier else 0x15, x=identifier, y=hz, token=token))
            reply = await asyncio.wait_for(future, 2)
            command_ms.append((time.monotonic()-sent)*1000)
            if reply[2] != (0 if hz else 1):
                raise RuntimeError(f'Simulator query lease rejected: motor={identifier}, result={reply[2]}, detail={reply[3]}, hz={u32(reply,8)}')
        finally:
            pending.pop(token, None)

    cpu = time.process_time()
    snapshots = []
    host_tcp = []
    connected = None
    measured_ended = None

    def snapshot():
        first, last = statuses[0] if statuses else {}, statuses[-1] if statuses else {}
        deltas = {key: (last[key]-first[key]) & 0xffffffff for key in ('rx', 'tx', 'failures', 'drops')} if first else {}
        end = measured_ended or time.monotonic()
        fixture_keys = ('201', '202', '205', '060/1', '060/2', '060/3', '060/4')
        if options.poll_hz:
            fixture_keys += ('011', '012', '013', '014')
        silence = {key: round(max(host_gaps[key], end-arrivals.get(key, traffic_start or end)), 4)
                   for key in fixture_keys}
        return dict(mode=mode, case_started_utc=case_started_utc, started_monotonic=started,
                    interface=getattr(link, 'interface', None), duration_s=(measured_ended or time.monotonic())-(connected or started),
                    source_ip=getattr(link, 'source_ip', None),
                    bound_interface=getattr(link, 'bound_interface', None),
                    connect_s=connected-started if connected else None,
                    cpu_s=time.process_time()-cpu, poll_hz=options.poll_hz,
                    counts=dict(kinds), ids=dict(ids), max_device_gap_ms=dict(gaps), timestamp_order=dict(ordering),
                    fixture_max_silence_s=silence,
                    status=list(statuses), device_delta=deltas, query_ack_ms=percentiles(command_ms),
                    diagnostics=snapshots, host_tcp=list(host_tcp), errors=list(errors))

    def capture_tcp():
        writer = getattr(link, 'writer', None)
        if mode == 'wifi' and writer is not None:
            host_tcp.append(dict(at_s=time.monotonic()-started,
                metrics=read_tcp_info(writer.get_extra_info('socket'))))

    async def wifi_diagnostic():
        nonlocal token
        from can_scope.wifi_diagnostics import Snapshot, query as diagnostic_query
        token += 1
        wifi_snapshot = Snapshot(token)
        original_received = link.received
        def receive_diagnostic(p):
            wifi_snapshot.receive(p)
            original_received(p)
        link.received = receive_diagnostic
        try:
            await link.write(diagnostic_query(wifi_snapshot.token))
            result = await asyncio.wait_for(wifi_snapshot.future, 3)
            result['phase'] = 'before' if connected is None else 'after'
            result['scope'] = ('Queue depths and TLS counters apply to this Wi-Fi connection; '
                               'drop/reject fields count UART payload bytes, not CAN packets. '
                               'Use CAN status for CAN drop totals.')
            snapshots.append(result)
        finally:
            link.received = original_received
            wifi_snapshot.cancel()
    try:
        if mode == 'usb':
            await link.connect(options.port)
        elif mode == 'ble':
            await link.connect(saved['address'])
        else:
            # Mode switching restarts association. Keep setup outside the
            # measured traffic window; do not fill the TCP backlog before IP.
            await asyncio.sleep(10)
            # Wi-Fi association can outlast the transport's 5s connect timeout.
            # Retrying a connection sends no CAN command.
            for attempt in range(5):
                try:
                    await connect_wifi(link, saved, getattr(options, 'wifi_interface', None))
                    required_source = getattr(options, 'wifi_source_ip', None)
                    if required_source and link.source_ip != required_source:
                        raise ConnectionError('Wi-Fi source changed; refusing a different A/B path')
                    break
                except (OSError, asyncio.TimeoutError):
                    await link.close()
                    if attempt == 4:
                        raise
                    await asyncio.sleep(2)
                    link = LanTransport(received, disconnected)
        # Synchronize with the CAN owner after a session change. HELLO itself
        # precedes its asynchronous stop; this acknowledged stop is a barrier.
        await query(0, 0)
        if mode == 'wifi':
            await wifi_diagnostic()
        connected = time.monotonic()
        traffic_start = connected
        capture_tcp()
        # Connection/diagnostic setup is outside the reported traffic window.
        kinds.clear(); ids.clear(); gaps.clear(); stamps.clear(); ordering.clear(); statuses.clear()
        next_query = connected
        next_diagnostic = connected
        next_checkpoint = connected
        while time.monotonic()-connected < options.duration:
            if options.poll_hz and time.monotonic() >= next_query:
                for identifier in range(1, 5):
                    await query(identifier, options.poll_hz)
                next_query = time.monotonic()+1
            if options.ble_diagnostics and mode == 'ble' and time.monotonic() >= next_diagnostic:
                from ble_stress import DIAGNOSTICS, diagnostics
                snapshots.append(diagnostics(bytes(await link.client.read_gatt_char(DIAGNOSTICS))))
                next_diagnostic = time.monotonic()+5
            if any(e['event'] == 'disconnect' for e in errors):
                raise ConnectionError('Physical transport disconnected')
            if time.monotonic() >= next_checkpoint:
                capture_tcp()
                progress = snapshot()
                options.checkpoint(progress)
                if time.monotonic()-connected > 3:
                    missing = [key for key, gap in progress['fixture_max_silence_s'].items()
                               if time.monotonic()-arrivals.get(key, connected) > 3]
                    if missing:
                        errors.append(dict(event='fixture_stalled', streams=missing))
                        raise RuntimeError('STM32 fixture feedback stopped; check CAN and Bus-off')
                next_checkpoint = time.monotonic()+5
            await asyncio.sleep(.01)
        # Stop leases explicitly and collect their finite CAN/result tail.
        if options.poll_hz:
            for identifier in range(1, 5):
                await query(identifier, 0)
        await asyncio.sleep(.15)
        if options.ble_diagnostics and mode == 'ble':
            from ble_stress import DIAGNOSTICS, diagnostics
            snapshots.append(diagnostics(bytes(await link.client.read_gatt_char(DIAGNOSTICS))))
        measured_ended = time.monotonic()
        capture_tcp()
        row = snapshot()
        if mode == 'wifi':
            await wifi_diagnostic()  # End snapshot is outside the measured window.
        return row
    except BaseException as exc:
        capture_tcp()
        exc.bench_row = snapshot()
        exc.bench_row['failure'] = dict(type=type(exc).__name__)
        observation = getattr(options, 'failure_observe_seconds', 0)
        if mode == 'wifi' and observation and isinstance(exc, Exception):
            try:
                exc.bench_row['failure_observation'] = await observe_wifi_failure(link, kinds, statuses, observation)
            except Exception as observer_error:
                exc.bench_row['failure_observation'] = dict(failure=type(observer_error).__name__)
        raise
    finally:
        await link.close()


async def main(options):
    saved = load_connection(connection_folder()/f'connection-{options.channel}.json')
    report = dict(fixture='STM32H723 simulated motor/IMU over physical CAN', cases=[], ok=False)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        temporary = options.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        temporary.replace(options.output)
    def checkpoint(row):
        report['active'] = row
        save()
    options.checkpoint = checkpoint
    try:
        for mode in options.modes:
            try:
                row = await measure(options, mode, saved)
            except BaseException as exc:
                report['cases'].append(getattr(exc, 'bench_row', dict(mode=mode, failure=dict(type=type(exc).__name__))))
                report.pop('active', None)
                save()
                raise
            report['cases'].append(row)
            row['fixture_stream_ok'] = (all(row['ids'].get(identifier, 0) > 0
                for identifier in ('201', '202', '205', '060'))
                and all(gap <= 3 for gap in row['fixture_max_silence_s'].values()))
            row['ok'] = (row['fixture_stream_ok'] and not row['errors']
                and not row['device_delta'].get('failures', 1) and not row['device_delta'].get('drops', 1))
            report.pop('active', None)
            save()
            print(json.dumps({k: v for k, v in row.items() if k not in ('status', 'diagnostics')}, ensure_ascii=False), flush=True)
    finally:
        try:
            await prepare_usb(options.port, data=0)
            report['restored_usb_can'] = True
        finally:
            save()
    report['ok'] = all(row.get('ok') for row in report['cases']) and report.get('restored_usb_can', False)
    save()
    return report['ok']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, choices=['stm32h723'], help='explicitly identify the simulated CAN bench')
    parser.add_argument('--port', default='/dev/ttyACM0')
    parser.add_argument('--channel', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--modes', nargs='+', choices=['usb', 'ble', 'wifi'], default=['usb', 'ble', 'wifi'])
    parser.add_argument('--duration', type=float, default=60)
    parser.add_argument('--poll-hz', type=int, choices=range(0, 1001), default=0)
    parser.add_argument('--ble-diagnostics', action='store_true', help='read optional test-firmware counters; not needed for production firmware')
    parser.add_argument('--wifi-source-ip', type=ipv4, help='A/B only: accept a Wi-Fi connection only through this local IPv4 address; never changes OS routes')
    parser.add_argument('--wifi-interface', help='A/B only: identity-probe and bind this Linux interface; requires --wifi-source-ip')
    parser.add_argument('--failure-observe-seconds', type=float, default=0, help='Wi-Fi only: passively observe after failure without sending or retrying commands (0..30s)')
    parser.add_argument('--output', required=True, type=Path)
    options = parser.parse_args()
    if options.wifi_interface and not options.wifi_source_ip:
        parser.error('--wifi-interface requires --wifi-source-ip to verify the selected source')
    if not 5 <= options.duration <= 3600:
        parser.error('--duration must be 5..3600 seconds')
    if not 0 <= options.failure_observe_seconds <= 30:
        parser.error('--failure-observe-seconds must be 0..30')
    sys.exit(0 if asyncio.run(main(options)) else 1)
