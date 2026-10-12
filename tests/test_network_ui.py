"""Exercise the actual WebView assets with a mock native bridge."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];ASSETS=ROOT/'android/app/src/main/assets'
with sync_playwright() as p:
 browser=p.chromium.launch(args=['--no-sandbox'])
 context=browser.new_context(viewport={'width':412,'height':915},device_scale_factor=2)
 context.route('https://**/*',lambda r:r.fulfill(path=str(ASSETS/r.request.url.rsplit('/',1)[-1])))
 context.add_init_script('''window.calls=[];window.CanAndroid={networkState(){},connectionMode(mode){calls.push(['mode',mode]);onNative('networkMode',{mode,paired:true,ip:'192.168.1.10'})},wifiConfigure(op,ssid,password){calls.push([op,ssid,password]);onNative('wifiResult',{busy:true,message:'正在安全配网'})},connectLan(ip){calls.push(['lan',ip])},scan(){}};''')
 page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://app.local/index.html')
 page.evaluate("showTab('settings')")
 assert page.locator('#wifiScan').is_disabled()
 page.evaluate("onNative('state',{value:'ready',name:'ESP32-CAN'});onNative('networkMode',{mode:'ble',paired:false})")
 assert page.locator('#wifiScan').is_enabled()
 page.locator('#wifiSsid').fill('测试网络');page.locator('#wifiPassword').fill('short');page.locator('#wifiSave').click()
 assert not page.evaluate('calls.length')
 page.locator('#wifiPassword').fill('12345678');page.locator('#wifiSave').click()
 assert page.evaluate('calls.at(-1)')==[5,'测试网络','12345678']
 assert page.locator('#wifiPassword').input_value()=='' and page.locator('#wifiScan').is_disabled()
 assert page.locator('#connectionMode').is_disabled()
 page.evaluate("onNative('wifiResult',{busy:false,message:'Wi-Fi 已连接并保存'});onNative('wifiScan',{rows:[['3c7363726970743e',-40,3],['e4b8ade69687',-60,0]]})")
 assert page.locator('#wifiNetworks option').count()==3
 assert '<script>' in page.locator('#wifiNetworks').inner_text()
 assert page.locator('#wifiNetworks script').count()==0
 page.select_option('#wifiNetworks','中文');assert page.locator('#wifiSsid').input_value()=='中文'
 page.screenshot(path=str(ROOT/'android/dist/preview-network-settings.png'),full_page=True)
 page.select_option('#connectionMode','wifi');assert page.evaluate('calls.at(-1)')==['mode','wifi']
 assert page.locator('#transportBadge').inner_text()=='Wi-Fi' and page.locator('#wifiScan').is_disabled()
 page.evaluate("document.getElementById('deviceModal').hidden=false;onNative('device',{address:'wifi:192.168.1.10',name:'ESP32-CAN',rssi:0,transport:'wifi'})")
 assert page.locator('#lanManual').is_visible()
 assert page.locator('#deviceList').inner_text().endswith('Wi-Fi')
 page.evaluate("document.getElementById('lanIp').value='192.168.1.10';connectManualLan()")
 assert page.evaluate('calls.at(-1)')==['lan','192.168.1.10']
 page.evaluate("onNative('state',{value:'idle',message:'断开'})")
 assert not errors,errors
 browser.close()
print('Wi-Fi UI: mode choices, native calls, UTF-8 SSID, password validation/clearing, busy states, safe scan labels and manual IP PASS')
