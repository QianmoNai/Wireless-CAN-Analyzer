"""Desktop connection cache, shared with the terminal provisioning tool."""
import json
import os
from pathlib import Path
import tempfile


def connection_folder():
    # QtCore only: no window, WebEngine, or desktop transport is started.
    from PySide6.QtCore import QCoreApplication, QStandardPaths
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
        app.setOrganizationName('Qianmo')
        app.setApplicationName('CAN Scope Desktop')
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))


def load_connection(path):
    """Missing files are new channels; malformed existing files are not overwritten."""
    try:
        value = path.read_text('utf8')
    except FileNotFoundError:
        return {}
    value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('连接缓存格式无效')
    return value


def save_connection(path, value):
    """Atomically replace the cache with a private file, as in the Qt runtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.connection-')
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            if os.name != 'nt':
                os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
