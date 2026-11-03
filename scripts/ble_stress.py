"""Shared BLE stress primitives. No pairing, retries of writes, or CAN traffic."""
import asyncio
import hashlib
from pathlib import Path
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from can_scope.protocol import device_settings_command, packet, u32
from can_scope.transports import BleTransport

DIAGNOSTICS = 'c0de0005-1212-efde-1523-785feabcd123'
HEADER = ('version words uptime_ms generation mtu interval latency timeout param_changes '
          'param_status disconnects disconnect_reason notify_events notify_errors notify_rc '
          'pool_min heap_free heap_min type17_bypass connects phy_tx phy_rx param_requests '
          'param_request_rc pool1_count pool2_count resource_waits last_error_rc '
          'rc_enomem rc_enotconn rc_eagain rc_other').split()
FIELDS = ('enqueued full_packets full_bytes high_water max_age_ms alloc_fail reserve_reject '
          'submit_error submitted drop_alloc_bytes drop_reserve_bytes drop_submit_bytes '
          'reset_bytes other_bytes failed_packets depth').split()

UART_FIELDS = ('data_events data_event_bytes fifo_ovf buffer_full frame_err parity_err '
               'read_calls read_bytes ble_attempt_bytes ble_accept_bytes ble_reject_bytes '
               'disconnected_bytes dual_bytes config_flushes config_flush_bytes '
               'error_flushes error_flush_bytes tx_discard_bytes downstream_drop_bytes '
               'buffer_query_errors flush_errors read_errors event_high_water').split()


def diagnostics(value):
    base = len(HEADER) + 3 * len(FIELDS)
    if len(value) not in (4 * base, 4 * (base + len(UART_FIELDS))):
        raise ValueError('Invalid diagnostic snapshot length')
    words = struct.unpack('<' + 'I' * (len(value)//4), value)
    result = dict(zip(HEADER, words))
    if (result['version'], len(words)) not in ((1, base), (2, base + len(UART_FIELDS))) or result['words'] != len(words):
        raise ValueError('Unknown diagnostic version')
    for i, name in enumerate(('raw', 'control', 'ack')):
        start = len(HEADER) + i * len(FIELDS)
        result[name] = dict(zip(FIELDS, words[start:start + len(FIELDS)]))
    if result['version'] == 2:
        result['uart'] = dict(zip(UART_FIELDS, words[base:]))
    result['interval_ms'] = result['interval'] * 1.25
    result['timeout_ms'] = result['timeout'] * 10
    # Zero PHY means unavailable, never an inferred PHY or DLE result.
    return result


def account_ledgers(row):
    """Keep host acceptance, UART reads and known discards distinct.

    A hardware error event is not a byte count. Unobserved ingress remains a
    failing residual, never manufactured drop bytes from input minus output.
    Use quiescent boundaries; lifetime diagnostic words are not transactional.
    """
    for key in ('rx', 'tx', 'drop'):
        row['delta_'+key] = (row['after'][key]-row['before'][key]) & 0xffffffff
    row['input_read_difference'] = row['input_bytes']-row['delta_rx']
    row['input_accounting_difference'] = row['input_read_difference']
    row['drop_accounting_difference'] = row['missing_bytes']-row['delta_drop']
    d0, d1 = row['diagnostics_before'], row['diagnostics_after']
    if not d0 or not d1:
        return
    causes = ('full_bytes', 'drop_alloc_bytes', 'drop_reserve_bytes',
              'drop_submit_bytes', 'reset_bytes', 'other_bytes')
    row['drop_causes'] = {k: (d1['raw'][k]-d0['raw'][k]) & 0xffffffff for k in causes}
    known_uart = 0
    if 'uart' in d0 and 'uart' in d1:
        u = row['uart_delta'] = {k: (d1['uart'][k]-v) & 0xffffffff
                                for k, v in d0['uart'].items() if k != 'event_high_water'}
        row['uart_drop_causes'] = {k: u[k] for k in
            ('config_flush_bytes', 'error_flush_bytes', 'disconnected_bytes', 'tx_discard_bytes')}
        known_uart = sum(row['uart_drop_causes'].values())
        flushed = u['config_flush_bytes']+u['error_flush_bytes']
        row['input_accounting_difference'] -= flushed
        row['uart_layer_residuals'] = dict(
            status_vs_read=row['delta_rx']-u['read_bytes'],
            read_vs_route=u['read_bytes']-u['ble_attempt_bytes']-u['disconnected_bytes']-u['dual_bytes'],
            admission=u['ble_attempt_bytes']-u['ble_accept_bytes']-u['ble_reject_bytes'],
            rejected_vs_full=u['ble_reject_bytes']-row['drop_causes']['full_bytes'],
            reported_drop=row['delta_drop']-u['ble_reject_bytes']-u['downstream_drop_bytes']-known_uart)
        # Informational only: event delivery can itself be incomplete. Require
        # conservation with reads + known flushes before interpreting this gap.
        row['driver_event_read_difference'] = u['data_event_bytes']-u['read_bytes']-flushed
        row['host_vs_driver_event_difference'] = row['input_bytes']-u['data_event_bytes']
    row['raw_cause_accounting_difference'] = row['delta_drop']-sum(row['drop_causes'].values())
    row['cause_accounting_difference'] = row['raw_cause_accounting_difference']-known_uart


def percentiles(values):
    a = sorted(values)
    return dict(n=len(a), **{k: a[round((len(a)-1)*p)] if a else None
                           for k, p in [('p50', .5), ('p95', .95), ('max', 1)]})


def digest(data):
    return hashlib.sha256(data).hexdigest()


def split_prefix(stream, marker):
    """A sync marker establishes the boundary; never trim measured test data."""
    if not stream.endswith(marker) or stream.count(marker) != 1:
        raise ValueError('Missing/ambiguous sync marker or unexpected suffix: ' + stream.hex())
    return bytes(stream[:-len(marker)])


class ChannelReceiver:
    """Actual Channel RX batching with immediate harness receipts, no Qt page."""
    def __init__(self):
        from can_scope.engine import Channel
        self.data = bytearray()
        self.ages = []
        self.high_water = 0
        self.channel = Channel(self.emit)
        self.channel.ready = True
        self.channel.uart = True

    def emit(self, name, value):
        if name == 'packets':
            for item in value['items']:
                if 'uart' in item:
                    self.data.extend(bytes.fromhex(item['uart']))
                    self.ages.append(item['age'])
            self.channel.submit('delivered', [value['delivery']])

    def receive(self, p):
        self.channel.receive(p)
        self.high_water = max(self.high_water, len(self.channel.packets))

    async def flush(self):
        while True:
            await asyncio.sleep(.025)
            self.channel.flush_once()

    def finish(self):
        # No producer remains; drain actual bounded batches with receipts.
        while self.channel.packets or self.channel.controls:
            self.channel.flush_once()
        return dict(bytes=len(self.data), sha256=digest(self.data),
                    dropped=self.channel.dropped, high_water=self.high_water,
                    age_ms=percentiles(self.ages), receipt='immediate harness; no GUI')


class Owner:
    """One GATT owner, including token reply and diagnostic read; epoch isolated."""
    def __init__(self, timeout=3):
        self.lock = asyncio.Lock()
        self.timeout = timeout
        self.epoch = 0
        self.token = 0x63000000
        self.pending = {}
        self.latest = None
        self.status_serial = 0
        self.sink = None
        self.lost = []
        self.stale_callbacks = 0
        self.types = {}
        self.tx = []
        self.transport = None

    def next_token(self):
        self.token += 1
        return self.token

    async def connect(self, device):
        self.epoch += 1
        epoch = self.epoch
        def receive(p):
            if epoch != self.epoch:
                self.stale_callbacks += 1
                return
            self.receive(p)
        def lost(reason):
            if epoch == self.epoch:
                self.lost.append(dict(epoch=epoch, reason=reason))
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError(reason))
        self.latest = None
        self.transport = BleTransport(receive, lost)
        await self.transport.connect(device)

    def receive(self, p):
        if len(p) != 20 or p[0] != 1:
            return
        self.types[p[1]] = self.types.get(p[1], 0) + 1
        if p[1] == 21:
            self.latest = dict(baud=u32(p, 4), rx=u32(p, 8), tx=u32(p, 12),
                               drop=u32(p, 16), ready=p[3] == 0)
            self.status_serial += 1
        key = (p[1], u32(p, 16))
        if p[1] == 18 and p[2]:
            # The rejection token is at offset 4, not the event token field.
            for (kind, token), f in list(self.pending.items()):
                if token == u32(p, 4) and not f.done():
                    f.set_exception(ValueError('Command rejected: ' + p.hex()))
        elif key in self.pending and not self.pending[key].done():
            self.pending[key].set_result(p)
        if self.sink:
            self.sink(p)

    async def exchange(self, p, kind, submission=None):
        async with self.lock:
            key = kind, u32(p, 16)
            f = asyncio.get_running_loop().create_future()
            self.pending[key] = f
            started = time.monotonic()
            try:
                await self.transport.write(p)
                if submission is not None:
                    submission['gatt_write_completed'] = True
                r = await asyncio.wait_for(asyncio.shield(f), self.timeout)
                return r, (time.monotonic()-started)*1000
            finally:
                self.pending.pop(key, None)
                if not f.done():
                    f.cancel()

    async def query(self, **settings):
        r, ms = await self.exchange(device_settings_command(token=self.next_token(), **settings), 22)
        if r[2] != int(bool(settings)):
            raise ValueError('Settings failed: ' + r.hex())
        return r, ms

    async def send(self, data):
        token = self.next_token()
        row = dict(epoch=self.epoch, token=token, hex=data.hex(), outcome='unknown', gatt_write_completed=False)
        self.tx.append(row)  # Before write: timeout may still have physical output.
        p = bytearray(packet(0x18, a=len(data), token=token))
        p[4:4+len(data)] = data
        try:
            r, ms = await self.exchange(p, 20, row)
            if r[2] or r[3] != len(data) or r[4:4+len(data)] != data:
                row.update(outcome='rejected', response=r.hex())
                raise ValueError('Bad physical UART completion: ' + r.hex())
            row.update(outcome='confirmed', rtt_ms=ms)
            return ms
        except BaseException as exc:
            row['error'] = repr(exc)
            raise  # No automatic replay, including cancellation/write timeout.

    async def snapshot(self):
        async with self.lock:
            if not self.transport.client.services.get_characteristic(DIAGNOSTICS):
                return None
            value = await asyncio.wait_for(self.transport.client.read_gatt_char(DIAGNOSTICS), self.timeout)
            return diagnostics(value)

    async def status(self, baud):
        serial = self.status_serial
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.status_serial > serial and self.latest['baud'] == baud and self.latest['ready']:
                return dict(self.latest)
            await asyncio.sleep(.005)
        raise TimeoutError('Fresh UART type21 status missing')

    async def close(self):
        self.epoch += 1  # Reject old callbacks even if backend delivers after close.
        self.sink = None
        for f in self.pending.values():
            if not f.done():
                f.cancel()
        if self.transport:
            await self.transport.close()
