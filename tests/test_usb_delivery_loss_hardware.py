"""A stop, unhealthy fixture or wrong USB identity must never mutate the bench."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
usb=types.ModuleType('usb');core=types.ModuleType('usb.core');core.USBTimeoutError=TimeoutError;usb.core=core
with patch.dict(sys.modules,{'usb':usb,'usb.core':core}):
    spec=importlib.util.spec_from_file_location('delivery_loss_hardware',root/'scripts/test-usb-delivery-loss-hardware.py')
    tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)

with tempfile.TemporaryDirectory(prefix='usb-delivery-preflight-')as d:
    folder=Path(d)
    for case in ['stop','health','identity']:
        writes=[];reads=[]
        class Fixture:
            symbols={'dual_load_bench':0x20000000}
            image_sha256='test-only'
            def __init__(self,elf):pass
            def health(self):return dict(fatal_error=int(case=='health'))
            def read(self,address):return [0]
            def quiet(self,value):writes.append(('periodic',value))
        class Physical:
            def __init__(self,port):reads.append('USB identity')
            def identity(self):return bytes.fromhex('333333333333')
            def close(self):pass
        class Raw:
            def __init__(self,*args):raise AssertionError('Raw USB must not be claimed')
        async def prepare(*args,**kwargs):writes.append(('USB setting',args))
        def module(name,file):
            return types.SimpleNamespace(Fixture=Fixture)if file=='test-dual-can-hardware.py'else types.SimpleNamespace(RawCdc=Raw)
        stop=folder/'STOP';stop.unlink(missing_ok=True)
        if case=='stop':stop.touch()
        output=folder/(case+'.json')
        argv=['test','--fixture','stm32h723-dual','--elf',str(folder/'mock.elf'),'--port','identity-test-only','--stop-file',str(stop),'--output',str(output)]
        with patch.object(tool,'module',module),patch.object(tool,'hardware_bench_lock',contextlib.nullcontext),patch.object(tool,'LocalUsb',Physical),patch.object(tool,'prepare_usb',prepare),patch.object(tool,'load_connection',return_value={'id':'111111111111'}),patch.object(tool,'connection_folder',return_value=folder),patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):
            assert tool.main()==1
        result=json.loads(output.read_text());assert not result['ok']and not writes,(case,writes)
        assert bool(reads)==(case=='identity'),(case,reads)
print('PASS: USB delivery fault-test stop/health/identity preflight performs no settings, source or CDC mutations')
