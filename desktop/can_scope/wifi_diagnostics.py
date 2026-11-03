"""Opt-in numeric-only Wi-Fi QUERY diagnostics; never log packets or trust data."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import struct
import time

FIELDS = (
    'sample_ms mode uart_sequence uart_rx_bytes uart_drop_bytes '
    'fifo_overrun_events ring_high_bytes ring_full_events ring_lost_bytes '
    'uart_read_errors config_flush_bytes disconnected_bytes tx_discard_bytes '
    'notify_raw_depth notify_raw_high notify_control_depth notify_control_high '
    'notify_ack_depth notify_ack_high '
    'drop_notify_full drop_notify_reset drop_generation drop_inactive drop_suppressed drop_route '
    'lan_raw_depth lan_raw_high lan_control_depth lan_control_high '
    'lan_reject_full lan_reject_offline drop_lan_reset drop_tls '
    'tls_send_calls tls_send_failures tls_send_timeouts tls_want_events '
    'tls_block_us_total tls_block_us_max tls_call_us_total tls_call_us_max '
    'tls_pending_high_bytes tcp_kernel_send_high_bytes '
    'tcp_closes tcp_close_reason tcp_read_failures '
    'tls_last_error socket_last_errno tcp_nodelay tls_handshake_failures '
    'wifi_rssi wifi_rate_kbps wifi_retries wifi_disconnects wifi_reason wifi_phy_mode'
).split()
UNAVAILABLE = {'ring_lost_bytes', 'tcp_kernel_send_high_bytes', 'wifi_rate_kbps',
               'wifi_retries', 'wifi_phy_mode'}
SIGNED = {'wifi_rssi', 'tls_last_error'}
EVENT = 26


def query(token):
    if type(token) is not int or not 0 < token <= 0xffffffff:
        raise ValueError('无效诊断请求序号')
    return bytes((1, 0x19, 2)) + bytes(13) + struct.pack('<I', token)


class Snapshot:
    def __init__(self, token):
        self.token = token
        self.future = asyncio.get_running_loop().create_future()
        self.header = None
        self.records = {}

    def receive(self, p):
        if len(p) != 20 or p[0] != 1:
            return
        if p[1] == 18 and p[2] and int.from_bytes(p[4:8], 'little') == self.token:
            if not self.future.done():
                self.future.set_exception(ValueError('固件不支持此只读诊断 QUERY'))
            return
        if p[1] != EVENT or int.from_bytes(p[16:20], 'little') != self.token or self.future.done():
            return
        try:
            if p[3] != len(FIELDS):
                raise ValueError('诊断字段版本不匹配')
            a, b, c = struct.unpack_from('<III', p, 4)
            if p[2] == 255:
                if self.header is not None or a != 1:
                    raise ValueError('诊断快照版本或顺序错误')
                self.header = dict(version=a, boot_id=b, serial=c)
                return
            if self.header is None or p[2] != len(self.records) or p[2] >= len(FIELDS):
                raise ValueError('诊断分片缺失或重复')
            name = FIELDS[p[2]]
            value = a - (1 << 32) if name in SIGNED and a & 0x80000000 else a
            available = not (name in UNAVAILABLE and a == 0xffffffff or
                             name == 'wifi_rssi' and value == -(1 << 31))
            self.records[name] = dict(value=value if available else None, available=available,
                                      at_ms=b, uart_sequence=c)
            if len(self.records) == len(FIELDS):
                self.future.set_result(dict(**self.header, fields=self.records))
        except ValueError as exc:
            self.future.set_exception(exc)

    def cancel(self):
        if not self.future.done():
            self.future.cancel()


class NumericLog:
    """Only internally constructed numeric schemas enter this private JSONL file."""
    def __init__(self, path):
        path = Path(path)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('诊断日志必须属于当前用户，且是权限 0600 的普通文件')
            self.file = os.fdopen(fd, 'a', encoding='utf8')
        except BaseException:
            os.close(fd)
            raise

    def write(self, event, slot, epoch, **values):
        # Call sites only supply counters/safe snapshots. No exception text,
        # arbitrary device JSON, SSID, frame hex, fingerprint or token.
        row = dict(event=event, slot=slot, epoch=epoch,
                   utc=datetime.now(timezone.utc).isoformat(), host_monotonic=time.monotonic(), **values)
        self.file.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.file.flush()

    def close(self):
        self.file.close()


class WifiDiagnostics:
    def __init__(self, channel, log, slot=1):
        self.channel, self.log, self.slot = channel, log, slot
        self.pending = None
        self.latest = None
        self.latest_at = None
        self.previous = None
        self.bytes = self.gaps = self.duplicates = self.reordered = 0
        self.total_bytes = self.total_gaps = 0
        self.first = self.last = None
        self.began = time.monotonic()
        self.queued = False
        self.unsupported = False

    def emit(self, event, **values):
        self.log.write(event, self.slot, self.channel.epoch, **values)

    def receive(self, p):
        if self.pending:
            self.pending.receive(p)
        if len(p) == 20 and p[0] == 1 and p[1] == 19 and 1 <= p[2] <= 12 and not p[3]:
            seq = int.from_bytes(p[16:], 'little')
            if self.previous is not None:
                distance = (seq - self.previous) & 0xffffffff
                if not distance:
                    self.duplicates += 1
                elif distance < 0x80000000:
                    self.gaps += distance - 1
                    self.total_gaps += distance - 1
                else:
                    self.reordered += 1
            if self.first is None:
                self.first = seq
            self.last = self.previous = seq
            self.bytes += p[2]
            self.total_bytes += p[2]
        return len(p) == 20 and p[1] == EVENT

    def tick(self, partial=False):
        now = time.monotonic()
        elapsed = now - self.began
        self.emit('rx_second' if not partial else 'rx_partial', elapsed_s=elapsed,
                  bytes=self.bytes, Bps=self.bytes / elapsed if elapsed else 0,
                  sequence_gaps=self.gaps, duplicates=self.duplicates, reordered=self.reordered,
                  first_sequence=self.first, last_sequence=self.last,
                  total_bytes=self.total_bytes, total_sequence_gaps=self.total_gaps,
                  connected=self.channel.ready, desktop_drop_packets=self.channel.dropped,
                  snapshot_serial=self.latest['serial'] if self.latest else None,
                  snapshot_age_s=now-self.latest_at if self.latest_at is not None else None)
        self.bytes = self.gaps = self.duplicates = self.reordered = 0
        self.first = self.last = None
        self.began = now

    async def poll(self):
        self.queued = False
        if self.unsupported or not self.channel.ready or self.channel.mode != 'wifi':
            return
        snapshot = Snapshot(self.channel.next_token())
        self.pending = snapshot
        epoch = self.channel.epoch
        try:
            await self.channel.write(query(snapshot.token))
            result = await asyncio.wait_for(asyncio.shield(snapshot.future), 3)
            if epoch != self.channel.epoch:
                return
            self.latest, self.latest_at = result, time.monotonic()
            self.emit('device_snapshot', snapshot=result)
        except asyncio.CancelledError:
            self.log.write('snapshot_incomplete', self.slot, epoch, received_fields=len(snapshot.records))
            raise
        except Exception as exc:
            if isinstance(exc, ValueError):
                self.unsupported = True
            self.log.write('snapshot_error', self.slot, epoch, error_type=type(exc).__name__, received_fields=len(snapshot.records))
        finally:
            snapshot.cancel()
            if self.pending is snapshot:
                self.pending = None

    def disconnected(self):
        self.tick(partial=True)
        self.emit('link_closed', last_snapshot=self.latest)
        self.previous = None
        self.queued = False
        self.unsupported = False
        self.latest = self.latest_at = None
        if self.pending:
            self.pending.cancel()

    async def run(self):
        try:
            while True:
                await asyncio.sleep(1)
                self.tick()
                if (self.channel.ready and self.channel.mode == 'wifi' and not self.queued and
                        not self.pending and not self.unsupported and not self.channel.busy and
                        self.channel.commands.empty()):
                    self.queued = True
                    self.channel.enqueue('wifiDiagnostics', [])
        finally:
            self.tick(partial=True)
