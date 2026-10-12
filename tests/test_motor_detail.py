"""Single motor detail UI: isolated routing, decoder units, bounded curves and query targeting."""
from pathlib import Path
import struct
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
def sample(data,t):return (bytes([1,11,0,len(data)])+struct.pack('<I',123)+data+struct.pack('<I',t)).hex()
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':412,'height':915},device_scale_factor=2)
 ctx.route('https://**/*',lambda route:route.fulfill(path=str(ASSETS/route.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.actions=[];window.CanAndroid={dashboard(){},showDetail(k){actions.push(["detail",k])},leaveDetail(){actions.push(["leave"])},home(){actions.push(["home"])},setPolling(...a){actions.push(["poll",...a])}}')
 page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://app.local/index.html')
 page.evaluate("onNative('state',{value:'ready',name:'底盘-CAN'});commitWatch({profile:'c620',id:1,name:'左轮'});commitWatch({profile:'c620',id:2,name:'右轮'});commitWatch({profile:'dm4310',id:3,name:'关节',master:0,mode:'mit',limits:{p:12.5,v:30,t:10}});commitWatch({profile:'dm4310',id:4,master:0,mode:'mit',limits:{p:12.5,v:30,t:10}});dashboardOpen(JSON.stringify(watchedMotors[0].config));onNative('detailSupport',{supported:true,channel:'通道 1 · 底盘-CAN'});onNative('telemetryMode',{mode:2})")
 assert page.locator('#view-detail').is_visible() and not page.locator('.tabs').is_visible() and not page.locator('#view-motor').is_visible()
 assert page.locator('#detailSource').inner_text()=='通道 1 · 底盘-CAN'
 assert not page.locator('#detailQueryPanel').is_visible()
 assert page.locator('#detailSampleSave').is_disabled()
 page.evaluate("onNative('detailSupport',{supported:true,maxHz:1000,channel:'通道 1 · 底盘-CAN'})")
 assert not page.locator('#detailSampleSave').is_disabled()
 assert page.locator('#detailSampleHz').input_value()=='100'
 page.fill('#detailSampleHz','1000');page.locator('#detailSampleSave').click()
 assert page.evaluate('detailRateFor(watchedMotors[0])')==1000
 assert not page.evaluate('detailReady')
 page.evaluate("onNative('telemetryMode',{mode:2,hz:1000})")
 page.locator('#detailSampleSave').click();assert page.evaluate('detailReady') # unchanged rate needs no new ACK
 page.fill('#detailSampleHz','1001');page.locator('#detailSampleSave').click()
 assert page.evaluate('detailRateFor(watchedMotors[0])')==1000
 page.fill('#detailSampleHz','1000')
 assert page.evaluate("ConfigIO.exportChannel(1,'','').detailRates['c620:1:513']")==1000
 assert page.evaluate('detailRateFor(watchedMotors[1])')==100
 keys=page.evaluate('watchedMotors.map(m=>JSON.stringify(m.config))')
 def send(key,data,t):page.evaluate("async d=>{onNative('motorSample',d);await new Promise(requestAnimationFrame)}",{'key':key,'hex':sample(data,t)})
 dji=bytes([0x10,0,0,100,0,50,40,0]);send(keys[1],dji,500);assert page.evaluate('detailSamples.length')==0
 # Record immediately; paint the newest state at the next screen refresh.
 page.evaluate("async d=>{let before=document.querySelector('.trace').getAttribute('d');motorDetailSample(d);if(detailSamples.length!==1)throw Error('sample not recorded');await new Promise(requestAnimationFrame);if(document.querySelector('.detail-number').textContent!=='180.0°'||document.querySelector('.trace').getAttribute('d')===before)throw Error('frame not painted')}",{'key':keys[0],'hex':sample(dji,950)})
 page.evaluate('clearDetail();clearDetailStream()')
 for t in range(1000,2000,50):send(keys[0],dji,t)
 page.evaluate('drawDetail()');assert page.evaluate('detailSamples.length')==20
 assert page.locator('.detail-number').all_text_contents()==['180.0°','100 rpm','50 raw','40 °C']
 assert 'L' in page.locator('.trace').first.get_attribute('d')
 page.select_option('#detailWindow','10')
 send(keys[0],dji,4000);page.evaluate('drawDetail()');assert page.locator('.trace').first.get_attribute('d').count('M')==2
 assert page.evaluate('watchedMotors[1].feedback') is None
 page.evaluate('detailTogglePause()');end=page.evaluate('detailFrozen');send(keys[0],dji,4050);assert page.evaluate('detailFrozen')==end
 page.evaluate('detailTogglePause();detailMotor.time-=3000;refreshDetail()');assert '超时' in page.locator('#detailState').inner_text()
 assert page.locator('.detail-number').nth(1).inner_text()=='100 rpm'
 # Raw bytes use the configured feedback CAN ID, never the event11 revision or motor base ID.
 page.locator('#detailStreamTab').click();assert page.locator('#detailStreamPanel').is_visible()
 assert not page.locator('#detailChartsPanel').is_visible()
 assert page.locator('#detailStreamId').inner_text()=='0x201'
 assert page.locator('.detail-can-frame').first.locator('.can-id').inner_text()=='0x201'
 assert page.locator('.detail-can-frame').first.locator('.frame-info').inner_text()=='STD · DLC 8'
 assert page.locator('.detail-can-frame').first.locator('.bytes').inner_text().split()==['10','00','00','64','00','32','28','00']
 assert page.locator('.detail-can-frame').first.locator('.time').inner_text()=='4.050 s'
 page.locator('#detailStreamPause').click();frozen=page.locator('#detailStreamFrames').inner_text()
 send(keys[0],dji,4100);page.wait_for_timeout(150);assert page.locator('#detailStreamFrames').inner_text()==frozen
 assert page.evaluate('detailRxFrames[0].stamp')==4100
 page.locator('#detailStreamPause').click();assert page.locator('.detail-can-frame').first.locator('.time').inner_text()=='4.100 s'
 # Pagination freezes the list while browsing older entries; TX history stays untouched.
 for t in range(4200,7000,50):send(keys[0],dji,t)
 page.evaluate('renderDetailStream()');assert page.locator('.detail-can-frame').count()==40
 page.locator('#detailStreamOlder').click();assert page.locator('#detailStreamPause').inner_text()=='继续列表'
 assert page.evaluate('detailStreamPage')==1
 page.fill('#detailStreamLimit','3');page.evaluate('saveDetailStreamLimit()')
 assert page.evaluate('rxSaveLimit')==3 and page.evaluate('txSaveLimit')==300
 assert page.evaluate('detailRxFrames.length')==3 and page.evaluate('detailStreamFrozen.length')==3
 assert page.evaluate("ConfigIO.readField('historyLimits').rx")==3
 page.fill('#detailStreamLimit','10001');page.evaluate('saveDetailStreamLimit()');assert page.evaluate('rxSaveLimit')==3
 page.fill('#detailStreamLimit','0');page.evaluate('saveDetailStreamLimit()');send(keys[0],dji,7050)
 assert page.evaluate('detailRxFrames.length')==0 and page.evaluate('detailSamples.length')>0
 assert '上限为 0' in page.locator('#detailStreamFrames').inner_text()
 page.fill('#detailStreamLimit','300');page.evaluate('saveDetailStreamLimit();toggleDetailStream()');send(keys[0],dji,7100)
 page.evaluate('renderDetailStream()');assert page.locator('.detail-can-frame').count()==1
 count=page.evaluate('detailSamples.length');page.evaluate('clearDetailStream()');assert page.evaluate('detailSamples.length')==count
 send(keys[0],dji,7150);page.evaluate('renderDetailStream()')
 for width in [320,412,820]:
  page.set_viewport_size({'width':width,'height':915});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.set_viewport_size({'width':412,'height':915});page.screenshot(path=str(ROOT/'android/dist/preview-motor-stream.png'),full_page=True)
 page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[2].config));onNative("telemetryMode",{mode:2});window.devicePollingSupported=true')
 assert page.evaluate('detailSamples.length')==0 and page.locator('#detailQueryPanel').is_visible()
 assert page.evaluate('detailRxFrames.length')==0
 assert page.locator('#detailStreamPanel').is_hidden()
 send(keys[2],bytes([0x14,0x80,0,0x80,0,0,43,44]),5000);assert page.evaluate('detailSamples.length')==0 # same Master, different low nibble
 send(keys[2],bytes([0x13,0x80,0,0x80,0,0,43,44]),5050);assert page.evaluate('detailSamples.length')==1
 assert page.locator('.detail-number').nth(3).inner_text()=='44 / 43 °C'
 assert page.evaluate('detailSamples[0].values.mosTemperature')==43
 page.evaluate("d=>{onNative('motorRates',{key:d,received:100,sampled:20,elapsed:1000});onNative('motorRates',{key:d,received:1100,sampled:220,elapsed:2000});refreshDetail()}",keys[2])
 assert page.locator('#detailCanRate').inner_text()=='1000.0 Hz'
 assert '200.0 Hz' in page.locator('#detailRateNote').inner_text()
 page.evaluate('detailAppWindow=performance.now()-1000;detailAppCount=75;refreshDetail()');assert '75.' in page.locator('#detailAppRate').inner_text()

 assert page.evaluate('detailRxFrames.length')==1 and page.evaluate('detailRxFrames[0].id')==0
 assert page.evaluate('detailRxFrames[0].data[0]')==0x13
 page.evaluate('detailQueryToggle(true)');assert page.evaluate('actions[actions.length-1][0]')=='poll'
 assert page.evaluate('actions[actions.length-1][2]')==3
 page.evaluate('detailQueryRate(25)');assert page.evaluate('detailMotor.config.pollHz')==25
 assert page.evaluate('actions[actions.length-1][0]')=='detail'
 assert page.evaluate('JSON.parse(actions[actions.length-1][1]).pollHz')==25
 # High-rate input cannot grow memory indefinitely; timestamp rollover remains ordered.
 page.evaluate("onNative('telemetryMode',{mode:2})")
 key=page.evaluate('JSON.stringify(detailMotor.config)');data=sample(bytes([0x13,0x80,0,0x80,0,0,43,44]),0)
 page.evaluate("d=>{for(let i=0;i<1300;i++){let a=Uint8Array.from(d.hex.match(/../g).map(x=>parseInt(x,16)));new DataView(a.buffer).setUint32(16,(0xffffff00+i*50)>>>0,true);motorDetailSample({key:d.key,hex:Array.from(a,x=>x.toString(16).padStart(2,'0')).join('')})}}",{'key':key,'hex':data})
 assert page.evaluate('detailRxFrames.length')==300
 assert page.evaluate('detailSamples.length-detailSampleHead')<=1201 and page.evaluate('detailClock')==64950
 # Preserve extrema in zoomed-out paths, cap live history, and draw 60k samples without argument overflow.
 page.evaluate("""() => {
 const points=Array.from({length:60000},(_,i)=>({t:i,values:{position:i===30000?1000:0,speed:0,effort:0,temperature:40}}));
 const reduced=detailPeakPoints(points,'position',60000,0);
 if(!reduced.some(p=>p.values.position===1000)||reduced.length>1600)throw Error('lost peak or unbounded SVG');
 detailSamples=points;detailSampleHead=0;detailClock=59999;detailLastStamp=59999;detailLastArrival=Date.now();$('detailWindow').value='60';drawDetail();
 }""")
 send(key,bytes([0x13,0x80,0,0x80,0,0,43,44]),60000)
 assert page.evaluate('detailSamples.length-detailSampleHead')==60000
 # Changing detail rate does not alter query activation, frequency, or query bridge traffic.
 page.evaluate("detailMotor.polling=true;detailMotor.config.pollHz=25;window.rateActionsBefore=actions.length;$('detailSampleHz').value=500;saveDetailSampleRate()")
 assert page.evaluate('detailMotor.polling') and page.evaluate('detailMotor.config.pollHz')==25
 assert page.evaluate('actions.length==rateActionsBefore')
 assert page.evaluate('detailRateFor(detailMotor)')==500
 page.evaluate("onNative('telemetryMode',{mode:2,hz:500})")

 for width in [320,412,820]:
  page.set_viewport_size({'width':width,'height':915});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.set_viewport_size({'width':412,'height':915});page.evaluate('drawDetail()');page.screenshot(path=str(ROOT/'android/dist/preview-motor-detail.png'),full_page=True)
 page.evaluate("onNative('state',{value:'idle',message:'已断开'})");page.wait_for_timeout(200);assert page.evaluate('detailSamples.length')==0
 assert '未连接' in page.locator('#detailState').inner_text()
 assert page.evaluate('detailRxFrames.length')==0
 page.evaluate('detailHome()');assert page.evaluate('actions[actions.length-1][0]')=='home'
 assert not errors,errors
 b.close()
print('PASS: single motor isolation, CAN stream ID/bytes/timestamps, pagination/pause, independent RX limits, DM shared Master routing, curves, disconnect and responsive layout.')
