"""Offline multi-tab UI operation check. No hardware transport is opened."""
import argparse,json,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'desktop'))
from can_scope.app import QApplication,Window,QTimer,delete,isValid
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--switches',type=int,default=20);parser.add_argument('--settle-ms',type=int,default=100);options=parser.parse_args();options.output.parent.mkdir(parents=True,exist_ok=True)
if not 2<=options.switches<=200 or not 50<=options.settle_ms<=1000:parser.error('switches must be 2..200; settle-ms must be 50..1000')
app=QApplication([]);app.setOrganizationName('Qianmo');app.setApplicationName('CAN Scope Desktop')
profile=tempfile.TemporaryDirectory(prefix='can-tabs-offline-');w=Window(Path(profile.name));w.add_channel();w.showMaximized();report={'scope':'offline real Qt; simulated connected UI; no hardware transport','samples':[]};iteration=0;finished=False
out=options.output
def js(slot,source,callback=lambda _:None):w.views[slot].page().runJavaScript(source,callback)
seed="commitWatch({profile:'c620',id:1,name:'离线模拟电机'});onNative('state',{value:'ready',name:'离线模拟',message:'界面操作测试'});dashboardOpen(JSON.stringify(watchedMotors[0].config));true;"
def start():
 if len(w.ready)<2:QTimer.singleShot(100,start);return
 js(1,seed,lambda _:js(2,seed,lambda _:open_next()))
def open_next():
 global iteration
 if iteration>=options.switches:
  slot=w.tabs.currentIndex()+1
  js(slot,'closeDetailChartFullscreen();true;',lambda _:QTimer.singleShot(options.settle_ms,closed));return
 slot=iteration%2+1;iteration+=1;w.tabs.setCurrentIndex(slot-1)
 js(slot,"openDetailChart(detailSeries[0].key);true;",lambda _:QTimer.singleShot(options.settle_ms,lambda:sample(slot)))
def sample(slot):
 def got(value):
  report['samples'].append({'iteration':iteration,'active_slot':slot,'native_fullscreen':w.isFullScreen(),'saved_state':w.chart_window_state.value if w.chart_window_state is not None else None,'owner':w.chart_window_slot,'page':json.loads(value)})
  open_next()
 js(slot,"JSON.stringify({key:detailChartKey,visible:!document.hidden,connected})",got)
def closed():
 report['final_state']={'window':w.windowState().value,'saved':w.chart_window_state.value if w.chart_window_state is not None else None,'owner':w.chart_window_slot};report['maximized_restored']=w.isMaximized() and not w.isFullScreen()
 hidden=2 if w.tabs.currentIndex()==0 else 1
 w.set_chart_fullscreen(hidden,True)
 report['hidden_request_ignored']=not w.isFullScreen()
 finish()
def finish():
 global finished
 if finished:return
 finished=True
 # Close while a native fullscreen reconciliation is still queued. The window
 # must remain closed when Qt drains that callback during shutdown.
 w.set_chart_fullscreen(w.tabs.currentIndex()+1,True);w.close();app.processEvents()
 report['closed_not_reopened']=not w.isVisible() and w.chart_window_slot is None
 report['js_errors']=w.js_errors;report['ok']=report.get('maximized_restored')and report.get('hidden_request_ignored')and report['closed_not_reopened']and len(report['samples'])==options.switches and not w.js_errors and all(r['native_fullscreen']and r['page']['key']is not None and r['page']['visible']for r in report['samples']);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');app.quit()
QTimer.singleShot(200,start);QTimer.singleShot(options.switches*(options.settle_ms+300)+15000,finish);app.exec()
for view in w.views.values():
 if isValid(view.page()):delete(view.page())
for p in w.profiles:
 if isValid(p):delete(p)
profile.cleanup();print(json.dumps(report,ensure_ascii=False));sys.exit(0 if report.get('ok')else 1)
