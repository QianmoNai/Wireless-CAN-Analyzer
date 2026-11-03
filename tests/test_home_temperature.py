"""Home temperatures from raw feedback and quiet summaries; native BLE is mocked."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'android/app/src/main/assets'
with sync_playwright() as p:
    browser = p.chromium.launch(args=['--no-sandbox'])
    ctx = browser.new_context(viewport={'width': 375, 'height': 915})
    ctx.route('https://**/*', lambda route: route.fulfill(path=str(ASSETS / route.request.url.rsplit('/', 1)[-1])))
    ctx.add_init_script('window.CanAndroid={dashboard(){}};window.HomeAndroid={};')
    channel = ctx.new_page()
    home = ctx.new_page()
    errors = []
    for page in [channel, home]:
        page.on('pageerror', lambda error: errors.append(str(error)))
    channel.goto('https://channel.local/index.html')
    home.goto('https://home.local/home.html')
    channel.evaluate("""() => {
        onNative('state',{value:'ready',name:'底盘'});
        commitWatch({profile:'c620',id:1,feedbackId:0x321,name:'左轮'});
        commitWatch({profile:'dm4310',id:3,master:0,mode:'mit',limits:{p:12.5,v:30,t:10},name:'关节'});
        commitWatch({profile:'c610',id:2});
        commitWatch({profile:'gm3510',id:3});
        for (const m of watchedMotors) {
            const c=m.config,p=Motors.profiles[c.profile],data=p.family==='dm'?[0x13,0,0,0,0,0,66,42]:[0,0,0,0,0,0,35,0];
            m.feedback=p.family==='dm'?Motors.dmFeedback(c.id,c.master,c.feedbackId,data,c.limits):Motors.djiFeedback(c.profile,c.id,c.feedbackId,data,c.feedbackId);
            m.time=Date.now();
        }
    }""")

    def render(connected=True, age=0, extra=None):
        motors = channel.evaluate('dashboardSnapshot()')
        if extra:
            motors += extra
        home.evaluate('updateHome', [{'slot': 1, 'name': '底盘', 'label': '已连接', 'connected': connected, 'age': age, 'motors': motors}])

    def temperatures():
        return home.locator('#cards .motor-temperature strong').all_text_contents()

    render()
    assert temperatures() == ['35 °C', '42 °C', '— °C', '— °C']
    assert '电机温度 42 摄氏度' in home.locator('.motor').nth(1).get_attribute('aria-label')
    assert home.locator('#cards .motor-temperature').nth(2).get_attribute('title') == '此型号不提供温度反馈'
    # Temperature belongs at the bottom right without covering status, also while dragging.
    for width in [320, 359, 375, 412, 820]:
        home.set_viewport_size({'width': width, 'height': 915})
        assert home.evaluate("""() => document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll('.motor')].every(card=>{
            const c=card.getBoundingClientRect(),t=card.querySelector('.motor-temperature').getBoundingClientRect(),s=card.querySelector('.status').getBoundingClientRect();
            return Math.abs(c.right-t.right-16)<1 && Math.abs(c.bottom-t.bottom-16)<1 && s.right<=t.left && s.bottom<=c.bottom-15;
        })""")
    home.set_viewport_size({'width': 375, 'height': 915})
    # Let the resize handler settle before it can cancel the new drag preview.
    home.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    home.evaluate("const c=document.querySelector('.motor'),r=c.getBoundingClientRect();liftCard({card:c,lastX:r.left+20,lastY:r.top+20})")
    assert home.locator('.drag-preview .motor-temperature strong').inner_text() == '35 °C'
    home.evaluate('finishDrag(false)')
    # Mode transitions clear full-feedback caches; an old firmware's online-only summary stays unknown.
    channel.evaluate("onNative('telemetryMode',{quiet:true,mode:1});onNative('onlineSummary',{count:4,online:15,seen:15})")
    render()
    assert temperatures() == ['— °C'] * 4
    # Summary motor temperature must be the DM motor winding value, not MOS temperature.
    channel.evaluate("onNative('motorTemperatures',{start:0,count:4,temperatures:[0,255,88,99]})")
    render()
    assert temperatures() == ['0 °C', '255 °C', '— °C', '— °C']
    channel.evaluate("onNative('motorTemperatures',{start:0,count:3,temperatures:[12,13,14]})")
    render()
    assert temperatures()[:2] == ['0 °C', '255 °C']
    # Expiry is independent of fresh online summaries and of the native snapshot delivery time.
    channel.evaluate('watchedMotors.forEach(m=>m.temperatureAt-=3000)')
    render()
    assert temperatures() == ['— °C'] * 4
    channel.evaluate("onNative('motorTemperatures',{start:0,count:4,temperatures:[35,42,null,null]})")
    render(age=3000)
    assert temperatures() == ['— °C'] * 4
    render(connected=False)
    assert temperatures() == ['— °C'] * 4
    channel.evaluate("onNative('onlineSummary',{count:4,online:13,seen:15})")
    render()
    assert temperatures()[:2] == ['35 °C', '— °C']
    channel.evaluate("onNative('motorTemperatures',{start:0,count:4,temperatures:[null,null,null,null]})")
    render()
    assert temperatures() == ['— °C'] * 4
    channel.evaluate("onNative('telemetryMode',{quiet:false,mode:0})")
    render()
    assert temperatures() == ['— °C'] * 4
    # IMU shares the home grid, but this feature is for motor cards.
    render(extra=[{'key': '{"profile":"dm_imu_l1","id":4,"feedbackId":9}', 'name': 'IMU', 'model': 'DM-IMU-L1', 'id': '0x004', 'feedbackId': '0x009', 'family': 'imu', 'connectionStatus': 'online', 'temperature': 30, 'temperatureAge': 0}])
    assert home.locator('#cards .motor-temperature').last.is_hidden()
    # Capture a useful final preview with live and unsupported motor temperatures.
    channel.evaluate("onNative('telemetryMode',{quiet:true,mode:1});onNative('onlineSummary',{count:4,online:15,seen:15});onNative('motorTemperatures',{start:0,count:4,temperatures:[35,42,null,null]})")
    render()
    home.screenshot(path=str(ROOT / 'android/dist/preview-home-temperature.png'), full_page=True)
    assert not errors, errors
    browser.close()
print('PASS: current motor temperatures, DM winding vs MOS, quiet/legacy summaries, expiry/disconnect, unsupported models, IMU, narrow card and drag layout; BLE/native mocked.')
