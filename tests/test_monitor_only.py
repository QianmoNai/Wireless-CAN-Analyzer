"""Monitor-only UI: no motor/raw send entry points, feedback and settings retained."""
from pathlib import Path
import struct
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
def packet(cid,data,kind=1,flags=0):return (bytes([1,kind,flags,len(data)])+struct.pack('<I',cid)+data.ljust(8,b'\0')+bytes(4)).hex()
def config():return (bytes([1,5,0,7])+struct.pack('<I',1000000)+bytes(12)).hex()
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':412,'height':915},device_scale_factor=2)
 ctx.route('https://**/*',lambda route:route.fulfill(path=str(ASSETS/route.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.sent=[];window.CanAndroid={scan(){},connect(){},disconnect(){},send(h){sent.push(h)}}')
 page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://app.local/index.html')
 assert page.locator('#view-motor').is_visible()
 assert page.locator('#motorRoster,#watchList,.watched-motor').count()==0
 assert page.locator('#watchEditSelected').is_disabled() and page.locator('#watchRemoveSelected').is_disabled()
 assert page.locator('#motorControlPanel,#view-send,#motorPulse,#demoToggle').count()==0
 assert page.evaluate("typeof motorSend==='undefined'&&typeof sendCanFrame==='undefined'&&typeof Motors.dm==='undefined'&&typeof Motors.special==='undefined'")
 page.evaluate("onNative('state',{value:'ready',name:'底盘-CAN'})")
 page.evaluate('h=>processPacket(h)',config())
 page.locator('#rosterAdd').click();page.fill('#watchName','左轮');page.locator('#watchSave').click()
 page.locator('#rosterAdd').click();page.select_option('#watchBrand','dm');page.select_option('#watchProfile','dm4310');page.fill('#watchName','关节');page.fill('#watchId','2');page.fill('#watchFeedback','120');page.locator('#watchSave').click()
 page.evaluate('openWatchEditor(watchedMotors[1])')
 assert page.locator('#watchBrand').input_value()=='dm'
 assert page.locator('#watchProfile option').count()>=3
 assert page.locator('#watchFeedback').input_value()=='120'
 page.select_option('#watchProfile','dm_custom');assert page.locator('#watchLimits').get_attribute('open') is not None
 assert page.locator('#watchP').input_value()==''
 page.select_option('#watchBrand','dji');assert page.locator('#watchProfile option').count()>=3
 assert page.locator('#watchDmFields').is_hidden()
 page.evaluate('closeWatchEditor()')
 page.evaluate("openWatchEditor();$('watchProfile').value='gm3510';watchEditorProfileChanged()")
 assert page.locator('#watchFeedback').input_value()=='205'
 page.evaluate("const testMotor={config:validateWatch({profile:'gm3510',id:3,feedbackId:0x207}),feedback:null,time:0};openMotorDetail(testMotor)")
 assert page.locator('.detail-plot').count()==2
 assert '转矩' in page.locator('.detail-plot').nth(1).inner_text()
 page.evaluate('closeMotorDetail(false);closeWatchEditor()')
 assert page.locator('#watchMode').count()==0
 assert page.evaluate('watchedMotors.length')==2
 # The native home/back path closes detail without calling showTab again.
 # Reopening that channel must match its visible page and selected tab.
 for previous in ('live','settings'):
  page.locator('.tab[data-tab="'+previous+'"]').click()
  page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[1].config))')
  page.evaluate('closeMotorDetail(false)')
  assert page.locator('.tabs .tab.active').count()==1
  assert page.locator('.tabs .tab.active').get_attribute('data-tab')=='motor'
  assert page.locator('.view:visible').evaluate_all('(nodes)=>nodes.map(n=>n.id)')==['view-motor']
  for current in ('live','settings','motor'):
   page.locator('.tab[data-tab="'+current+'"]').click()
   assert page.locator('.tabs .tab.active').get_attribute('data-tab')==current
   assert page.locator('.view:visible').evaluate_all('(nodes)=>nodes.map(n=>n.id)')==['view-'+current]
  page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[1].config))')
  page.evaluate("showTab('unknown')")
  assert page.locator('#view-detail').is_visible()
  page.evaluate('name=>showTab(name)',previous)
  assert page.locator('.tabs .tab.active').get_attribute('data-tab')==previous
  assert page.locator('.view:visible').evaluate_all('(nodes)=>nodes.map(n=>n.id)')==['view-'+previous]
  page.evaluate("showTab('motor')")
 page.evaluate('h=>processPacket(h)',packet(0x201,bytes([0x10,0,0,100,0,50,40,0])))
 page.evaluate('h=>processPacket(h)',packet(0x120,bytes([0x12,0x80,0,0x80,0,0,43,44])))
 page.evaluate('refreshWatchedMotors()')
 assert page.evaluate('watchedMotors[0].feedback.speed')=='100 rpm'
 assert page.evaluate('watchedMotors[1].feedback.state')=='使能'
 page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[1].config))')
 assert page.locator('#detailName').inner_text()=='关节'
 assert page.locator('#view-detail').is_visible()
 page.evaluate("showTab('motor')")
 assert page.evaluate('sent.length')==0
 page.select_option('#watchSelect','0');page.locator('#watchEditSelected').click();page.fill('#watchFeedback','321');page.locator('#watchSave').click()
 assert page.evaluate('watchedMotors[0].feedback') is None
 page.evaluate('h=>processPacket(h)',packet(0x201,bytes(8)));assert page.evaluate('watchedMotors[0].feedback') is None
 page.evaluate('h=>processPacket(h)',packet(0x321,bytes([0,0,0,20,0,0,30,0])))
 page.evaluate('refreshWatchedMotors()');assert page.evaluate('watchedMotors[0].feedback.speed')=='20 rpm'
 page.select_option('#watchSelect','0');page.locator('#watchRemoveSelected').click();page.locator('#rosterUndo').click();assert page.evaluate('watchedMotors.length')==2
 page.evaluate("$('toast').hidden=true;window.scrollTo(0,0)");page.screenshot(path=str(ROOT/'android/dist/preview-monitor-only.png'),full_page=True)
 # Telemetry statistics remain independent of retained motor feedback.
 page.evaluate("showTab('live');$('filter').value='321';renderFrames()")
 assert page.locator('.frame').count()==1
 page.evaluate("showTab('settings');$('rxLimit').value=0;applyHistoryLimits()")
 assert page.evaluate('frames.filter(f=>f.kind===1).length')==0
 # Only bridge settings create writes, and results still await device notifications.
 page.select_option('#busBitrate','500000');page.locator('#saveBitrate').click()
 cmd=bytes.fromhex(page.evaluate('sent[0]'));assert cmd[1]==0x12 and struct.unpack_from('<I',cmd,4)[0]==500000
 reply=bytearray(cmd);reply[1]=5;reply[3]=7;page.evaluate('h=>processPacket(h)',reply.hex())
 assert '已应用' in page.locator('#rateResult').inner_text()
 page.fill('#bleNameInput','状态-CAN');page.locator('#saveBleName').click()
 cmd=bytes.fromhex(page.evaluate('sent[1]'));assert cmd[1]==0x13
 page.evaluate("onNative('writeDone',{success:true})");assert page.evaluate('nameSaving')
 name='状态-CAN'.encode();page.evaluate('h=>processPacket(h)',(bytes([1,7,1,len(name)])+name.ljust(16,b'\0')).hex())
 assert '永久保存' in page.locator('#bleNameResult').inner_text()
 page.evaluate("onNative('state',{value:'idle',message:'已断开'})")
 assert page.evaluate('watchedMotors.every(m=>m.feedback===null)')
 assert page.locator('#saveBitrate').is_disabled()
 page.reload();assert page.evaluate('watchedMotors.length')==2
 assert page.evaluate('rxSaveLimit')==0
 assert not errors,errors
 b.close()
print('PASS: monitor-only navigation and no control codecs, DJI/DM feedback, card focus, editing, undo, zero RX retention, bitrate/name settings and disconnect/persistence.')
