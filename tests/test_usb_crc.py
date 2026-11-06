"""Check the production USB CRC against independent zlib across lengths and alignment."""
import ctypes
from pathlib import Path
import random
import subprocess
import tempfile
import zlib

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='usb-crc-reference-')as d:
    p=Path(d);(p/'check.c').write_text('#include "usb_protocol.h"\nuint32_t check(const uint8_t*p,unsigned n){return usb_crc(p,n);}\n')
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC','-I'+str(root/'firmware/main'),str(p/'check.c'),'-o',str(p/'check.so')],check=True)
    lib=ctypes.CDLL(str(p/'check.so'));lib.check.argtypes=[ctypes.c_void_p,ctypes.c_uint];lib.check.restype=ctypes.c_uint32
    rng=random.Random(20261106);cases=0
    for n in [*range(513),*([1024,2048,4096]*30),*([28]*2000)]:
        for alignment in range(8):
            raw=rng.randbytes(n+alignment);buffer=ctypes.create_string_buffer(raw)
            actual=lib.check(ctypes.addressof(buffer)+alignment,n)
            assert actual==zlib.crc32(raw[alignment:]),(n,alignment,actual)
            cases+=1
print(f'PASS: production USB IEEE CRC32 vs independent zlib, {cases} aligned/unaligned vectors')
