"""Fullscreen motor charts: live routing, shared controls and page lifecycle.

Uses a mocked native transport. No BLE device is connected and no CAN command
is sent. Run manually with the project's Playwright test environment.
"""
from pathlib import Path
import struct

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "android/app/src/main/assets"

OVERLAY = "#detailChartFullscreen"
TRACE = "#detailChartSvg .trace"
EXTRA = "#detailChartSvg .trace-extra"


def sample(data, stamp):
    assert len(data) == 8
    return (bytes([1, 11, 0, 8]) + struct.pack("<I", 123) + data + struct.pack("<I", stamp)).hex()


def dji(angle=4096, speed=100, effort=50, temperature=40):
    return struct.pack(">HhhBB", angle, speed, effort, temperature, 0)


def dm(mos=43, motor=44):
    return bytes([0x13, 0x80, 0, 0x80, 0, 0, mos, motor])


def send(page, key, data, stamp):
    page.evaluate("d => onNative('motorSample', d)", {"key": key, "hex": sample(data, stamp)})


def open_motor(page, index):
    page.evaluate("i => {dashboardOpen(JSON.stringify(watchedMotors[i].config));onNative('telemetryMode',{mode:2,hz:100});}", index)
    return page.evaluate("JSON.stringify(detailMotor.config)")


def open_chart(page, key):
    page.evaluate("key => openDetailChart(key)", key)
    expect(page.locator(OVERLAY)).to_be_visible()


def native_actions(page):
    return page.evaluate("actions")


def assert_no_transport_action(actions):
    assert all(item[0] == "fullscreen" for item in actions), actions


def live_isolation_and_preservation(page):
    key = open_motor(page, 0)
    other = page.evaluate("JSON.stringify(watchedMotors[1].config)")
    send(page, key, dji(speed=100), 1000)
    send(page, key, dji(speed=120), 1050)
    page.evaluate("""() => {
        window.chartHistoryReference=detailSamples;
        window.chartBefore={count:detailSamples.length,head:detailSampleHead,
            clock:detailClock,stream:detailRxFrames.length,key:JSON.stringify(detailMotor.config),
            hz:detailRateFor(detailMotor),ready:detailReady};actions=[];
    }""")
    # The user enters by clicking the speed card, not a testing-only shortcut.
    page.locator("#detailPlots .detail-plot").nth(1).click()
    expect(page.locator(OVERLAY)).to_be_visible()
    assert "速度" in page.locator("#detailChartTitle").inner_text()
    assert "左轮" in page.locator("#detailChartMotor").inner_text()
    assert "120" in page.locator("#detailChartValue").inner_text()
    assert page.locator("#detailChartUnit").inner_text() == "rpm"
    assert page.evaluate("detailSamples===chartHistoryReference")
    assert page.evaluate("detailSamples.length===chartBefore.count&&detailSampleHead===chartBefore.head&&detailClock===chartBefore.clock&&detailRxFrames.length===chartBefore.stream&&detailReady===chartBefore.ready&&detailRateFor(detailMotor)===chartBefore.hz")
    assert native_actions(page) == [["fullscreen", True]]
    assert page.locator(EXTRA).get_attribute("d") in (None, "")

    # Unrelated motor samples cannot replace the selected parameter or history.
    page.evaluate("""d => {
        const value=$('detailChartValue').textContent;
        const path=$('detailChartSvg').querySelector('.trace').getAttribute('d');
        const count=detailSamples.length;
        onNative('motorSample',d);
        if($('detailChartValue').textContent!==value||detailSamples.length!==count||
            $('detailChartSvg').querySelector('.trace').getAttribute('d')!==path)
            throw Error('unrelated motor changed fullscreen chart');
    }""", {"key": other, "hex": sample(dji(speed=999), 1100)})
    # Value and SVG follow the next screen refresh; storage remains immediate.
    page.evaluate("""async d => {
        const before=$('detailChartSvg').querySelector('.trace').getAttribute('d');
        onNative('motorSample',d);
        await new Promise(requestAnimationFrame);
        if(!$('detailChartValue').textContent.includes('250')||
            $('detailChartSvg').querySelector('.trace').getAttribute('d')===before)
            throw Error('fullscreen chart did not update on screen refresh');
    }""", {"key": key, "hex": sample(dji(angle=2048, speed=250, effort=75), 1100)})
    assert page.evaluate("detailSamples.length") == 3
    assert page.evaluate("watchedMotors[1].feedback") is None
    page.locator("#detailChartClose").click()
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert page.evaluate("detailSamples===chartHistoryReference&&detailSamples.length===3&&detailReady&&JSON.stringify(detailMotor.config)===chartBefore.key")
    assert native_actions(page) == [["fullscreen", True], ["fullscreen", False]]
    assert "250" in page.locator("#detailPlots .detail-number").nth(1).inner_text()


def pause_window_and_keyboard(page):
    key = page.evaluate("JSON.stringify(detailMotor.config)")
    page.select_option("#detailWindow", "10")
    page.evaluate("actions=[]")
    card = page.locator("#detailPlots .detail-plot").nth(0)
    assert card.get_attribute("tabindex") == "0"
    card.focus()
    card.press("Enter")
    expect(page.locator(OVERLAY)).to_be_visible()
    assert page.locator("#detailChartWindow").input_value() == "10"
    page.select_option("#detailChartWindow", "0.5")
    assert page.locator("#detailWindow").input_value() == "0.5"
    page.locator("#detailChartPause").click()
    assert page.evaluate("detailFrozen!==null")
    assert page.locator("#detailPause").text_content() == page.locator("#detailChartPause").text_content()
    page.evaluate("""() => {
        refreshDetailChart();drawDetail();
        const observer=new MutationObserver(()=>{});
        observer.observe($('detailChartFullscreen'),{subtree:true,childList:true,characterData:true,attributes:true});
        refreshDetailChart();drawDetail();
        const writes=observer.takeRecords().length;observer.disconnect();
        if(writes)throw Error('unchanged fullscreen chart was rewritten');
    }""")
    page.evaluate("window.chartFrozenReference=detailFrozenSamples;window.chartFrozenEnd=detailFrozen")
    # Scale/axis text can settle the SVG flex height on the next layout turn.
    page.evaluate("async()=>{for(let i=0;i<2;i++){await new Promise(requestAnimationFrame);drawDetail()}}")
    frozen_path = page.locator(TRACE).get_attribute('d')
    page.select_option('#detailChartWindow', '0.1')
    assert page.locator(TRACE).get_attribute('d') != frozen_path
    page.select_option('#detailChartWindow', '0.5')
    assert page.locator(TRACE).get_attribute('d') == frozen_path
    # A frozen curve still responds to resize and switching the selected series.
    page.set_viewport_size({'width': 915, 'height': 412})
    page.evaluate('drawDetail()')
    assert page.locator(TRACE).get_attribute('d') != frozen_path
    page.set_viewport_size({'width': 412, 'height': 915})
    page.evaluate('drawDetail()')
    assert page.locator(TRACE).get_attribute('d') == frozen_path
    open_chart(page, 'temperature')
    assert page.locator(TRACE).get_attribute('d') != frozen_path
    open_chart(page, 'position')
    assert page.locator(TRACE).get_attribute('d') == frozen_path
    page.evaluate("""d => {
        const before=$('detailChartSvg').querySelector('.trace').getAttribute('d');
        const oldCount=detailSamples.length;
        onNative('motorSample',d);
        if(detailSamples.length!==oldCount+1||detailFrozen!==chartFrozenEnd||
            detailFrozenSamples!==chartFrozenReference||
            $('detailChartSvg').querySelector('.trace').getAttribute('d')!==before)
            throw Error('fullscreen pause stopped receiving or changed frozen curve');
    }""", {"key": key, "hex": sample(dji(angle=8191, speed=350), 1150)})
    page.keyboard.press("Escape")
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert page.evaluate("detailFrozen!==null&&detailFrozenSamples===chartFrozenReference")
    assert page.locator("#detailWindow").input_value() == "0.5"
    # Space is the other standard activation key for the accessible plot card.
    card.focus()
    card.press("Space")
    expect(page.locator(OVERLAY)).to_be_visible()
    assert page.locator("#detailChartWindow").input_value() == "0.5"
    page.locator("#detailChartPause").click()
    assert page.evaluate("detailFrozen===null")
    assert page.locator("#detailPause").text_content() == page.locator("#detailChartPause").text_content()
    before_actions = native_actions(page)
    # Native Back already restores orientation; JS close(false) must not issue
    # an extra bridge orientation callback or leave the motor detail page.
    page.evaluate("closeDetailChartFullscreen(false)")
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert native_actions(page) == before_actions
    assert page.evaluate("detailMotor!==null&&detailReady")
    assert_no_transport_action(native_actions(page))


def responsive_landscape(page):
    page.evaluate("detailMotor.config.name='这是一个用于验证窄屏横向布局的很长电机名称';actions=[]")
    open_chart(page, "effort")
    for width, height in ((915, 412), (915, 320), (640, 320), (412, 915)):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("drawDetail()")
        boxes = page.evaluate("""() => {
            const overlay=$('detailChartFullscreen'),svg=$('detailChartSvg'),close=$('detailChartClose');
            const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}};
            return {overlay:box(overlay),svg:box(svg),close:box(close),width:innerWidth,height:innerHeight,
                pageOverflow:document.documentElement.scrollWidth>innerWidth,
                overlayOverflow:overlay.scrollWidth>overlay.clientWidth};
        }""")
        assert not boxes["pageOverflow"] and not boxes["overlayOverflow"], boxes
        for target in ("overlay", "svg", "close"):
            box = boxes[target]
            assert box["w"] > 0 and box["h"] > 0, boxes
            assert box["x"] >= -1 and box["y"] >= -1 and box["right"] <= width + 1 and box["bottom"] <= height + 1, boxes
        assert boxes["svg"]["h"] >= 70, boxes
    page.locator("#detailChartClose").click()
    page.set_viewport_size({"width": 412, "height": 915})
    assert_no_transport_action(native_actions(page))


def dm_two_traces_and_query_settings(page):
    key = open_motor(page, 2)
    page.evaluate("detailMotor.polling=true;detailMotor.config.pollHz=25;refreshDetail();actions=[]")
    key = page.evaluate("JSON.stringify(detailMotor.config)")
    send(page, key, dm(43, 44), 2000)
    send(page, key, dm(45, 47), 2050)
    page.evaluate("window.dmHistoryReference=detailSamples;window.dmConfigBefore=JSON.stringify(detailMotor.config);window.dmRateBefore=detailRateFor(detailMotor)")
    page.locator("#detailPlots .detail-plot").nth(3).click()
    expect(page.locator(OVERLAY)).to_be_visible()
    assert "温度" in page.locator("#detailChartTitle").inner_text()
    assert "关节" in page.locator("#detailChartMotor").inner_text()
    assert "MOS" in page.locator("#detailChartLegend").inner_text()
    assert "电机" in page.locator("#detailChartLegend").inner_text()
    assert "47" in page.locator("#detailChartValue").inner_text() and "45" in page.locator("#detailChartValue").inner_text()
    assert page.locator("#detailChartUnit").inner_text() == "°C"
    assert "L" in page.locator(TRACE).get_attribute("d")
    assert "L" in page.locator(EXTRA).get_attribute("d")
    page.evaluate("""async d => {
        const svg=$('detailChartSvg'),before=Array.from(svg.querySelectorAll('.trace,.trace-extra'),p=>p.getAttribute('d'));
        onNative('motorSample',d);
        await new Promise(requestAnimationFrame);
        const after=Array.from(svg.querySelectorAll('.trace,.trace-extra'),p=>p.getAttribute('d'));
        if(after.some((path,i)=>path===before[i])||!$('detailChartValue').textContent.includes('51')||!$('detailChartValue').textContent.includes('49'))
            throw Error('DM motor/MOS temperature traces did not both update on screen refresh');
    }""", {"key": key, "hex": sample(dm(49, 51), 2100)})
    page.locator("#detailChartClose").click()
    assert page.evaluate("detailSamples===dmHistoryReference&&detailMotor.polling&&detailMotor.config.pollHz===25&&JSON.stringify(detailMotor.config)===dmConfigBefore&&detailRateFor(detailMotor)===dmRateBefore")
    assert native_actions(page) == [["fullscreen", True], ["fullscreen", False]]
    assert page.locator("#detailQuery").is_checked()

    # Explicitly clearing a curve may discard chart history, while RX history,
    # query activation, configured frequency, and BLE selection are retained.
    page.evaluate("window.streamBeforeClear=detailRxFrames.length;actions=[]")
    open_chart(page, "temperature")
    page.locator("#detailChartClear").click()
    assert page.evaluate("detailSamples.length===0&&detailFrozen===null&&detailRxFrames.length===streamBeforeClear&&detailMotor.polling&&detailMotor.config.pollHz===25&&detailRateFor(detailMotor)===dmRateBefore")
    page.locator("#detailChartClose").click()
    assert_no_transport_action(native_actions(page))


def bounded_live_history(page):
    key = page.evaluate("JSON.stringify(detailMotor.config)")
    page.evaluate("""() => {
        detailSamples=Array.from({length:60000},(_,i)=>({t:i,values:{position:0,speed:i===30000?9999:10,effort:0,temperature:44,mosTemperature:43}}));
        detailSampleHead=0;detailClock=59999;detailLastStamp=59999;detailLastArrival=Date.now();
        $('detailWindow').value='60';window.largeHistoryReference=detailSamples;actions=[];
    }""")
    open_chart(page, "speed")
    assert page.evaluate("detailSamples===largeHistoryReference&&detailSamples.length-detailSampleHead===60000")
    for stamp in range(60000, 60005):
        send(page, key, dm(), stamp)
    assert page.evaluate("detailSamples.length-detailSampleHead") == 60000
    assert page.evaluate("detailSamples.length") <= 62048
    assert len(page.locator(TRACE).get_attribute("d")) < 100000
    page.locator("#detailChartClose").click()
    assert page.evaluate("detailSamples.length-detailSampleHead") == 60000
    assert_no_transport_action(native_actions(page))


def lifecycle_cleanup(page):
    # Stream/tab changes close only the overlay, keeping this motor selected.
    open_chart(page, "position")
    page.evaluate("showDetailTab('stream')")
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert page.locator("#detailStreamPanel").is_visible()
    assert page.evaluate("detailMotor!==null&&detailReady")
    page.evaluate("showDetailTab('charts')")

    open_chart(page, "speed")
    page.evaluate("detailRetarget(detailMotor)")
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert page.evaluate("!detailReady&&detailSamples.length===0")
    page.evaluate("onNative('telemetryMode',{mode:2,hz:100})")
    open_chart(page, "effort")
    open_motor(page, 0)
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert page.evaluate("detailMotor===watchedMotors[0]")

    open_chart(page, "position")
    page.evaluate("onNative('state',{value:'idle',message:'已断开'})")
    expect(page.locator(OVERLAY)).to_be_hidden(timeout=1000)
    assert not page.evaluate("connected")
    page.wait_for_function("detailSamples.length===0&&!detailReady")
    page.evaluate("onNative('state',{value:'ready',name:'底盘-CAN'})")
    open_motor(page, 0)
    open_chart(page, "speed")
    page.evaluate("closeMotorDetail(false)")
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert page.evaluate("detailMotor===null")
    open_motor(page, 0)
    open_chart(page, "speed")
    page.evaluate("showTab('settings')")
    expect(page.locator(OVERLAY)).to_be_hidden()
    assert page.locator("#view-settings").is_visible()
    assert page.evaluate("detailMotor===null")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")


def main():
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch( args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 412, "height": 915}, device_scale_factor=2)
        context.route("https://**/*", lambda route: route.fulfill(path=str(ASSETS / route.request.url.rsplit("/", 1)[-1])))
        context.add_init_script("""window.actions=[];window.CanAndroid={
            dashboard(){},showDetail(key){actions.push(['detail',key])},leaveDetail(){actions.push(['leave'])},
            home(){actions.push(['home'])},setPolling(...args){actions.push(['poll',...args])},
            send(hex){actions.push(['send',hex])},setTelemetry(...args){actions.push(['telemetry',...args])},
            setChartFullscreen(on){actions.push(['fullscreen',!!on])}};""")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("https://app.local/index.html")
        page.evaluate("""() => {
            onNative('state',{value:'ready',name:'底盘-CAN'});
            commitWatch({profile:'c620',id:1,name:'左轮'});
            commitWatch({profile:'c620',id:2,name:'右轮'});
            commitWatch({profile:'dm4310',id:3,name:'关节',master:0,mode:'mit',limits:{p:12.5,v:30,t:10}});
            onNative('detailSupport',{supported:true,maxHz:1000,channel:'通道 1 · 底盘-CAN'});
            window.devicePollingSupported=true;
        }""")
        live_isolation_and_preservation(page)
        pause_window_and_keyboard(page)
        responsive_landscape(page)
        dm_two_traces_and_query_settings(page)
        bounded_live_history(page)
        lifecycle_cleanup(page)
        assert not errors, errors
        browser.close()
    print("PASS: fullscreen selected-series isolation and screen-paced updates, shared pause/windows, no transport changes, keyboard/native Back, lifecycle cleanup, landscape layout, DM dual traces and bounded 60k history.")


if __name__ == "__main__":
    main()
