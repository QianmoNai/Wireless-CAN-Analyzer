"""Offline real Qt keyboard/toolbar zoom check; opens no hardware transport."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_scope.app import QApplication, Window, QTimer, delete, isValid
from can_scope.protocol import packet
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
options = parser.parse_args()
options.output.parent.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setOrganizationName('Qianmo'); app.setApplicationName('CAN Scope Desktop')
profile = tempfile.TemporaryDirectory(prefix='can-zoom-offline-')
window = Window(Path(profile.name)); window.add_channel(); window.show()
report = {'scope': 'offline real Qt shortcuts, synthetic samples; no hardware', 'checks': {}}
finished = False

sample = bytearray(packet(11, b=8, x=1001, token=1000))
sample[8:16] = bytes([0x10, 0, 0, 0x60, 0, 0x20, 42, 0])
seed = """
commitWatch({profile:'c620',id:1,name:'离线缩放测试'});
onNative('state',{value:'ready',name:'模拟界面',message:'离线界面测试'});
dashboardOpen(JSON.stringify(watchedMotors[0].config));
onNative('detailSupport',{supported:true,maxHz:1000});
onNative('telemetryMode',{mode:2,hz:100});
for(let n=0;n<50;n++){let a=parsePacket(SAMPLE);put32(a,16,1000+n*10);onNative('motorSample',{key:JSON.stringify(watchedMotors[0].config),hex:hexOf(a),age:0});}
true;
""".replace('SAMPLE', json.dumps(sample.hex()))


def js(code, callback=lambda _: None):
    window.views[1].page().runJavaScript(code, callback)


def begin():
    if len(window.ready) < 2:
        QTimer.singleShot(100, begin); return
    js(seed, lambda _: js('innerWidth', baseline))


def key(keycode, after):
    window.activateWindow()
    view = window.views[window.tabs.currentIndex()+1]
    view.setFocus()
    QTest.keyClick(view.focusProxy() or view, keycode, Qt.KeyboardModifier.ControlModifier)
    QTimer.singleShot(150, after)


def baseline(width):
    report['width_before'] = width
    key(Qt.Key.Key_Equal, enlarged)


def enlarged():
    report['checks']['keyboard_enlarged'] = abs(window.views[1].zoomFactor()-1.1) < .001
    js('innerWidth', lambda width: enlarged_width(width))


def enlarged_width(width):
    report['checks']['viewport_scaled'] = width < report['width_before']
    window.tabs.setCurrentIndex(1)
    report['checks']['second_channel_independent'] = window.views[2].zoomFactor() == 1 and window.zoom_action.text() == '100%'
    window.tabs.setCurrentIndex(0)
    report['checks']['first_channel_retained'] = window.zoom_action.text() == '110%'
    key(Qt.Key.Key_Minus, reduced)


def reduced():
    report['checks']['keyboard_reduced'] = abs(window.views[1].zoomFactor()-1) < .001
    for _ in range(20): window.adjust_zoom(1)
    report['checks']['upper_bound'] = window.views[1].zoomFactor() == 2
    for _ in range(20): window.adjust_zoom(-1)
    report['checks']['lower_bound'] = window.views[1].zoomFactor() == .75
    key(Qt.Key.Key_0, reset)


def reset():
    report['checks']['keyboard_reset'] = window.views[1].zoomFactor() == 1 and window.zoom_action.text() == '100%'
    window.set_active_zoom(1.5)
    window.zoom_action.trigger()
    report['checks']['toolbar_reset'] = window.views[1].zoomFactor() == 1
    js('JSON.stringify({count:detailSamples.length,ready:detailReady})', samples)


def samples(value):
    state = json.loads(value)
    report['checks']['samples_preserved'] = state['count'] == 50 and state['ready']
    finish()


def finish():
    global finished
    if finished: return
    finished = True
    report['js_errors'] = window.js_errors
    report['ok'] = len(report['checks']) == 10 and all(report['checks'].values()) and not window.js_errors
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    window.close(); app.quit()


QTimer.singleShot(200, begin); QTimer.singleShot(15000, finish)
app.exec()
for view in window.views.values():
    if isValid(view.page()): delete(view.page())
for item in window.profiles:
    if isValid(item): delete(item)
profile.cleanup()
print(json.dumps(report, ensure_ascii=False))
sys.exit(0 if report['ok'] else 1)
