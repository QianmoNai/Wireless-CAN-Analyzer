"""Opt-in visible Qt CAN bench for the STM32H723 simulator; no motion commands.

Exercises raw monitor, DJI/IMU/DM details, status-only DM query, pause/filter and
quiet overview. Uses a private temporary profile; restores USB + CAN on exit.
Run with desktop/.venv/bin/python from a graphical Linux session.
"""
import argparse,asyncio,hashlib,importlib.util,json,os,sys,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desktop'))
from can_scope.app import QApplication,Window,QTimer,delete,isValid
from can_scope.connection_store import connection_folder,load_connection,save_connection
from can_scope.protocol import u32

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fixture',required=True,choices=['stm32h723'])
parser.add_argument('--mode',choices=['usb','ble','wifi'],required=True)
parser.add_argument('--port',default='/dev/ttyACM0')
parser.add_argument('--channel',type=int,choices=range(1,5),default=1)
parser.add_argument('--phase-seconds',type=float,default=20)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--plot-containment',action='store_true',help='A/B only: isolate plot layout/paint without changing product CSS')
parser.add_argument('--paint-hz',type=int,choices=(0,15,30,60),default=0,help='A/B only: cap automatic detail painting; received samples remain intact')
parser.add_argument('--detail-view',choices=['charts','fullscreen','stream'],default='charts')
parser.add_argument('--exercise-tabs',action='store_true',help='Also verify an inactive connected channel becomes silent and resumes')
parser.add_argument('--zoom',type=float,choices=(.75,.9,1,1.1,1.25,1.5,1.75,2),default=1,help='Display scale only; CAN sampling and retained history are unchanged')
parser.add_argument('--detail-window',choices=('0.1','0.5','1','5','10','30','60'),default='1')
parser.add_argument('--freeze-after',type=float,default=0,help='Pause detail curves this many seconds into each detail phase; samples continue')
parser.add_argument('--recompute-frozen',action='store_true',help='A/B only: disable frozen draw cache without changing sample storage')
parser.add_argument('--detail-script',type=Path,help='A/B only: override drawDetailSeries/detailPeakPoints from an isolated JS candidate')
args=parser.parse_args()
if not 5<=args.phase_seconds<=300:parser.error('--phase-seconds must be 5..300')
if args.freeze_after and not 0<args.freeze_after<args.phase_seconds:parser.error('--freeze-after must be zero or inside the detail phase')
if args.freeze_after and args.detail_view=='stream':parser.error('--freeze-after tests chart pause; choose charts or fullscreen')
detail_override=''
override_metadata=None
if args.detail_script:
 source=args.detail_script.read_text()
 # Resolve the candidate before touching the physical transport. Only drawing
 # functions are replaced; sample routing, storage and transport stay intact.
 for name,following in [('drawDetailSeries','detailQueryToggle'),('detailPeakPoints','detailLowerBound')]:
  marker='function '+name+'('
  detail_override+=marker+source.split(marker,1)[1].split('\nfunction '+following+'(',1)[0]+'\n'
 override_metadata={'path':str(args.detail_script.resolve()),'sha256':hashlib.sha256(source.encode()).hexdigest(),'functions':['drawDetailSeries','detailPeakPoints']}
mode=args.mode;out=args.output.resolve();out.parent.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('can_hardware_bench',ROOT/'scripts/test-can-hardware.py')
bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)
app=QApplication([]);app.setOrganizationName('Qianmo');app.setApplicationName('CAN Scope Desktop')
saved=load_connection(connection_folder()/f'connection-{args.channel}.json')
asyncio.run(bench.select(args.port,mode))
if mode=='wifi':asyncio.run(asyncio.sleep(10)) # association is setup, not a GUI connection retry
profile=tempfile.TemporaryDirectory(prefix='can-live-ui-');folder=Path(profile.name)
save_connection(folder/'connection-1.json',saved)
w=Window(folder);w.show();w.set_active_zoom(args.zoom);report={'fixture':'STM32H723 simulated motor/IMU over physical CAN','mode':mode,'plot_containment':args.plot_containment,'detail_view':args.detail_view,'display_zoom':args.zoom,'phases':[],'backend':[],'render_scope':'visible Qt software SVG callback timing; excludes compositor paint'};started=time.monotonic();phase=0;raw=0;maxqueue=0;finished=False;operations_started=False;device=[]
report['source_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('desktop/can_scope/app.py','desktop/can_scope/desktop.js','desktop/can_scope/engine.py','android/app/src/main/assets/motor_detail.js','android/app/src/main/assets/index.html')}
report['experimental_paint_hz']=args.paint_hz
report.update(started_utc=datetime.now(timezone.utc).isoformat(),started_monotonic=started)
report.update(detail_window=args.detail_window,freeze_after_s=args.freeze_after,recompute_frozen=args.recompute_frozen)
if override_metadata:report['experimental_detail_script']=override_metadata
def backend(slot,kind,value):
 if kind in ('state','error'):
  data=json.loads(value);row=dict(at_s=time.monotonic()-started,slot=slot,kind=kind,value=data.get('value'),message=data.get('message'));report['backend'].append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
w.runtime.message.connect(backend)
def js(source,cb=lambda _:None):w.views[1].page().runJavaScript(source,cb)
original=w.runtime.channels[1].receive
def received(p):
 global raw,maxqueue
 raw+=p[1]==1
 if p[1]==4:device.append(dict(rx=u32(p,4),tx=u32(p,8),failures=u32(p,12),drops=u32(p,16)))
 original(p);maxqueue=max(maxqueue,len(w.runtime.channels[1].packets))
w.runtime.channels[1].receive=received

setup="""
commitWatch({profile:'c620',id:1,name:'模拟 M3508'});
commitWatch({profile:'c610',id:2,name:'模拟 M2006'});
commitWatch({profile:'gm6020',id:1,name:'模拟 GM6020'});
for(let id=1;id<=4;id++)commitWatch({profile:'dm4310',id,name:'模拟达妙 '+id,master:0x10+id,mode:'mit',limits:{p:12.5,v:30,t:10}});
commitWatch({profile:'dm_imu_l1',id:0x50,name:'模拟 IMU',feedbackId:0x60});
window.perf={batch:[],draw:[],stream:[],long:[],raf:[]};
const onOriginal=onNative;window.onNative=(k,d)=>{let t=performance.now();onOriginal(k,d);if(k==='packets'||k==='motorSamples')perf.batch.push(performance.now()-t)};
const drawOriginal=drawDetail;drawDetail=()=>{let t=performance.now();drawOriginal();perf.draw.push(performance.now()-t)};
const streamOriginal=renderDetailStream;renderDetailStream=()=>{let t=performance.now();streamOriginal();perf.stream.push(performance.now()-t)};
new PerformanceObserver(l=>{for(const x of l.getEntries())perf.long.push(x.duration)}).observe({entryTypes:['longtask']});
window.lastRaf=0;function measureRaf(t){if(lastRaf)perf.raf.push(t-lastRaf);lastRaf=t;requestAnimationFrame(measureRaf)}requestAnimationFrame(measureRaf);
showTab('live');true;
"""
if args.plot_containment:
 setup+="const isolated=document.createElement('style');isolated.textContent='.detail-plot{contain:layout paint}';document.head.append(isolated);true;"
if args.paint_hz:
 setup+="const originalPaintRequest=requestDetailPaint;let lastPaintRequest=-Infinity;requestDetailPaint=()=>{const now=performance.now();if(now-lastPaintRequest<1000/"+str(args.paint_hz)+")return;lastPaintRequest=now;originalPaintRequest()};true;"
if args.recompute_frozen:
 setup+="const drawWithoutFrozenCache=drawDetail;drawDetail=()=>{detailFrozenDraw=null;drawWithoutFrozenCache()};true;"
def process_memory():
 # Current Linux process tree, numeric RSS only. Qt renderer lives below its
 # zygote, so include descendants instead of reporting Python memory alone.
 todo=[os.getpid()];seen=set();rss=renderer=0;cpu=renderer_cpu=0
 while todo:
  pid=todo.pop()
  if pid in seen:continue
  seen.add(pid)
  try:
   root=Path('/proc')/str(pid)
   todo.extend(map(int,(root/'task'/str(pid)/'children').read_text().split()))
   resident=int((root/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')//1024
   fields=(root/'stat').read_text().rsplit(')',1)[1].split()
   seconds=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')
   rss+=resident
   cpu+=seconds
   if b'--type=renderer' in (root/'cmdline').read_bytes():renderer+=resident;renderer_cpu+=seconds
  except (OSError,ValueError,IndexError):pass
 return dict(tree_rss_kib=rss,renderer_rss_kib=renderer,tree_cpu_s=cpu,renderer_cpu_s=renderer_cpu)
def sample_resources():
 if finished:return
 report.setdefault('resources',[]).append(dict(at_s=time.monotonic()-started,phase=phase,**process_memory()))
 # Preserve partial observations if a long hardware run fails or is stopped.
 temporary=out.with_suffix('.tmp')
 temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 temporary.replace(out)
 QTimer.singleShot(5000,sample_resources)
def begin():
 if 1 not in w.ready:QTimer.singleShot(200,begin);return
 def connect(_):
  w.runtime.call(1,'connect',[{'usb':'usb:'+args.port,'ble':saved['address'],'wifi':'wifi:'+saved.get('ip','')}[mode]])
  QTimer.singleShot(200,wait_connected)
 js(detail_override+setup,connect)
def wait_connected():
 if w.runtime.channels[1].ready:
  report['connect_s']=time.monotonic()-started
  QTimer.singleShot(int(args.phase_seconds*1000),nextphase)
 elif time.monotonic()-started<30 and not any(r['kind']=='error' for r in report['backend']):QTimer.singleShot(200,wait_connected)
 else:finish()
def snap(name,after=lambda:None):
 source="""JSON.stringify({name:NAME,connected,viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},roster:dashboardSnapshot(),frames:frames.length,appDrops,firmwareDrops,
 detail:detailMotor?{ready:detailReady,count:detailSamples.length,activeCount:detailSamples.length-detailSampleHead,clock:detailClock,frozen:detailFrozen!==null,frozenEnd:detailFrozen,frozenCount:detailFrozenSamples.length,values:detailMotor.feedback&&detailMotor.feedback.values,canRate:$('detailCanRate').textContent,appRate:$('detailAppRate').textContent,state:$('detailState').textContent,view:!$('detailStreamPanel').hidden?'stream':detailChartKey!==null?'fullscreen':'charts',streamRows:$('detailStreamFrames').querySelectorAll('.detail-can-frame').length}:null,
 perf:Object.fromEntries(Object.entries(perf).map(([k,a])=>[k,{count:a.length,max:a.length?Math.max(...a):0,mean:a.reduce((s,v)=>s+v,0)/(a.length||1),p95:[...a].sort((a,b)=>a-b)[Math.floor(a.length*.95)]||0}]))})""".replace('NAME',json.dumps(name))
 def got(value):
  row=json.loads(value or '{}');row.update(raw=raw,maxqueue=maxqueue,cpu_s=time.process_time(),memory=process_memory(),device=device[-1] if device else None);w.grab().save(str(out.with_name(out.stem+'-'+name+'.png')));report['phases'].append(row);print(json.dumps(row,ensure_ascii=False),flush=True);after()
 js(source,got)
def nextphase():
 global phase
 names=['raw','dji-detail','imu-detail','dm-detail','pause-filter','home']
 if phase>=len(names):finish();return
 name=names[phase];phase+=1
 def change():
  codes=["dashboardOpen(JSON.stringify(watchedMotors[0].config))","dashboardOpen(JSON.stringify(watchedMotors[7].config))","dashboardOpen(JSON.stringify(watchedMotors[3].config));setMotorPolling(JSON.stringify(watchedMotors[3].config),true)","showTab('live');togglePause();$('filter').value='201';renderFrames()","togglePause();$('filter').value='';showTab('motor')"]
  if phase<=len(codes):
   extra=";openDetailChart(detailSeries[0].key)" if args.detail_view=='fullscreen' and phase<=3 else ";showDetailTab('stream')" if args.detail_view=='stream' and phase<=3 else ''
   if phase<=3:extra=";$('detailWindow').value="+json.dumps(args.detail_window)+";drawDetail()"+extra
   js(codes[phase-1]+extra+';for(const k in perf)perf[k]=[];true;');QTimer.singleShot(int(args.phase_seconds*1000),nextphase)
   if args.freeze_after and phase<=3:QTimer.singleShot(round(args.freeze_after*1000),lambda:js('if(detailFrozen===null)detailTogglePause();true;'))
   if phase==5:
    def settled():report['home_after_one_second']=dict(raw=raw,applied_mode=w.runtime.channels[1].applied_mode)
    QTimer.singleShot(1000,settled)
  else:finish()
 snap(name,change)
def finish():
 global finished,operations_started
 if finished:return
 if args.exercise_tabs and len(report['phases'])==6 and not operations_started:
  operations_started=True
  def hidden():
   w.add_channel();w.tabs.setCurrentIndex(1)
   def settled():
    report['tabs']={'hidden_mode':w.runtime.channels[1].applied_mode,'hidden_raw':raw}
    def resume():
     report['tabs']['hidden_extra_raw']=raw-report['tabs']['hidden_raw'];w.tabs.setCurrentIndex(0)
     def resumed():
      report['tabs'].update(resumed_mode=w.runtime.channels[1].applied_mode,resumed_extra_raw=raw-report['tabs']['hidden_raw'],connected=w.runtime.channels[1].ready)
      finish()
     QTimer.singleShot(1500,resumed)
    QTimer.singleShot(2000,resume)
   QTimer.singleShot(1500,settled)
  js("showTab('live');true;",lambda _:QTimer.singleShot(1500,hidden))
  return
 finished=True
 phases=report['phases']
 report['device_delta']={k:(device[-1][k]-device[0][k])&0xffffffff for k in device[0]} if device else {}
 report.update(js_errors=w.js_errors,seconds=time.monotonic()-started,
  ok=len(phases)==6 and not w.js_errors and all(p.get('connected') and p.get('appDrops')==0 for p in phases)
   and all((p.get('detail') or {}).get('count',0)>=min(args.phase_seconds,60)*60 for p in phases[1:4])
   and all((p.get('detail') or {}).get('activeCount',60001)<=60000 and (p.get('detail') or {}).get('count',62048)<=62047 for p in phases[1:4])
   and all((p.get('detail') or {}).get('view')==args.detail_view for p in phases[1:4])
   and report.get('home_after_one_second',{}).get('applied_mode')==1
   and phases[-1]['raw']-report['home_after_one_second']['raw']<=128 and report['device_delta'].get('drops')==0 and report['device_delta'].get('failures')==0)
 if args.exercise_tabs:
  tabs=report.get('tabs',{});report['ok']=report['ok'] and tabs.get('hidden_mode')==3 and tabs.get('hidden_extra_raw',1)==0 and tabs.get('resumed_mode')==0 and tabs.get('resumed_extra_raw',0)>=300 and tabs.get('connected') is True
 if args.freeze_after:
  report['ok']=report['ok'] and all((p.get('detail') or {}).get('frozen') is True and (p.get('detail') or {}).get('frozenCount',0)>0 and p['detail']['clock']>p['detail']['frozenEnd'] for p in phases[1:4])
 out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 w.grab().save(str(out.with_suffix('.png')))
 w.close();app.quit()
QTimer.singleShot(int((args.phase_seconds*6+45)*1000),finish);QTimer.singleShot(500,begin);QTimer.singleShot(5000,sample_resources)
app.exec()
for view in w.views.values():
 if isValid(view.page()):delete(view.page())
for p in w.profiles:
 if isValid(p):delete(p)
profile.cleanup()
asyncio.run(bench.select(args.port,'usb'))
sys.exit(0 if report.get('ok') else 1)
