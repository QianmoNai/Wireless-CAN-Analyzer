"""Offline real WebChannel export of a full synthetic CAN history; no hardware."""
import argparse
import csv
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--package', type=Path, default=ROOT/'desktop', help='optional isolated source package')
parser.add_argument('--output', type=Path, required=True)
options = parser.parse_args()
sys.path.insert(0, str(options.package))
import can_scope.app as shell

shell.ASSETS = ROOT/'android/app/src/main/assets'
app = shell.QApplication([])
app.setOrganizationName('Qianmo'); app.setApplicationName('CAN Scope Desktop')
profile = tempfile.TemporaryDirectory(prefix='can-csv-offline-')
folder = Path(profile.name)
window = shell.Window(folder); window.show()
target = folder/'history.csv'
errors = []; dialogs = []; finished = False
report = {'scope': 'offline real Qt WebChannel; synthetic 20000 CAN frames; no hardware'}
original = window.deliver


def deliver(slot, kind, payload):
    if kind == 'error': errors.append(json.loads(payload)['message'])
    original(slot, kind, payload)


def save_dialog(*args, **kwargs):
    dialogs.append(True)
    return str(target), '*.csv'


window.runtime.message.disconnect(original)
window.runtime.message.connect(deliver)
window.deliver = deliver
shell.QFileDialog.getSaveFileName = save_dialog


def begin():
    if 1 not in window.ready:
        shell.QTimer.singleShot(100, begin); return
    window.views[1].page().runJavaScript("""(() => {
        const frame={kind:1,id:0x1fffffff,flags:1,ext:true,rtr:false,dlc:8,
            data:Array(8).fill(255),receivedAt:Number.MAX_SAFE_INTEGER,seq:0xffffffff};
        const payload=JSON.stringify(Array(20000).fill(frame));
        invoke('exportCsv',payload);
        return JSON.stringify([payload]).length;
    })()""", lambda length: requested(length))


def requested(length):
    report['payload_chars'] = length
    check_file()


def check_file():
    if errors:
        finish(); return
    if not target.exists():
        shell.QTimer.singleShot(100, check_file); return
    with target.open(encoding='utf-8-sig', newline='') as stream:
        exported = list(csv.reader(stream))
    report.update(rows=len(exported)-1, header=exported[0], last=exported[-1],
                  bom=target.read_bytes().startswith(bytes([239, 187, 191])))
    report['ok'] = (len(exported) == 20001 and len(dialogs) == 1
                    and report['bom'] and report['payload_chars'] > 2_000_000
                    and exported[-1] == ['1', '0x1fffffff', '1', '0', '8',
                        'FF FF FF FF FF FF FF FF', str(2**53-1), str(0xffffffff)])
    finish()


def finish():
    global finished
    if finished: return
    finished = True
    report.update(errors=errors, js_errors=window.js_errors, dialogs=len(dialogs))
    report['ok'] = bool(report.get('ok') and not errors and not window.js_errors)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    window.close(); app.quit()


shell.QTimer.singleShot(200, begin); shell.QTimer.singleShot(20000, finish)
app.exec()
for view in window.views.values():
    if shell.isValid(view.page()): shell.delete(view.page())
for item in window.profiles:
    if shell.isValid(item): shell.delete(item)
profile.cleanup()
print(json.dumps(report, ensure_ascii=False))
sys.exit(0 if report['ok'] else 1)
