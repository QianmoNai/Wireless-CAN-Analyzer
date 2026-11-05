"""Exclusive owner for this shared physical CAN/phone/GUI acceptance bench."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile


@contextmanager
def hardware_bench_lock(path=None):
    path=Path(path) if path is not None else Path(tempfile.gettempdir())/f'can-scope-hardware-{getattr(os,"getuid",lambda:0)()}.lock'
    descriptor=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    locked=False
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (hasattr(os,'getuid') and info.st_uid!=os.getuid()):
            raise RuntimeError('Unsafe hardware bench lock file')
        if os.name=='nt':
            import msvcrt
            if not info.st_size:
                os.write(descriptor,b'0')
            os.lseek(descriptor,0,os.SEEK_SET)
            try:
                msvcrt.locking(descriptor,msvcrt.LK_NBLCK,1)
            except OSError as error:
                raise RuntimeError('Hardware bench is already owned by another test') from error
        else:
            import fcntl
            try:
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('Hardware bench is already owned by another test') from error
        locked=True
        yield
    finally:
        if locked:
            if os.name=='nt':
                os.lseek(descriptor,0,os.SEEK_SET)
                msvcrt.locking(descriptor,msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(descriptor,fcntl.LOCK_UN)
        os.close(descriptor)
