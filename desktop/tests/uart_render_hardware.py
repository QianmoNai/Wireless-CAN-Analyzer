"""Opt-in real UART/Qt render timing. Test-only JS hooks, no CAN TX.

Measures the UART timer callback including synchronous layout, not GPU paint.
Uses the real visible page with the latest 64 KiB HEX log and a dedicated writer.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from uart_stress_fixture import FixtureWriter
from can_scope.usb_transport import prepare_usb, open_port

HOOK = r"""
window.rxTiming={render:[],gaps:[],bytes:0,dirty:false,hooked:false,longtasks:[],nodesMax:0,heapMax:0};
new PerformanceObserver(list=>rxTiming.longtasks.push(...list.getEntries().map(e=>e.duration))).observe({entryTypes:['longtask']});
const nativeInterval=window.setInterval;
window.setInterval=function(fn,ms,...args){
 if(typeof fn==='function'&&String(fn).includes('lastStatusAt')&&String(fn).includes('render()')){
  rxTiming.hooked=true;let previous=null;
  return nativeInterval.call(window,()=>{
   const measure=rxTiming.dirty&&!document.hidden&&!document.getElementById('view-uart').hidden;
   const t=performance.now();fn();
   if(measure){rxTiming.render.push(performance.now()-t);
    rxTiming.nodesMax=Math.max(rxTiming.nodesMax,document.getElementById('uartLog').querySelectorAll('*').length);
    rxTiming.heapMax=Math.max(rxTiming.heapMax,performance.memory?.usedJSHeapSize||0);if(previous!==null)rxTiming.gaps.push(t-previous);previous=t;rxTiming.dirty=false;}
  },ms,...args);
 }
 return nativeInterval.call(window,fn,ms,...args);
};
document.addEventListener('DOMContentLoaded',()=>{
 const original=window.uartBatch;
 window.uartBatch=row=>{rxTiming.bytes+=row.uart.length/2;rxTiming.dirty=true;return original(row);};
});
"""


def distribution(values):
    values=sorted(values)
    return {name:round(values[min(len(values)-1,int((len(values)-1)*q))],3) if values else None
            for name,q in [('p95',.95),('max',1)]}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--usb',default='/dev/ttyACM0');ap.add_argument('--peer',default='/dev/ttyACM1')
    ap.add_argument('--baud',type=int,choices=[115200,921600],default=921600)
    ap.add_argument('--duration',type=float,default=20);ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args()
    if args.duration<=0:ap.error('duration must be positive')
    report=dict(uid=os.getuid(),baud=args.baud,duration_s=args.duration,
                scope='real visible Qt UART HEX; timer callback including synchronous layout, excluding paint')
    report['bootstrap']=asyncio.run(prepare_usb(args.usb,data=1,baud=args.baud))
    # Import configures the same software renderer as the desktop application.
    from can_scope import app as gui
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEngineScript
    class TimedPage(gui.Page):
        def __init__(self,*a,**kw):
            super().__init__(*a,**kw)
            self.scripts().insert(gui.script('test-uart-timing',HOOK,QWebEngineScript.InjectionPoint.DocumentCreation))
    gui.Page=TimedPage
    received=bytearray();statuses=[];errors=[];ages=[]
    class TestWindow(gui.Window):
        def deliver(self,slot,kind,payload):
            if kind=='packets':
                for item in json.loads(payload).get('items',[]):
                    if 'uart' in item:
                        received.extend(bytes.fromhex(item['uart']));ages.append(item['age'])
                    elif bytes.fromhex(item['hex'])[1]==21:
                        statuses.append(item['hex'])
            elif kind=='error':errors.append(json.loads(payload))
            super().deliver(slot,kind,payload)
    app=QApplication(sys.argv[:1]);temporary=tempfile.TemporaryDirectory(prefix='uart-render-')
    window=TestWindow(Path(temporary.name));window.show()
    peer=None;sender=None;finished=False;started=None;cpu=None;rss_max=0
    def js(code,done=lambda _:None):window.views[1].page().runJavaScript(code,done)
    def save():
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    def finish(error=None):
        nonlocal finished
        if finished:return
        finished=True
        if sender is not None:
            sender.stop.set();sender.join()
        if error:report['error']=str(error)
        report.update(js_errors=window.js_errors,errors=errors,type21=statuses)
        if peer:peer.baudrate=115200;peer.close()
        report['ok']=not(error or errors or window.js_errors) and report.get('strict_rx_equal',False) and report.get('ui_tail_equal',False) and report.get('hooked',False)
        save();window.close();app.exit(0 if report['ok'] else 1)
    def wait(code,predicate,done,deadline=None):
        deadline=time.monotonic()+10 if deadline is None else deadline
        def checked(value):
            if finished:return
            if predicate(value):done(value)
            elif time.monotonic()>deadline:finish('GUI readiness timeout: '+code)
            else:QTimer.singleShot(100,lambda:wait(code,predicate,done,deadline))
        js(code,checked)
    def connect():
        window.runtime.call(1,'connect',['usb:'+args.usb])
        wait(f"connected&&window.uartMode&&$('uartStats').textContent.includes('{args.baud}')",bool,begin)
    def ready():
        if 1 not in window.ready:QTimer.singleShot(100,ready)
        else:connect()
    def begin(_):
        nonlocal peer,sender,started,cpu
        peer=open_port(args.peer);peer.baudrate=args.baud;peer.write_timeout=2
        received.clear();report['type21_before']=statuses[-1] if statuses else None
        def launch(_):
            nonlocal sender,started,cpu
            started=time.monotonic();cpu=time.process_time()
            sender=FixtureWriter(peer,args.duration,args.baud/10*.98,4096).start(started)
            QTimer.singleShot(int((args.duration+2)*1000),collect)
            sample()
        js("showTab('uart');$('uartDisplay').value='hex';$('uartClear').click();rxTiming.render=[];rxTiming.gaps=[];rxTiming.bytes=0;true",launch)
    def sample():
        nonlocal rss_max
        if finished:return
        rss_max=max(rss_max,int(next(x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('VmRSS:')))*1024)
        QTimer.singleShot(250,sample)
    def collect():
        sender.join()
        report.update(input_bytes=len(sender.expected),output_bytes=len(received),
                      input_Bps=round(len(sender.expected)/args.duration,2),
                      output_Bps=round(len(received)/args.duration,2),fixture=sender.metrics(),
                      strict_rx_equal=sender.expected==received,
                      input_sha256=hashlib.sha256(sender.expected).hexdigest(),
                      output_sha256=hashlib.sha256(received).hexdigest(),
                      cpu_percent=round((time.process_time()-cpu)/(time.monotonic()-started)*100,2),
                      rss_max_bytes=rss_max,queue_age_at_qt_slot_ms=distribution(ages))
        def result(value):
            data=json.loads(value)
            report.update(hooked=data['hooked'],render_count=len(data['render']),
                          render_ms=distribution(data['render']),render_interval_ms=distribution(data['gaps']),
                          page_received_bytes=data['bytes'],ui_tail_equal=data['tail']==bytes(sender.expected[-65536:]).hex().upper(),
                          document_hidden=data['hidden'],ui_loss=data['loss'],
                          longtasks_ms=distribution(data['longtasks']),longtask_count=len(data['longtasks']),
                          dom_nodes_max=data['nodesMax'],js_heap_max_bytes=data['heapMax'])
            window.grab().save(str(args.output.with_suffix('.png')))
            finish()
        js("JSON.stringify({...rxTiming,tail:$('uartLog').textContent.replace(/\\s/g,''),hidden:document.hidden,loss:$('uartLoss').textContent})",result)
    QTimer.singleShot(100,ready)
    QTimer.singleShot(int((args.duration+35)*1000),lambda:finish('overall GUI timeout'))
    try:
        result=app.exec()
    finally:
        if sender is not None:sender.stop.set();sender.join()
        if peer:peer.baudrate=115200;peer.close()
        from shiboken6 import delete,isValid
        for view in window.views.values():
            if isValid(view.page()):delete(view.page())
        for profile in window.profiles:
            if isValid(profile):delete(profile)
        temporary.cleanup()
        report['restored_settings']=asyncio.run(prepare_usb(args.usb,data=1,baud=115200))
        save()
    return result


if __name__=='__main__':raise SystemExit(main())
