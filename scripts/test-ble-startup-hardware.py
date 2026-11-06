#!/usr/bin/env python3
"""Opt-in BLE startup stages on an identified STM32 simulated CAN bench.

Records connection/raw/query/stop counters without a GUI. Optional BlueZ MTU
acquisition is an isolated comparison, never a production transport change.
Any new device or app drop fails acceptance even when all operations complete.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'desktop')]
from hardware_bench_lock import hardware_bench_lock
from can_scope.connection_store import connection_folder, load_connection
from can_scope.engine import Channel
from can_scope.protocol import WRITE, u32
from can_scope.transports import BleTransport
from usb_management import LocalUsb
from bleak import BleakClient
from ble_stress import DIAGNOSTICS, diagnostics, HEADER, FIELDS, UART_FIELDS


def diagnostic_snapshot(value):
    # Isolated v3 extends the existing read-only v2 ledger with 24 type counters.
    base = len(HEADER) + 3*len(FIELDS) + len(UART_FIELDS)
    if len(value) != 4*(base+24):
        return diagnostics(value)
    words = list(struct.unpack('<'+str(base+24)+'I', value))
    if words[:2] != [3, base+24]:
        raise ValueError('Unknown isolated diagnostic snapshot')
    prior = [2, base, *words[2:base]]
    result = diagnostics(struct.pack('<'+str(base)+'I', *prior))
    result.update(version=3, words=base+24,
                  drop_types={str(i):n for i,n in enumerate(words[base:])})
    return result


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True, choices=['stm32h723-dual'])
    parser.add_argument('--elf', required=True, type=Path)
    parser.add_argument('--port', required=True)
    parser.add_argument('--channel', type=int, choices=range(1,5), required=True)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--compare-mtu', action='store_true',
                        help='isolated BlueZ comparison; may acquire/close an extra notification')
    parser.add_argument('--diagnostics', action='store_true',
                        help='require a test-firmware characteristic; no optional cache read')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error('--repeat must be 1..20')
    if args.compare_mtu and sys.platform != 'linux':
        parser.error('--compare-mtu requires Linux BlueZ')
    saved = load_connection(connection_folder()/f'connection-{args.channel}.json')
    if not saved.get('id') or not saved.get('address'):
        parser.error('An identity-bound saved BLE connection is required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(ok=False, scope='Native physical BLE/STM32 simulation, not GUI or real motor proof',
                  rounds=[], compare_mtu=args.compare_mtu, diagnostics=args.diagnostics,
                  engine_sha256=hashlib.sha256((ROOT/'desktop/can_scope/engine.py').read_bytes()).hexdigest())
    def save():
        args.output.write_text(json.dumps(report, indent=2)+'\n')
    def monitor():
        usb = LocalUsb(args.port)
        try:
            if usb.identity().hex() != saved['id']:
                raise RuntimeError('Physical USB and saved BLE identities differ')
            return usb.monitor_drops()
        finally:
            usb.close()
    bench = module('startup_can_bench', ROOT/'scripts/test-can-hardware.py')
    physical = dict(mode_attempted=False, periodic_requested=False)
    async def select(mode):
        await asyncio.to_thread(monitor)  # Verify identity before every mode mutation.
        physical['mode_attempted'] = True
        await bench.select(args.port, mode)
    async def until(check, timeout=25):
        end = time.monotonic()+timeout
        while not check():
            if time.monotonic() > end:
                raise TimeoutError('Startup operation was not confirmed')
            await asyncio.sleep(.02)

    state = dict(row=None, stage='none')
    original_notify, original_notification = BleakClient.start_notify, BleTransport.notification
    async def start_notify(client, characteristic, *positional, **keywords):
        row = state['row']
        row['pre_notify_write_size'] = client.services.get_characteristic(WRITE).max_write_without_response_size
        if row['variant'] == 'pre-mtu':
            acquire = getattr(client._backend, '_acquire_mtu', None)
            if acquire is None:
                raise RuntimeError('Isolated BlueZ MTU API unavailable')
            await asyncio.wait_for(acquire(), 5)
            row['acquired_mtu'] = client.mtu_size
        return await original_notify(client, characteristic, *positional, **keywords)
    def notification(transport, sender, data):
        row = state['row']
        if row is not None:
            if len(row['early_notifications']) < 128:
                row['early_notifications'].append(dict(stage=state['stage'],
                    at_ms=round((time.monotonic()-row['started'])*1000, 2), bytes=len(data),
                    types=[data[i+1] for i in range(0,min(len(data)-1,240),20)]))
            row['notification_bytes'] += len(data)
            row['notification_count'] += 1
        return original_notification(transport, sender, data)

    def finish_row(row, after):
        if row['stages'] and row['stages'][0]['stage'] == 'before-mode':
            row['delta'] = {k:(v-row['stages'][0]['monitor'][k])&0xffffffff
                            for k,v in after.items()}
        row['ok'] = (row['completed'] and row.get('app_dropped') == 0
                     and 'delta' in row and not any(row['delta'].values()))

    async def measure(row):
        channel = None
        def emit(kind, data):
            if kind in ('packets', 'motorSamples'):
                channel.submit('delivered', [data['delivery']])
            if kind == 'error':
                row['errors'].append(data.get('message'))
            if kind == 'queryState' and not data.get('enabled') and data.get('message') == '查询已关闭':
                row['query_stop_confirmed'] = True
        channel = Channel(emit, dict(saved, mode='ble', usb_port=args.port))
        original_receive = channel.receive
        def receive(packet):
            if len(packet) == 20 and packet[0] == 1 and packet[1] == 1:
                row['can_frames'] += 1
                if not packet[2] & 3 and packet[3] == 8 and u32(packet,4) == 17 and packet[8] & 15 == 1:
                    row['dm_feedback'] += 1
            original_receive(packet)
        channel.receive = receive
        async def snapshot(name):
            state['stage'] = name
            entry = dict(stage=name, at_s=round(time.monotonic()-row['started'],4),
                         monitor=await asyncio.to_thread(monitor),
                         ready=channel.ready, capabilities=channel.capabilities)
            if args.diagnostics and channel.transport and channel.ready:
                if not channel.transport.client.services.get_characteristic(DIAGNOSTICS):
                    raise RuntimeError('Required diagnostic characteristic missing')
                entry['diagnostics'] = diagnostic_snapshot(bytes(await channel.transport.client.read_gatt_char(DIAGNOSTICS)))
                entry['monitor_after_diagnostic_read'] = await asyncio.to_thread(monitor)
            row['stages'].append(entry)
            save()
        try:
            await snapshot('before-mode')
            await select('ble')
            await snapshot('after-mode')
            channel.start()
            channel.enqueue('connect', [saved['address']])
            await until(lambda:channel.ready)
            await snapshot('connected')
            await asyncio.sleep(2)
            await snapshot('raw-warmup')
            if not row['can_frames']:
                raise AssertionError('Connected BLE produced no physical CAN frames')
            key = json.dumps(dict(profile='dm4310', id=1, master=17, feedbackId=17,
                pollHz=100, mode='mit', limits=dict(p=12.5,v=30,t=10)), separators=(',',':'))
            channel.submit('dashboard', [json.dumps([dict(key=key,family='dm',sampleHz=100)])])
            await until(lambda:bool(channel.capabilities & 8))
            feedback_before = row['dm_feedback']
            channel.enqueue('setPolling', [key,1,True,100])
            await until(lambda:key in channel.polls)
            await snapshot('query-confirmed')
            await asyncio.sleep(8)
            if not channel.ready or key not in channel.polls or channel.dropped:
                raise AssertionError('Query stopped or native delivery dropped')
            if row['dm_feedback'] <= feedback_before:
                raise AssertionError('Query acknowledged without physical DM feedback')
            await snapshot('query-steady')
            row['query_stop_confirmed'] = False
            channel.enqueue('setPolling', [key,1,False,100])
            await until(lambda:row['query_stop_confirmed'])
            await asyncio.sleep(.15)
            await snapshot('query-stopped')
            if row['errors']:
                raise AssertionError('Native channel reported an error')
            row['completed'] = True
        finally:
            try:
                await channel.close()
                row['app_dropped'] = channel.dropped
                await snapshot('disconnected')
            finally:
                await select('usb')
                await snapshot('restored-usb')

    with hardware_bench_lock():
        fixture = None
        identified = False
        BleakClient.start_notify, BleTransport.notification = start_notify, notification
        try:
            report['before'] = monitor()
            identified = True
            fixture = module('startup_fixture',ROOT/'scripts/test-dual-can-hardware.py').Fixture(args.elf)
            report['fixture_image_sha256'] = fixture.image_sha256
            if any(fixture.health().values()):
                raise RuntimeError('Fixture CAN health failed before test')
            physical['periodic_requested'] = True
            fixture.quiet(1)
            for turn in range(args.repeat):
                for variant in (['baseline','pre-mtu'] if args.compare_mtu else ['baseline']):
                    row = dict(turn=turn+1, variant=variant, started=time.monotonic(), stages=[],
                        early_notifications=[], notification_bytes=0, notification_count=0,
                        can_frames=0, dm_feedback=0, errors=[], completed=False)
                    state['row'] = row
                    report['rounds'].append(row)
                    save()
                    try:
                        asyncio.run(measure(row))
                    finally:
                        if row['stages']:
                            finish_row(row, row['stages'][-1]['monitor'])
                    print(json.dumps(dict(turn=turn+1, variant=variant, ok=row['ok'], delta=row['delta'])), flush=True)
                    save()
            report['functional_completed'] = all(x['completed'] for x in report['rounds'])
            report['ok'] = all(x['ok'] for x in report['rounds'])
        except Exception as exc:
            report.update(failure=type(exc).__name__, failure_message=str(exc)[:300])
        finally:
            BleakClient.start_notify, BleTransport.notification = original_notify, original_notification
            try:
                if physical['mode_attempted']:
                    asyncio.run(select('usb'))
                    report['restored_usb'] = True
                if physical['periodic_requested']:
                    fixture.quiet(1)
                    report['periodic_restored'] = True
                    report['health'] = fixture.health()
                    report['ok'] = report['ok'] and not any(report['health'].values())
                if identified:
                    report['after'] = monitor()
                    report['delta'] = {k:(v-report['before'][k])&0xffffffff
                                       for k,v in report['after'].items()}
                    report['ok'] = report['ok'] and not any(report['delta'].values())
                    if report['rounds']:
                        finish_row(report['rounds'][-1], report['after'])
                report['restored'] = bool(report.get('restored_usb') and report.get('periodic_restored'))
            except Exception as exc:
                report.update(ok=False, restore_failure=type(exc).__name__)
            save()
    print(json.dumps({k:report.get(k) for k in ('ok','failure','functional_completed','restored')}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
