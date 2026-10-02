"""Per-motor switches and home routing; native scheduler tested separately in Java."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':412,'height':915},device_scale_factor=2)
 ctx.route('https://**/*',lambda r:r.fulfill(path=str(ASSETS/r.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.sent=[];window.queries=[];window.actions=[];window.CanAndroid={scan(){},connect(){},disconnect(){},send(h){sent.push(h)},setPolling(key,id,enabled,hz){queries.push({key,id,enabled,hz});onNative("queryState",{key,enabled,message:enabled?"持续查询中":"查询已关闭"})}};window.HomeAndroid={setQueryHz(slot,key,hz){actions.push(["hz",slot,key,hz])},setPolling(slot,key,on){actions.push([slot,key,on])},openMotor(slot,key){actions.push(["open",slot,key])}}')
 page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://app.local/index.html')
 page.evaluate("commitWatch({profile:'c620',id:1});commitWatch({profile:'dm4310',id:2,name:'关节 1',master:0,mode:'mit',limits:{p:12.5,v:30,t:10}});commitWatch({profile:'dm4310',id:3,name:'关节 2',master:0,mode:'mit',limits:{p:12.5,v:30,t:10}})")
 assert page.locator('#motorRoster,#watchList,.watched-motor').count()==0
 page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[1].config))')
 assert page.locator('#detailQuery').is_disabled()
 page.evaluate("onNative('state',{value:'ready',name:'ESP32-A'})")
 assert page.locator('#detailQuery').is_disabled(),'Old firmware must not allow device polling'
 page.evaluate("devicePacket(new Uint8Array([1,5,0,15,64,66,15,0,0,0,0,0,0,0,0,0,0,0,0,0]));refreshWatchedMotors()")
 assert page.locator('#detailHz').input_value()=='100'
 page.locator('#detailHz').fill('250');page.locator('#detailHz').press('Tab')
 assert page.evaluate('watchedMotors[1].config.pollHz')==250
 page.locator('#detailQuery').check();assert page.evaluate('watchedMotors[1].polling')
 assert not page.evaluate('watchedMotors[2].polling')
 assert page.evaluate('queries.at(-1).id')==2
 assert page.evaluate('queries.at(-1).hz')==250
 page.evaluate('dashboardOpen(JSON.stringify(watchedMotors[2].config))');page.locator('#detailQuery').check()
 home=ctx.new_page();home.on('pageerror',lambda e:errors.append(str(e)));home.goto('https://home.local/home.html')
 snapshot=page.evaluate('dashboardSnapshot()')
 home.evaluate('motors=>updateHome([{slot:1,name:"底盘-CAN",connected:true,label:"已连接",age:0,motors}])',snapshot)
 assert home.locator('.query-toggle,.query-hz,.values').count()==0
 home.locator('.motor').nth(1).click();assert home.evaluate('actions[0]')==['open',1,snapshot[1]['key']]
 page.evaluate('setMotorPolling(JSON.stringify(watchedMotors[1].config),false)');assert not page.evaluate('watchedMotors[1].polling')
 page.evaluate('setMotorQueryHz(JSON.stringify(watchedMotors[2].config),1000)')
 assert not page.evaluate('watchedMotors[2].polling')
 page.evaluate('setMotorQueryHz(JSON.stringify(watchedMotors[2].config),1001)')
 assert page.evaluate('watchedMotors[2].config.pollHz')==1000
 # Edit/remove and native failure disable only the matching motor.
 page.evaluate("showTab('motor')");page.select_option('#watchSelect','2');page.locator('#watchEditSelected').click();assert not page.evaluate('watchedMotors[2].polling')
 page.evaluate('closeWatchEditor();dashboardOpen(JSON.stringify(watchedMotors[1].config))');page.locator('#detailQuery').check()
 page.evaluate("onNative('queryState',{key:JSON.stringify(watchedMotors[1].config),enabled:false,message:'CAN 查询发送失败，已停止'})")
 assert not page.locator('#detailQuery').is_checked()
 assert '发送失败' in page.locator('#detailQueryNote').inner_text()
 page.locator('#detailQuery').check()
 exported=page.evaluate("ConfigIO.exportChannel(1,'ESP32-A','')")
 assert all('polling' not in m for m in exported['motors'])
 assert [m.get('pollHz') for m in exported['motors']]==[None,250,1000]
 # A query-management timeout stops renewals but keeps monitoring connected.
 page.evaluate("onNative('queryState',{key:JSON.stringify(watchedMotors[1].config),enabled:false,message:'查询配置确认超时，已停止续租；正在确认设备停止，等待停止确认或租约到期后可重开'})")
 assert page.evaluate('connected') and page.locator('#badgeText').inner_text()=='已连接'
 assert not page.locator('#detailQuery').is_checked()
 assert '正在确认设备停止' in page.locator('#detailQueryNote').inner_text()
 page.evaluate("onNative('queryState',{key:JSON.stringify(watchedMotors[1].config),enabled:false,message:'查询已停止，可重新开启'})")
 assert '可重新开启' in page.locator('#detailQueryNote').inner_text()
 page.evaluate("onNative('state',{value:'idle',message:'断开'})");assert not page.locator('#detailQuery').is_checked()
 page.evaluate("onNative('state',{value:'ready',name:'ESP32-A'})");assert not page.evaluate('watchedMotors[1].polling')
 page.reload();assert not page.evaluate('watchedMotors[1].polling')
 assert page.evaluate('watchedMotors[1].config.pollHz')==250
 home.screenshot(path=str(ROOT/'android/dist/preview-dm-query.png'),full_page=True)
 assert page.evaluate('sent.length')==0,'Switch should call dedicated native query bridge, not raw send'
 assert not errors,errors
 b.close()
print('PASS: DM-only toggles, connection gating, home routing without navigation, independent states, edit/failure/disconnect stop and no persistence/export of activation.')
