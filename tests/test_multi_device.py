"""Four monitor channels retain independent telemetry and saved settings; mocked BLE."""
from pathlib import Path
import struct
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context();ctx.route('https://**/*',lambda r:r.fulfill(path=str(ASSETS/r.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.sent=[];window.CanAndroid={scan(){},connect(){},disconnect(){},send(h){sent.push(h)}}')
 pages=[];errors=[]
 for i in range(4):
  pg=ctx.new_page();pg.on('pageerror',lambda e:errors.append(str(e)));pg.goto('https://'+('app' if i==0 else f'channel-{i+1}')+'.local/index.html')
  pg.evaluate("i=>{onNative('state',{value:'ready',name:'ESP32-'+i});$('rxLimit').value=i+1;$('txLimit').value=i+2;applyHistoryLimits();commitWatch({profile:'c620',id:1,name:'电机 '+i})}",i)
  data=bytes([1,1,0,8])+struct.pack('<I',0x201)+bytes([0,0,0,i+10,0,0,30,0])+bytes(4)
  pg.evaluate('h=>processPacket(h)',data.hex());pages.append(pg)
  load=bytes([1,13])+struct.pack('<HIIII',100*(i+1),1110*(i+1),0,1000000,1000000)
  pg.evaluate("h=>onNative('busLoad',{hex:h})",load.hex())
 for i,pg in enumerate(pages):
  assert pg.evaluate('watchedMotors[0].feedback.speed')==f'{i+10} rpm'
  assert pg.locator('#busLoad').inner_text()==f'≈ {i+1:.2f}%'
 # Switching one channel to home mode cannot retain its cached load or reset others.
 pages[2].evaluate("onNative('telemetryMode',{mode:1,quiet:true})")
 assert pages[2].locator('#busLoad').inner_text()=='—'
 for i in (0,1,3):assert pages[i].locator('#busLoad').inner_text()==f'≈ {i+1:.2f}%'
 pages[0].evaluate("onNative('state',{value:'idle',message:'断线'})")
 assert pages[0].evaluate('watchedMotors[0].feedback') is None
 assert pages[0].locator('#busLoad').inner_text()=='—'
 assert pages[1].locator('#busLoad').inner_text()=='≈ 2.00%'
 assert pages[3].locator('#busLoad').inner_text()=='≈ 4.00%'
 for i in (1,2,3):assert pages[i].evaluate('connected') and pages[i].evaluate('watchedMotors[0].feedback.speed')==f'{i+10} rpm'
 for i,pg in enumerate(pages):
  assert pg.evaluate('sent.length')==0
  pg.reload();assert pg.evaluate('[rxSaveLimit,txSaveLimit]')==[i+1,i+2]
  assert pg.locator('#busLoad').inner_text()=='—'
  assert pg.evaluate('watchedMotors[0].config.name')==f'电机 {i}'
 assert not errors,errors
 b.close()
print('PASS: four monitor channels, independent feedback/settings, device-load and disconnect isolation, load reset on mode changes, persistence; no writes.')
