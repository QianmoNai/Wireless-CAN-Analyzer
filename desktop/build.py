"""Build on each target OS; PyInstaller cannot cross-compile Windows on Linux."""
from pathlib import Path
import subprocess
import sys
import shutil
import tarfile
import hashlib
from can_scope import __version__

ROOT = Path(__file__).resolve().parent
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
    '--name', 'CAN-Scope', '--distpath', str(ROOT/'dist'), '--workpath', str(ROOT/'build'),
    '--specpath', str(ROOT/'build'), '--paths', str(ROOT),
    '--add-data', str(ROOT.parent/'android/app/src/main/assets') + ':assets',
    '--add-data', str(ROOT/'can_scope/desktop.js') + ':can_scope',
    '--collect-submodules', 'bleak', str(ROOT/'main.py'),
], check=True, cwd=ROOT)
output = ROOT/'dist/CAN-Scope'
if sys.platform == 'linux':
    (output/'CAN-Scope').chmod(0o755)
    for helper in output.rglob('QtWebEngineProcess'):
        helper.chmod(0o755)
shutil.copy(ROOT.parent/'LICENSE', output/'LICENSE')
(output/'README.md').write_text((ROOT/'README.md').read_text('utf-8').replace('../docs/UART.md','UART.md'), encoding='utf-8')
shutil.copy(ROOT.parent/'docs/UART.md', output/'UART.md')
# Archive preserves execute bits even on filesystems that ignore chmod.
if sys.platform == 'linux':
    archive = ROOT/f'dist/CAN-Scope-Desktop-{__version__}-Linux-x64.tar.gz'
    def permissions(info):
        info.mode = 0o755 if info.isdir() or Path(info.name).name in ('CAN-Scope', 'QtWebEngineProcess') else 0o644
        return info
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(output, arcname='CAN-Scope', filter=permissions)
else:
    archive = Path(shutil.make_archive(str(ROOT/f'dist/CAN-Scope-Desktop-{__version__}-Windows-x64'), 'zip', ROOT/'dist', 'CAN-Scope'))
archive.with_suffix(archive.suffix+'.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n', encoding='ascii')
print(archive)
