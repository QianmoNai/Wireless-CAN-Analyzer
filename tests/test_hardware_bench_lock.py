"""Process-level exclusion for the shared physical fixture, without hardware."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from hardware_bench_lock import hardware_bench_lock

with tempfile.TemporaryDirectory() as directory:
    lock=Path(directory)/'bench.lock'
    code='import sys;sys.path.insert(0,sys.argv[1]);from hardware_bench_lock import hardware_bench_lock\nwith hardware_bench_lock(sys.argv[2]):pass'
    command=[sys.executable,'-c',code,str(ROOT/'scripts'),str(lock)]
    with hardware_bench_lock(lock):
        attempt=subprocess.run(command,capture_output=True,text=True)
        assert attempt.returncode and 'already owned' in attempt.stderr
    assert subprocess.run(command,capture_output=True).returncode==0
    try:
        with hardware_bench_lock(lock):
            raise ValueError('test exception')
    except ValueError:
        pass
    with hardware_bench_lock(lock):
        pass
print('PASS: shared bench excludes a conflicting process and releases on normal/exception exit; no hardware')
