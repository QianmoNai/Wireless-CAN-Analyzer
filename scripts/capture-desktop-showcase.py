#!/usr/bin/env python3
"""Capture actual Qt desktop UI with offline telemetry for the showcase."""
import sys,tempfile,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'desktop'))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from shiboken6 import delete,isValid
from can_scope.app import Window
out=ROOT/'dist/combined-showcase/pc-frames';out.mkdir(parents=True,exist_ok=True)
app=QApplication([]);app.setOrganizationName('Qianmo');app.setApplicationName('CAN Scope Showcase')
temp=tempfile.TemporaryDirectory(prefix='can-pc-video-',ignore_cleanup_errors=True)
window=Window(Path(temp.name),True);window.resize(1320,920);window.show()
state={'frame':0,'done':False}
def js(code,callback):window.views[1].page().runJavaScript(code,callback)
def finish(code=0):
 if state['done']:return
 state['done']=True
 (out.parent/'pc-capture.json').write_text(json.dumps({'frames':state['frame'],'js_errors':window.js_errors},ensure_ascii=False,indent=2),encoding='utf8')
 window.close();app.exit(code)
def begin():
 if 1 not in window.ready:QTimer.singleShot(200,begin);return
 window.add_channel()
 js("""window.CanAndroid=new Proxy({}, {get:()=>()=>{}});
 commitWatch({profile:'c620',name:'左轮电机',id:1,feedbackId:513});
 commitWatch({profile:'c620',name:'右轮电机',id:2,feedbackId:514});
 onNative('state',{value:'ready',name:'底盘 CAN',message:''});
 let cfg=new Uint8Array(20);cfg[0]=1;cfg[1]=5;cfg[3]=201;put32(cfg,4,1000000);put32(cfg,16,1000);processPacket(hexOf(cfg));
 true;""",lambda _:QTimer.singleShot(200,tick))
def tick():
 f=state['frame'];t=f/15
 if f>=360:QTimer.singleShot(0,lambda:finish(1 if window.js_errors else 0));return
 transition=''
 if f==120:transition="dashboardOpen(JSON.stringify(watchedMotors[0].config));onNative('detailSupport',{supported:true,maxHz:1000,channel:'底盘 CAN'});onNative('telemetryMode',{mode:2,hz:100});document.getElementById('detailPlots').scrollIntoView();"
 if f==240:transition="showTab('live');onNative('telemetryMode',{mode:0});window.scrollTo(0,0);"
 if f==285:transition="document.querySelector('.desktop-send').scrollIntoView({behavior:'smooth',block:'start'});$('sendId').value='123';$('sendData').value='01 02 03 04 05 06 07 08';"
 feed="""const t=TIME;const make=(kind,id,data)=>{const a=new Uint8Array(20);a[0]=1;a[1]=kind;a[3]=data.length;put32(a,4,id);a.set(data,8);return a};
 const angle=Math.round(4096+2200*Math.sin(t*2)),speed=Math.round(240+100*Math.sin(t*3));const bytes=[angle>>8,angle&255,speed>>8,speed&255,0,80,43,0];
 processPacket(hexOf(make(1,0x201,bytes)));processPacket(hexOf(make(1,0x202,[12,30,0,160,0,50,44,0])));publishDashboard();
 if(detailMotor){for(let n=0;n<5;n++){let tt=t+n/75,a=make(11,1001,bytes),v=Math.round(4096+2200*Math.sin(tt*2));a[8]=v>>8;a[9]=v&255;put32(a,16,Math.round(tt*1000));onNative('motorSample',{key:JSON.stringify(detailMotor.config),hex:hexOf(a),age:0})}}
 else{let a=make(14,700+Math.round(20*Math.sin(t)),[]);put32(a,8,100);put32(a,12,1000000);put32(a,16,1000000);onNative('busRates',{hex:hexOf(a)});a=make(13,80000,[]);a[2]=122;a[3]=4;put32(a,8,12000);put32(a,12,1000000);put32(a,16,1000000);onNative('busLoad',{hex:hexOf(a)});updateMonitor();renderFrames()};true;
 """.replace('TIME',str(t))
 js('(()=>{'+transition+feed+'})()',lambda _:QTimer.singleShot(60,capture))
def capture():
 f=state['frame'];window.grab().save(str(out/f'{f:04}.png'));state['frame']+=1
 if f%60==0:print('PC frame',f,flush=True)
 QTimer.singleShot(0,tick)
QTimer.singleShot(1500,begin);QTimer.singleShot(180000,lambda:finish(1))
code=app.exec()
for view in window.views.values():
 page=view.page()
 if isValid(page):delete(page)
for profile in window.profiles:
 if isValid(profile):delete(profile)
temp.cleanup();raise SystemExit(code)
