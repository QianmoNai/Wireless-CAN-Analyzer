from pathlib import Path
import struct
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
with sync_playwright() as p:
 b=p.chromium.launch(args=['--no-sandbox'])
 ctx=b.new_context(viewport={'width':412,'height':915})
 ctx.route('https://**/*',lambda route:route.fulfill(path=str(ASSETS/route.request.url.rsplit('/',1)[-1])))
 ctx.add_init_script('window.sent=[];window.CanAndroid={send(h){sent.push(h)},dashboard(){},showDetail(){}}')
 page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://app.local/index.html')
 page.locator('#rosterAdd').click();page.select_option('#watchBrand','dm');page.select_option('#watchProfile','dm_imu_l1');page.fill('#watchName','姿态模块');page.fill('#watchId','123');page.fill('#watchFeedback','11');assert page.locator('#watchDmFields').is_hidden();page.locator('#watchSave').click()
 assert page.evaluate('watchedMotors.length')==1
 assert page.evaluate('watchedMotors[0].config.id')==0x123
 page.evaluate("onNative('state',{value:'ready'});openMotorDetail(watchedMotors[0]);motorDetailSupport({supported:true,maxHz:1000});motorDetailMode({mode:2,hz:100})")
 assert page.locator('#detailQueryPanel').is_hidden();assert page.locator('.detail-plot').count()==14
 for n,data in enumerate([[1,36,0,0,255,255,0,128],[2,0,0,128,0,128,0,128],[3,0,0,128,255,255,0,0],[4,128,0,0,0,0,0,0]]):
  frame=(bytes([1,11,0,8])+struct.pack('<I',7)+bytes(data)+struct.pack('<I',n*10)).hex()
  page.evaluate("hex=>motorDetailSample({key:JSON.stringify(watchedMotors[0].config),hex})",frame)
 assert page.evaluate('detailMotor.feedback.values.temperature')==36
 assert page.evaluate('detailMotor.feedback.values.yaw')==180
 assert page.evaluate('detailRxFrames.length')==4
 assert page.evaluate('detailSamples[3].values.accelX===undefined')
 page.locator('.detail-plot').first.click();assert page.locator('#detailChartFullscreen').is_visible();page.locator('#detailChartClose').click()
 page.evaluate("Object.keys(detailMotor.feedback.times).forEach(k=>detailMotor.feedback.times[k]-=3000);refreshDetail()")
 assert page.locator('.detail-number').all_text_contents()==['—']*14
 page.evaluate('dashboardEdit(JSON.stringify(watchedMotors[0].config))');assert page.locator('#watchBrand').input_value()=='dm';assert page.locator('#watchProfile').input_value()=='dm_imu_l1';assert page.locator('#watchId').input_value()=='123'
 assert page.evaluate('sent.length')==0
 assert not errors,errors
 b.close()
print('PASS: IMU add/edit, independent fields and stale data, fullscreen curves, raw stream, no motor queries')
