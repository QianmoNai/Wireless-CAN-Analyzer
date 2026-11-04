#!/usr/bin/env python3
"""One idle pinned-TLS connection with numeric phase/TCP observations; never retries.

Run with desktop/.venv/bin/python. No serial, BLE, network changes or payload logs.
"""
import argparse
import array
import asyncio
import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))


def tcp_numbers(sock):
    """Stable Linux tcp_info prefix plus supported later counters; no packet data."""
    b = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 232)
    result = {'state': b[0]}
    fields = {'rto_us': 8, 'unacked': 24, 'sacked': 28, 'lost': 32,
              'retrans': 36, 'rtt_us': 68, 'rttvar_us': 72,
              'snd_cwnd': 80, 'total_retrans': 100}
    for name, offset in fields.items():
        if len(b) >= offset+4:
            result[name] = struct.unpack_from('=I', b, offset)[0]
    for name, offset in {'bytes_acked': 120, 'bytes_received': 128,
                         'bytes_sent': 200, 'bytes_retrans': 208}.items():
        if len(b) >= offset+8:
            result[name] = struct.unpack_from('=Q', b, offset)[0]
    queued = array.array('i', [0])
    fcntl.ioctl(sock.fileno(), 0x5411, queued, True)  # SIOCOUTQ, read-only.
    result['send_queue_bytes'] = queued[0]
    return result


def route_check(ip, dev, source):
    value = json.loads(subprocess.check_output(['ip', '-j', 'route', 'get', ip], text=True))[0]
    safe = {name: value.get(name) for name in ('dst', 'dev', 'prefsrc')}
    if safe['dev'] != dev or safe['prefsrc'] != source:
        raise ValueError('Required route mismatch')
    return safe


class Trace:
    def __init__(self):
        self.began = time.monotonic_ns()
        self.rows = []
        self.active = None

    def ms(self):
        return round((time.monotonic_ns()-self.began)/1e6, 3)

    def start(self, name):
        row = {'name': name, 'start_ms': self.ms()}
        self.rows.append(row)
        self.active = row

    def end(self, error=None):
        if self.active is None:
            return
        self.active['end_ms'] = self.ms()
        self.active['duration_ms'] = round(self.active['end_ms']-self.active['start_ms'], 3)
        self.active['ok'] = error is None
        if error:
            self.active['error_type'] = error
        self.active = None


async def measure(options):
    from can_scope import transports
    from can_scope.connection_store import connection_folder, load_connection
    from can_scope.protocol import device_settings_command, device_settings_status, u32
    from can_scope.wifi_diagnostics import Snapshot, query

    if os.geteuid() == 0:
        raise ValueError('Run as the normal desktop user')
    report = {'label': options.label, 'ip': options.ip, 'identity': options.identity,
              'attempts': 1, 'phases': [], 'tcp_samples': [], 'ss_samples': [],
              'start_utc': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'timeouts': {'tcp_plus_tls_s': 5, 'application_auth_s': 5, 'query_s': 3},
              'uart_supply': False, 'serial_opened': False}
    trace = Trace()
    loop = asyncio.get_running_loop()
    original_open = asyncio.open_connection
    original_connect = loop.sock_connect
    original_compare = transports.hmac.compare_digest
    observed = None
    transport = None
    snapshot = None
    request = 151
    reply = loop.create_future()
    sampler = None

    async def samples():
        last_ss = -1.0
        while True:
            if observed is not None:
                try:
                    report['tcp_samples'].append({'at_ms': trace.ms(), **tcp_numbers(observed)})
                except OSError:
                    pass
            if trace.ms()-last_ss >= 250:
                began = trace.ms()
                proc = await asyncio.create_subprocess_exec(
                    'ss', '-tin', 'dst', options.ip, 'dport', '=', ':32323',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                output, _ = await proc.communicate()
                report['ss_samples'].append({'start_ms': began, 'end_ms': trace.ms(),
                                             'text': output.decode('ascii', errors='replace')})
                last_ss = trace.ms()
            await asyncio.sleep(.05)

    async def connected(sock, address):
        nonlocal observed
        observed = sock
        trace.start('tcp_connect')
        await original_connect(sock, address)
        trace.end()
        local = sock.getsockname()
        report['local_ip'] = local[0]
        if local[0] != options.source:
            report['fatal_gate'] = 'Unexpected local socket source'
            raise ValueError('Unexpected local socket source')
        trace.start('tls_handshake')

    async def opened(*args, **kwargs):
        reader, writer = await original_open(*args, **kwargs)
        trace.end()
        trace.start('certificate_pin')
        return reader, writer

    def compared(a, b):
        result = original_compare(a, b)
        trace.end(None if result else 'PinMismatch')
        if result:
            trace.start('application_auth')
        return result

    def receive(p):
        if p[1] == 22 and u32(p, 16) == request and not reply.done():
            reply.set_result(p)
        if snapshot:
            snapshot.receive(p)

    try:
        try:
            report['route_before'] = route_check(options.ip, options.dev, options.source)
        except Exception:
            report['fatal_gate'] = 'Required route mismatch before connection'
            raise
        found = []
        await transports.discover(options.identity, lambda v: found.append(v['address']),
                                  broadcast=options.ip, timeout=3)
        report['udp_identity_addresses'] = found
        if 'wifi:'+options.ip not in found:
            report['fatal_gate'] = 'Target identity not discovered at required IP'
            raise ValueError('Target identity gate failed')
        saved = load_connection(connection_folder()/'connection-1.json')
        if saved.get('id') != options.identity:
            report['fatal_gate'] = 'Saved identity mismatch'
            raise ValueError('Saved identity mismatch')
        sampler = asyncio.create_task(samples())
        loop.sock_connect = connected
        asyncio.open_connection = opened
        transports.hmac.compare_digest = compared
        transport = transports.LanTransport(receive, lambda _: None)
        try:
            await transport.connect(options.ip, saved.get('fp'), saved.get('token'))
            trace.end()
            report['pinned_auth'] = True
        finally:
            # Trust values stay in the normal transport/cache path, never in report.
            saved.clear()
            loop.sock_connect = original_connect
            asyncio.open_connection = original_open
            transports.hmac.compare_digest = original_compare
        trace.start('first_query')
        await transport.write(device_settings_command(token=request))
        state = device_settings_status(await asyncio.wait_for(reply, 3))
        state.pop('token', None)
        report['settings'] = state
        trace.end()
        if (state['network'], state['data'], state['baud']) != (1, 1, 115200):
            report['fatal_gate'] = 'Unexpected device mode or baud'
            raise ValueError('Mode gate failed')
        trace.start('p0_snapshot')
        snapshot = Snapshot(request+1)
        await transport.write(query(request+1))
        report['diagnostics'] = await asyncio.wait_for(snapshot.future, 3)
        trace.end()
        if report['diagnostics']['fields']['tcp_nodelay']['value'] != 0:
            report['fatal_gate'] = 'Expected NODELAY=0'
            raise ValueError('NODELAY gate failed')
        neighbours = json.loads(subprocess.check_output(
            ['ip', '-j', 'neigh', 'show', 'to', options.ip, 'dev', options.dev], text=True))
        report['neighbour_mac'] = [v.get('lladdr', '').lower() for v in neighbours]
        if options.mac.lower() not in report['neighbour_mac']:
            report['fatal_gate'] = 'Target MAC mismatch or unavailable'
            raise ValueError('MAC gate failed')
        report['success'] = True
    except Exception as exc:
        trace.end(type(exc).__name__)
        report['success'] = False
        report['error_type'] = type(exc).__name__
    finally:
        loop.sock_connect = original_connect
        asyncio.open_connection = original_open
        transports.hmac.compare_digest = original_compare
        if observed is not None:
            try:
                report['tcp_samples'].append({'at_ms': trace.ms(), **tcp_numbers(observed)})
            except OSError:
                pass
        if transport:
            await transport.close()
        if sampler:
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        try:
            report['route_after'] = route_check(options.ip, options.dev, options.source)
        except Exception as exc:
            report['fatal_gate'] = 'Route no longer matches'
            report['route_error_type'] = type(exc).__name__
        report['phases'] = trace.rows
        report['elapsed_ms'] = trace.ms()
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ip', default='192.168.1.221')
    ap.add_argument('--dev', default='enp3s0')
    ap.add_argument('--source', default='192.168.1.131')
    ap.add_argument('--identity', default='90da7273722c')
    ap.add_argument('--mac', default='90:da:72:73:72:2c')
    ap.add_argument('--label', required=True)
    ap.add_argument('--output', type=Path, required=True)
    options = ap.parse_args()
    # Refuse overwrite before any connection. Only numeric schemas reach disk.
    fd = os.open(options.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        report = asyncio.run(measure(options))
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: report[k] for k in ('label', 'success', 'phases')}, ensure_ascii=False))
    return 2 if report.get('fatal_gate') else (0 if report['success'] else 1)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Never echo exception args/repr from TLS, cache or backend.
        print('CHECK_FAILED', type(exc).__name__)
        sys.exit(2)
