"""Offline acceptance checks for the opt-in hardware bench, without hardware."""
import asyncio
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


async def passive_observation():
    spec = importlib.util.spec_from_file_location('hardware_observer', ROOT/'scripts/test-can-hardware.py')
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    clock = 0.0
    kinds = bench.Counter({1: 20})
    statuses = [dict(rx=20)]
    class Writer:
        def get_extra_info(self, name):
            assert name == 'socket'
            return object()
    class Link:
        writer = Writer()
        tasks = []
        async def heartbeat(self):
            await asyncio.Event().wait()
        async def write(self, _):
            raise AssertionError('Passive observer sent a command')
    link = Link()
    heartbeat = asyncio.create_task(link.heartbeat())
    link.tasks = [heartbeat]
    await asyncio.sleep(0)
    async def sleep(seconds):
        nonlocal clock
        clock += seconds
        kinds[1] += 10
        statuses.append(dict(rx=kinds[1]))
        await asyncio.sleep(0)
    bench.time = SimpleNamespace(monotonic=lambda: clock)
    bench.asyncio = SimpleNamespace(sleep=sleep, gather=asyncio.gather)
    bench.read_tcp_info = lambda _: dict(total_retrans=3)
    result = await bench.observe_wifi_failure(link, kinds, statuses, 5)
    assert heartbeat.cancelled() and result['heartbeat_tasks_stopped'] == 1
    rows = result['samples']
    assert len(rows) == 11 and rows[0]['additional_rx'] == 0 and rows[-1]['additional_rx'] == 100
    assert rows[-1]['at_s'] == 5 and rows[-1]['tcp']['total_retrans'] == 3
    statuses[0]['rx'] = 999
    assert rows[0]['last_status']['rx'] == 20
    print('PASS: passive Wi-Fi recovery observation records continued RX without writes')


async def scenario(directory, case, poll_hz=0):
    spec = importlib.util.spec_from_file_location('hardware_bench', ROOT/'scripts/test-can-hardware.py')
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    clock = 0.0
    transport = None

    class Link:
        def __init__(self, received, disconnected):
            nonlocal transport
            transport = self
            self.received, self.rx, self.queries = received, 0, set()
            self.last_status = -1

        async def connect(self, _):
            pass

        async def close(self):
            pass

        async def write(self, p):
            identifier, hz = bench.u32(p, 4), bench.u32(p, 8)
            if hz:
                self.queries.add(identifier)
            else:
                self.queries.discard(identifier)
            self.received(bench.packet(8, a=0 if hz else 1, token=bench.u32(p, 16)))

        def emit(self):
            streams = [(0x201, 0), (0x202, 0), (0x205, 0)]
            streams += [(0x60, subtype) for subtype in range(1, 5)
                        if not (case == 'missing_imu' and subtype == 4)]
            if case != 'missing_dm':
                streams += [(0x10+identifier, 0) for identifier in self.queries]
            if case == 'stopped' and clock >= 2:
                streams = []
            for identifier, subtype in streams:
                self.rx += 1
                self.received(bench.packet(1, x=identifier, y=subtype, token=int(clock*1000)))
            if int(clock) != self.last_status:
                self.last_status = int(clock)
                drops = int(case == 'dropped' and clock >= 6)
                self.received(bench.packet(4, x=self.rx, token=drops))

    async def sleep(seconds):
        nonlocal clock
        clock += seconds
        transport.emit()
        await asyncio.sleep(0)

    async def prepare(*_, **__):
        pass

    bench.time = SimpleNamespace(monotonic=lambda: clock, process_time=lambda: 0)
    bench.asyncio = SimpleNamespace(Event=asyncio.Event, wait_for=asyncio.wait_for,
                                   get_running_loop=asyncio.get_running_loop, sleep=sleep)
    bench.select = bench.prepare_usb = prepare
    bench.UsbTransport = Link
    bench.connection_folder = lambda: directory
    bench.load_connection = lambda _: {}
    options = SimpleNamespace(port='fake', channel=1, modes=['usb'], duration=12,
                              poll_hz=poll_hz, ble_diagnostics=False,
                              output=directory/(case+str(poll_hz)+'.json'))
    failure = None
    with redirect_stdout(io.StringIO()):
        try:
            result = await bench.main(options)
        except RuntimeError as exc:
            failure = exc
            result = False
    report = json.loads(options.output.read_text())
    assert report['restored_usb_can'] is True
    if case == 'healthy':
        assert result and report['ok'] and failure is None
    elif case == 'dropped':
        assert not result and not report['ok'] and failure is None
        assert report['cases'][0]['device_delta']['drops'] == 1
    else:
        assert failure and not report['ok']
        errors = report['cases'][0]['errors']
        missing = next(e['streams'] for e in errors if e['event'] == 'fixture_stalled')
        expected = {'missing_imu': ['060/4'], 'missing_dm': ['011', '012', '013', '014']}
        if case in expected:
            assert missing == expected[case], missing
    print('PASS: hardware bench '+case+f' poll_hz={poll_hz}')


with tempfile.TemporaryDirectory(prefix='can-bench-acceptance-') as directory:
    asyncio.run(passive_observation())
    for case, hz in (('healthy', 0), ('healthy', 100), ('stopped', 0),
                     ('missing_imu', 0), ('missing_dm', 100), ('dropped', 0)):
        asyncio.run(scenario(Path(directory), case, hz))
