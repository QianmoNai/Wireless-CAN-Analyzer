"""Offline UI acceptance sequence; never opens a hardware transport."""
import json
from PySide6.QtCore import QTimer
from .protocol import packet, can_command


def run(app, window, output):
    checks = {}
    finished = False
    def js(source, callback=lambda _:None):
        window.views[1].page().runJavaScript(source, callback)
    def capture(suffix):
        window.grab().save(str(output.with_name(output.stem+suffix+'.png')))
    def failure(message):
        checks['failure'] = message
        finish()
    def begin():
        if 1 not in window.ready:
            QTimer.singleShot(200, begin)
            return
        window.add_channel()
        frame=bytearray(can_command(0x201,bytes([0x10,0,0,0x60,0,0x20,42,0])))
        frame[1]=1
        source="""
        commitWatch({profile:'c620',name:'底盘电机 · 演示',id:1,feedbackId:513});
        onNative('state',{value:'ready',name:'ESP32-CAN · 模拟设备',message:'离线界面测试 · 模拟数据'});
        onNative('packets',{items:[CONFIG,FRAME],dropped:0});publishDashboard();
        JSON.stringify({cards:document.querySelectorAll('#desktopMotors button').length,bridge:!!window.CanAndroid,send:!!document.getElementById('sendOnce')});
        """.replace('CONFIG',json.dumps(packet(5,b=0xc9,x=1000000,z=1000).hex())).replace('FRAME',json.dumps(frame.hex()))
        def done(value):
            checks['overview'] = json.loads(value or '{}')
            QTimer.singleShot(600, detail)
        js(source,done)
    def detail():
        capture('')
        # Click the actual desktop card to open shared charts.
        sample=bytearray(packet(11,b=8,x=1001,token=1000))
        sample[8:16]=bytes([0x10,0,0,0x60,0,0x20,42,0])
        code="""
        document.querySelector('#desktopMotors button').click();
        onNative('detailSupport',{supported:true,maxHz:1000,channel:'模拟设备'});
        onNative('telemetryMode',{mode:2,hz:100});
        for(let n=0;n<50;n++){let a=parsePacket(SAMPLE);put32(a,16,1000+n*10);a[8]=Math.floor(16+n/3);onNative('motorSample',{key:JSON.stringify(watchedMotors[0].config),hex:hexOf(a),age:0});}
        document.getElementById('detailPlots').scrollIntoView();
        JSON.stringify({detail:!!detailMotor,ready:detailReady,samples:detailSamples.length});
        """.replace('SAMPLE',json.dumps(sample.hex()))
        def done(value):
            checks['detail']=json.loads(value or '{}')
            QTimer.singleShot(600, live)
        js(code,done)
    def live():
        capture('-detail')
        def done(value):
            checks['live']=json.loads(value or '{}')
            QTimer.singleShot(600, settings)
        js("showTab('live');document.getElementById('sendOnce').scrollIntoView();JSON.stringify({visible:!document.getElementById('view-live').hidden,send:document.getElementById('sendOnce').textContent})",done)
    def settings():
        capture('-live')
        js("showTab('settings');document.getElementById('view-settings').scrollIntoView();true",lambda _:QTimer.singleShot(600,isolation))
    def isolation():
        capture('-settings')
        if 2 not in window.ready:
            failure('second channel did not load')
            return
        window.views[2].page().runJavaScript('watchedMotors.length',lambda count:isolated(count))
    def isolated(count):
        checks['isolated'] = count == 0
        finish()
    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        report=dict(ready=sorted(window.ready),js_errors=window.js_errors,checks=checks)
        output.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')
        ok=(not window.js_errors and checks.get('isolated') and checks.get('overview',{}).get('cards')==1 and checks.get('detail',{}).get('samples')==50 and checks.get('live',{}).get('visible'))
        print("Smoke checks completed", flush=True)
        # Leave the JavaScript result callback before tearing down its page.
        def quit_test():
            window.close()
            app.exit(0 if ok else 1)
        QTimer.singleShot(0, quit_test)
    QTimer.singleShot(60000, lambda: failure('UI test timed out'))
    QTimer.singleShot(2500,begin)
