"""Cross-channel dashboard and name settings, with mocked native BLE transport."""
from pathlib import Path
import struct
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
def name_event(name,result=0):
 data=name.encode();return (bytes([1,7,result,len(data)])+data.ljust(16,b'\0')).hex()
def config(cap):return (bytes([1,5,0,cap])+struct.pack('<I',1000000)+bytes(12)).hex()
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':412,'height':915},device_scale_factor=2)
 ctx.route('https://**/*',lambda route:route.fulfill(path=str(ASSETS/route.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.sent=[];window.snapshots=[];window.actions=[];window.CanAndroid={scan(){},connect(){},disconnect(){},send(h){sent.push(h)},dashboard(s){snapshots.push(JSON.parse(s))}};window.HomeAndroid={deleteMotor(slot,key){actions.push(["delete",slot,key])},connectAll(){actions.push(["connectAll"])},addChannel(){actions.push(["add"])},openChannel(slot){actions.push(["channel",slot])},openMotor(slot,key){actions.push(["motor",slot,key])}}')
 errors=[];pages=[]
 for i in range(2):
  page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://'+('app' if i==0 else 'channel-2')+'.local/index.html')
  page.evaluate("i=>{onNative('state',{value:'ready',name:'ESP32-'+i});commitWatch({profile:'c620',id:1,name:i?'右轮':'左轮'});commitWatch({profile:'c620',id:2,name:i?'后轮':'前轮'});watchedMotors.forEach((m,j)=>{m.feedback={angle:'90°',speed:(i*100+j*20)+' rpm',effort:'50 raw',temperature:'35 °C',state:'收到反馈'};m.time=Date.now()});publishDashboard()}",i)
  pages.append(page)
 home=ctx.new_page();home.on('pageerror',lambda e:errors.append(str(e)));home.goto('https://home.local/home.html')
 channels=[{'slot':i+1,'name':['底盘-CAN','关节-CAN'][i],'connected':True,'label':'已连接','age':0,'motors':pg.evaluate('snapshots[snapshots.length-1]')} for i,pg in enumerate(pages)]
 home.evaluate('updateHome',channels)
 # Batch reconnect is one native action, stays on the dashboard, and locks duplicate clicks.
 home.locator('#connectAll').click();assert home.evaluate('actions[actions.length-1]')==['connectAll']
 assert home.locator('#connectAll').is_disabled() and home.locator('#reconnectNotice').is_visible()
 home.evaluate("reconnectStatus({busy:false,message:'通道 1 已连接；通道 2 连接超时'})")
 assert not home.locator('#connectAll').is_disabled() and '通道 2' in home.locator('#reconnectNotice').inner_text()
 home.evaluate("configStatus('导入中',true)");assert home.locator('#connectAll').is_disabled()
 home.evaluate("configStatus('',false)");assert not home.locator('#connectAll').is_disabled()
 assert home.evaluate("document.querySelector('#connectAll').getBoundingClientRect().right<=document.querySelector('#addChannel').getBoundingClientRect().left")

 assert home.locator('.motor').count()==4
 assert home.locator('.source').first.inner_text()=='通道 1 · 底盘-CAN'
 assert home.locator('.status.live').count()==4
 assert home.locator('.values,.query-toggle,.query-hz,.state').count()==0
 assert home.locator('.motor-id').all_text_contents()==['ID 0x001','ID 0x002','ID 0x001','ID 0x002']
 assert home.locator('.feedback-id').all_text_contents()==['RX 0x201','RX 0x202','RX 0x201','RX 0x202']
 home.locator('.motor').nth(2).click()
 action=home.evaluate('actions[actions.length-1]');assert action[:2]==['motor',2]
 pages[1].evaluate('key=>dashboardOpen(key)',action[2]);assert pages[1].evaluate('activeWatch.config.name')=='右轮'
 assert pages[0].evaluate('activeWatch') is None
 assert all(pg.evaluate('sent.length')==0 for pg in pages)
 # Left swipe requests deletion once and suppresses the release click.
 card=home.locator('.motor').first;box=card.bounding_box();x=box['x']+100;y=box['y']+20
 home.mouse.move(x,y);home.mouse.down();home.mouse.move(x-70,y,steps=5);home.mouse.up()
 assert home.evaluate('actions[actions.length-1][0]')=='delete'
 deletion=home.evaluate('actions[actions.length-1]');before=home.evaluate('actions.length')
 # Movement cancels the pending long press and navigation.
 home.mouse.move(x,y);home.mouse.down();home.mouse.move(x+25,y+25);home.wait_for_timeout(700);home.mouse.up()
 assert home.evaluate('actions.length')==before
 card.click();assert home.evaluate('actions[actions.length-1][0]')=='motor'
 # Correct channel/key deletion persists and leaves the other channel intact.
 assert pages[0].evaluate('key=>dashboardDelete(key)',deletion[2])=='已删除电机'
 assert pages[0].evaluate('watchedMotors.length')==1
 assert pages[1].evaluate('watchedMotors.length')==2
 assert len(pages[0].evaluate("ConfigIO.readField('watchedMotors')"))==1
 assert pages[0].evaluate('key=>dashboardDelete(key)',deletion[2])=='该电机配置已更新，请重试'
 pages[0].evaluate('undoWatchRemove()')
 home.select_option('#filter','2');assert home.locator('.motor').count()==2
 home.select_option('#filter','all')
 channels[0]['motors'][0]['connectionStatus']='offline';channels[1]['connected']=False;channels[1]['label']='未连接'
 home.evaluate('updateHome',channels)
 assert '超时' in home.locator('.motor').first.locator('.status').inner_text()
 assert '蓝牙未连接' in home.locator('.motor').nth(2).locator('.status').inner_text()
 home.screenshot(path=str(ROOT/'android/dist/preview-home.png'),full_page=True)
 assert home.evaluate('document.documentElement.scrollWidth<=innerWidth')
 # Missing snapshots never claim a live motor, even if native BLE remains connected.
 channels[0]['age']=6000;home.evaluate('updateHome',channels)
 assert '状态更新超时' in home.locator('.motor').first.locator('.status').inner_text()
 # Device summaries provide online status without feedback payloads. Stale snapshots cannot stay online.
 page=pages[0];page.evaluate("onNative('telemetryMode',{quiet:true});onNative('onlineSummary',{online:1,seen:3,count:2})")
 summary=page.evaluate('dashboardSnapshot()');assert summary[0]['connectionStatus']=='online' and summary[1]['connectionStatus']=='offline'
 assert all('feedback' not in m and 'polling' not in m for m in summary)
 page.evaluate('summaryAt-=4000');assert page.evaluate('dashboardSnapshot()[0].statusAge')>=4000
 page.evaluate("onNative('telemetryMode',{quiet:false})");assert page.evaluate('watchedMotors[0].feedback') is None
 # Name save acknowledges device persistence, not just the ATT write callback.
 page=pages[0];page.evaluate("showTab('settings')")
 page.evaluate('h=>processPacket(h)',config(3));assert page.locator('#saveBleName').is_disabled()
 assert '固件' in page.locator('#bleNameResult').inner_text()
 page.evaluate('h=>processPacket(h)',config(7));page.evaluate('h=>processPacket(h)',name_event('ESP32-CAN'))
 assert page.locator('#bleNameInput').input_value()=='ESP32-CAN'
 page.fill('#bleNameInput','底盘-CAN');page.locator('#saveBleName').click()
 command=bytes.fromhex(page.evaluate('sent[0]'));assert command[:3]==bytes([1,0x13,len('底盘-CAN'.encode())]);assert command[4:4+command[2]].decode()=='底盘-CAN'
 page.evaluate("onNative('writeDone',{success:true})");assert page.evaluate('nameSaving')
 page.evaluate('h=>processPacket(h)',name_event('ESP32-CAN',0));assert page.evaluate('nameSaving')
 page.evaluate('h=>processPacket(h)',name_event('底盘-CAN',1));assert not page.evaluate('nameSaving')
 assert '永久保存' in page.locator('#bleNameResult').inner_text()
 page.fill('#bleNameInput','一二三四五六');page.locator('#saveBleName').click();assert page.evaluate('sent.length')==1
 assert '16' in page.locator('#bleNameResult').inner_text()
 page.fill('#bleNameInput','关节');page.locator('#saveBleName').click();page.evaluate('h=>processPacket(h)',name_event('底盘-CAN',2))
 assert '失败' in page.locator('#bleNameResult').inner_text()
 assert not page.evaluate('nameSaving')
 page.locator('#saveBleName').click();page.evaluate("onNative('state',{value:'idle',message:'断线'})")
 assert '未知' in page.locator('#bleNameResult').inner_text()
 assert page.locator('#saveBleName').is_disabled()
 # Card addresses use the configured feedback ID, including custom DJI and DM Master zero.
 pages[1].evaluate("commitWatch({...watchedMotors[0].config,feedbackId:0x321},watchedMotors[0]);commitWatch({profile:'dm4310',id:3,master:0,mode:'mit',limits:{p:12.5,v:30,t:10}})")
 channels[1]['motors']=pages[1].evaluate('dashboardSnapshot()');home.evaluate('updateHome',channels)
 assert home.locator('.feedback-id').all_text_contents()==['RX 0x201','RX 0x202','RX 0x321','RX 0x202','RX 0x000']
 assert '反馈 CAN ID 0x000' in home.locator('.motor').last.get_attribute('aria-label')
 for width in [320,375,412,820]:
  home.set_viewport_size({'width':width,'height':915})
  assert home.evaluate("document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('.motor-addresses')].every(b=>{const a=b.getBoundingClientRect(),c=b.closest('.motor').getBoundingClientRect();return a.left>=c.left&&a.right<=c.right})")
 home.set_viewport_size({'width':412,'height':915});home.screenshot(path=str(ROOT/'android/dist/preview-home.png'),full_page=True)
 assert not errors,errors
 b.close()
print('PASS: cross-channel cards/filter/routing, no control broadcast, stale/disconnected display, name capability/UTF-8/command encoding/persisted ACK/failure/disconnect; BLE/native mocked.')
