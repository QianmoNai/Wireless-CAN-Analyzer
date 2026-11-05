"""Offline UI acceptance sequence; never opens a hardware transport."""
import json
from PySide6.QtCore import QTimer
from .protocol import packet, can_command
from . import __version__


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
        JSON.stringify({cards:document.querySelectorAll('#desktopMotors button').length,bridge:!!window.CanAndroid,version:window.CAN_SCOPE_VERSION,versionLabel:document.querySelector('.desktop-title').textContent,send:!!document.getElementById('sendOnce')});
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
        const sample=$('detailSampleHz').closest('.panel'),boxes=[sample.querySelector('.query-frequency'),$('detailSampleSave'),$('detailCanRate').parentElement,$('detailAppRate').parentElement].map(e=>e.getBoundingClientRect());
        JSON.stringify({detail:!!detailMotor,ready:detailReady,samples:detailSamples.length,connectionHidden:!$('connectionMode').getClientRects().length,toolsHidden:!document.querySelector('.desktop-bar').getClientRects().length,sampleCompact:sample.getBoundingClientRect().height<180&&boxes.every((r,i)=>r.width>0&&(!i||boxes[i-1].right<=r.left))});
        """.replace('SAMPLE',json.dumps(sample.hex()))
        def done(value):
            checks['detail']=json.loads(value or '{}')
            QTimer.singleShot(600, fullscreen)
        js(code,done)
    def fullscreen():
        window.showMaximized()
        def enter():
            js("openDetailChart(detailSeries[0].key);true;",lambda _:QTimer.singleShot(300,leave))
        def leave():
            checks['native_fullscreen']=window.isFullScreen()
            js("closeDetailChartFullscreen();true;",lambda _:QTimer.singleShot(300,restored))
        def restored():
            checks['maximized_restored']=window.isMaximized() and not window.isFullScreen()
            window.showNormal()
            QTimer.singleShot(300,live)
        QTimer.singleShot(300,enter)
    def live():
        capture('-detail')
        def done(value):
            checks['live']=json.loads(value or '{}')
            QTimer.singleShot(600, settings)
        js("showTab('live');document.getElementById('sendOnce').scrollIntoView();JSON.stringify({visible:!document.getElementById('view-live').hidden,send:document.getElementById('sendOnce').textContent})",done)
    def settings():
        capture('-live')
        js("showTab('settings');document.getElementById('view-settings').scrollIntoView();true",lambda _:QTimer.singleShot(600,uart))
    def uart():
        capture('-settings')
        code = """
        onNative('packets',{items:[STATUS,RX],dropped:0});
        document.getElementById('view-uart').scrollIntoView();
        true;
        """.replace('STATUS', json.dumps(packet(21,a=1,x=115200,y=6).hex()))
        rx = bytes([1,19,6,0,0,255,65,228,184,173]) + bytes(6) + (1).to_bytes(4,'little')
        js(code.replace('RX',json.dumps(rx.hex())),lambda _:QTimer.singleShot(300,uart_check))
    def uart_check():
        code = """
        JSON.stringify({visible:!$('view-uart').hidden,canHidden:$('view-live').hidden,
         motorsHidden:document.querySelector('[data-tab="motor"]').hidden,
         binary:$('uartLog').textContent,sendEnabled:!$('uartSend').disabled,
         bitrateDisabled:$('saveBitrate').disabled,csvDisabled:$('desktopCsv').disabled});
        """
        def done(value):
            checks['uart']=json.loads(value or '{}')
            capture('-uart')
            js("$('uartDisplay').value='text';$('uartDisplay').onchange();JSON.stringify({text:$('uartLog').textContent})",uart_text)
        js(code,done)
    def uart_text(value):
        checks['uart_text']=json.loads(value or '{}').get('text','')
        js("onNative('packets',{items:[STATUS],dropped:0});JSON.stringify({can:!window.uartMode,motorsVisible:!document.querySelector('[data-tab=\"motor\"]').hidden})".replace('STATUS',json.dumps(packet(21,x=115200).hex())),uart_restored)
    def uart_restored(value):
        checks['uart_restored']=json.loads(value or '{}')
        js("onNative('networkMode',{mode:'usb',busy:false});onNative('device',{address:'usb:TEST',name:'ESP32 USB',transport:'usb',rssi:0});JSON.stringify({mode:$('connectionMode').value,badge:$('transportBadge').textContent,manualHidden:$('lanManual').hidden,port:$('deviceList').textContent.includes('usb:TEST')})", usb_checked)
    def usb_checked(value):
        checks['usb']=json.loads(value or '{}')
        code = '''(() => {
            const original=invoke,calls=[];invoke=(name,...args)=>{if(name==='scan')calls.push(args);else original(name,...args)};
            try{
                const option=$('desktopAllSerial'),label=option.closest('label');
                option.checked=false;scan();
                const visible=!label.hidden,title=$('deviceModal').querySelector('h2').textContent;
                option.checked=true;option.onchange();
                onNative('networkMode',{mode:'wifi',busy:false});scan();
                return JSON.stringify({visible,title,wifiHidden:label.hidden,wifiTitle:$('deviceModal').querySelector('h2').textContent,calls});
            }finally{invoke=original;$('desktopAllSerial').checked=false;onNative('networkMode',{mode:'usb',busy:false});}
        })()'''
        js(code,usb_chooser)
    def usb_chooser(value):
        checks['usb_chooser']=json.loads(value or '{}')
        manual_results()
    def manual_results():
        code = '''(() => {
            const results=$('sendResult'),emit=(token,status)=>onNative('manualResult',{token,status});
            emit(41,'等待 CAN 发送结果');const waiting=results.firstChild;
            emit(41,'CAN 发送成功（已收到 ACK）');
            const single=results.children.length===1&&results.firstChild===waiting&&!results.textContent.includes('等待 CAN 发送结果');
            emit(42,'等待 CAN 发送结果');emit(43,'等待 CAN 发送结果');
            emit(43,'等待超时；发送结果未知，不自动重发');
            emit(42,'CAN 仲裁丢失；本次未发送成功，未自动重发');
            const states=Object.fromEntries(Array.from(results.children,e=>[e.dataset.token,e.dataset.state]));
            for(let token=100;token<150;token++){emit(token,'等待 CAN 发送结果');emit(token,'CAN 发送成功（已收到 ACK）');}
            const bounded=results.children.length===40&&new Set(Array.from(results.children,e=>e.dataset.token)).size===40;
            emit(41,'等待 CAN 发送结果');emit(41,'CAN 发送成功（已收到 ACK）');
            return JSON.stringify({single,states,bounded,reused:results.querySelectorAll('[data-token="41"]').length===1&&results.firstChild.dataset.state==='success',count:results.children.length});
        })()'''
        def done(value):
            checks['manual_results']=json.loads(value or '{}')
            isolation()
        js(code,done)
    def isolation():
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
        ok=(not window.js_errors and checks.get('isolated') and checks.get('overview',{}).get('cards')==1 and checks.get('detail',{}).get('samples')==50 and checks.get('detail',{}).get('connectionHidden') and checks.get('detail',{}).get('toolsHidden') and checks.get('detail',{}).get('sampleCompact') and checks.get('live',{}).get('visible'))
        ok=ok and checks.get('overview',{}).get('version')==__version__ and ('Desktop '+__version__+' ·') in checks.get('overview',{}).get('versionLabel','')
        ok=ok and checks.get('native_fullscreen') and checks.get('maximized_restored')
        uart=checks.get('uart',{})
        ok=ok and all(uart.get(k) for k in ('visible','canHidden','motorsHidden','sendEnabled','bitrateDisabled','csvDisabled')) and uart.get('binary')=='00 FF 41 E4 B8 AD' and '中' in checks.get('uart_text','') and all(checks.get('uart_restored',{}).get(k) for k in ('can','motorsVisible'))
        usb=checks.get('usb',{})
        ok=ok and usb.get('mode')=='usb' and usb.get('badge')=='USB直连' and usb.get('manualHidden') and usb.get('port')
        chooser=checks.get('usb_chooser',{})
        ok=ok and chooser.get('visible') and chooser.get('title')=='选择 USB 串口' and chooser.get('wifiHidden') and chooser.get('wifiTitle')=='局域网设备' and chooser.get('calls')==[[False],[True],[]]
        manual=checks.get('manual_results',{})
        ok=ok and manual.get('single') and manual.get('bounded') and manual.get('reused') and manual.get('count')==40 and manual.get('states')=={'41':'success','42':'failed','43':'unknown'}
        print("Smoke checks completed", flush=True)
        # Leave the JavaScript result callback before tearing down its page.
        def quit_test():
            window.close()
            app.exit(0 if ok else 1)
        QTimer.singleShot(0, quit_test)
    QTimer.singleShot(60000, lambda: failure('UI test timed out'))
    QTimer.singleShot(2500,begin)
