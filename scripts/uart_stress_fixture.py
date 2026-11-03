"""Dedicated, bounded UART load writer shared by opt-in hardware tests.

Accepted bytes are host write completions, NOT proof of physical UART output.
Always join before closing/restoring a port; compare against device RX separately.
"""
import random
from pathlib import Path
import threading
import time


def rss_bytes():
    try:
        return int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                        if line.startswith('VmRSS:')))*1024
    except (OSError, StopIteration):
        return None


def low_latency(port, requested=False):
    result = dict(requested=requested)
    if requested:
        try:
            import array
            import fcntl
            import termios
            flags = array.array('i', [0]*32)
            fcntl.ioctl(port.fileno(), termios.TIOCGSERIAL, flags)
            result['previous'] = bool(flags[4] & 0x2000)
            port.set_low_latency_mode(True)
            result['applied'] = True
        except (OSError, ValueError, AttributeError, ImportError) as exc:
            result.update(applied=False, error=str(exc))
    return result


def restore_low_latency(port, result):
    if result.get('applied'):
        port.set_low_latency_mode(result['previous'])


class FixtureWriter:
    def __init__(self, port, duration, rate, chunk=4096, seed=921600, pattern='continuous'):
        self.port, self.duration, self.rate, self.chunk = port, duration, rate, chunk
        self.pattern = pattern
        self.expected = bytearray()
        self.events, self.latencies = [], []
        self.error = None
        self.unknown_accepted_bytes = False
        self.stop = threading.Event()
        self.done = threading.Event()
        self.rng = random.Random(seed)
        self.thread = threading.Thread(target=self._run, name='uart-fixture', daemon=False)

    def start(self, start=None):
        self.started = time.monotonic() if start is None else start
        self.thread.start()
        return self

    def _run(self):
        try:
            deadline = self.started + self.duration
            while not self.stop.is_set() and time.monotonic() < deadline:
                if self.rate:
                    burst = max(self.chunk, int(self.rate*.25))
                    scheduled = len(self.expected) if self.pattern != 'burst' else (len(self.expected)//burst)*burst
                    due = self.started + scheduled / self.rate
                    if self.stop.wait(max(0, min(due, deadline)-time.monotonic())):
                        break
                    if time.monotonic() >= deadline:
                        break
                    size = min(self.chunk, max(0, int(self.duration*self.rate)-len(self.expected)))
                    if self.pattern == 'burst':
                        size = min(size, burst-len(self.expected)%burst)
                    if not size:
                        break
                else:
                    size = self.chunk
                data = self.rng.randbytes(size)
                began = time.monotonic()
                try:
                    n = self.port.write(data)
                except BaseException:
                    self.unknown_accepted_bytes = True
                    raise
                ended = time.monotonic()
                self.latencies.append(ended-began)
                if not isinstance(n, int) or not 0 <= n <= size:
                    self.unknown_accepted_bytes = True
                    raise OSError('invalid fixture write count')
                self.expected.extend(data[:n])
                self.events.append((ended-self.started, len(self.expected)))
                if n != size:
                    raise OSError('fixture short write; no retry')
        except BaseException as exc:
            self.error = repr(exc)
        finally:
            self.finished = time.monotonic()
            self.done.set()

    def join(self):
        self.thread.join()
        if self.error:
            raise OSError(self.error + '; accepted_bytes_unknown=' + str(self.unknown_accepted_bytes))

    def metrics(self):
        # Exclude initial buffering and final submitted tail from steady rate.
        interior = [(t, n) for t, n in self.events if 1 <= t <= self.duration-1]
        steady = ((interior[-1][1]-interior[0][1])/(interior[-1][0]-interior[0][0])
                  if len(interior)>1 else None)
        return dict(chunk_bytes=self.chunk, target_Bps=self.rate, accepted_bytes=len(self.expected),
                    write_span_s=round(self.finished-self.started, 6),
                    accepted_Bps=round(len(self.expected)/(self.finished-self.started), 2),
                    steady_accepted_Bps=round(steady, 2) if steady is not None else None,
                    accepted_bytes_unknown=self.unknown_accepted_bytes, error=self.error,
                    write_events=self.events)
