from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,args=['--no-sandbox'])
 page=b.new_page(viewport={'width':412,'height':915})
 page.add_init_script('window.sent=[];window.CanAndroid={send(h){sent.push(h)}}')
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto((ROOT/'android/app/src/main/assets/index.html').as_uri())
 page.evaluate("window.disconnects=0;CanAndroid.disconnect=()=>disconnects++;onNative('state',{value:'connecting'})")
 assert page.locator('#connectButton').inner_text()=='取消连接'
 page.locator('#connectButton').click()
 assert page.evaluate('disconnects')==1
 page.evaluate("onNative('state',{value:'idle'})")
 assert page.locator('#connectButton').inner_text()=='连接设备'
 page.evaluate("""() => {
 connected=true;bridgeBitrate=1000000;
 $('rxLimit').value='2';$('txLimit').value='3';applyHistoryLimits();
 frames=[{kind:1,id:10},{kind:2,id:11},{kind:1,id:12},{kind:3,id:13},{kind:1,id:14},{kind:2,id:15},{kind:2,id:16}];
 trimHistory();
 }""")
 assert page.evaluate('frames.map(f=>f.id)')==[10,11,12,13,15]
 page.evaluate("$('rxLimit').value='0';$('txLimit').value='1';applyHistoryLimits()")
 assert page.evaluate('frames.map(f=>f.id)')==[11]
 page.evaluate("$('rxLimit').value='-1';applyHistoryLimits()")
 assert '整数' in page.locator('#historyResult').inner_text()
 assert page.evaluate('rxSaveLimit')==0
 page.reload()
 assert page.locator('#rxLimit').input_value()=='0' and page.locator('#txLimit').input_value()=='1'
 page.evaluate("""() => {
 connected=true;bridgeBitrate=1000000;resetMonitorMetrics();
 window.ratesPacket=(rx,tx,windowUs=1000000,bitrate=1000000)=>{const a=new Uint8Array(20);a[0]=1;a[1]=14;put32(a,4,rx);put32(a,8,tx);put32(a,12,windowUs);put32(a,16,bitrate);return a;};
 onNative('busRates',{hex:hexOf(ratesPacket(1000,500))});
 }""")
 assert 990<float(page.locator('#rxCount').inner_text().replace(',',''))<1010
 assert 490<float(page.locator('#txCount').inner_text().replace(',',''))<510
 # Device load is accepted with no raw frames, no type-4 status and no config.
 # 100 RX frames and 10 successful query TX frames occupy 12210 estimated bits.
 page.evaluate("""() => {
 connected=true;resetMonitorMetrics();bridgeBitrate=0;
 window.loadPacket=new Uint8Array(20);loadPacket[0]=1;loadPacket[1]=13;
 const pct=122;loadPacket[2]=pct&255;loadPacket[3]=pct>>8;
 put32(loadPacket,4,11100);put32(loadPacket,8,1110);put32(loadPacket,12,1000000);put32(loadPacket,16,1000000);
 onNative('busLoad',{hex:hexOf(loadPacket)});updateMonitor();
 }""")
 assert page.locator('#busLoad').inner_text()=='≈ 1.22%'
 assert page.evaluate('frames.length')==0
 assert page.locator('#rxCount').inner_text()=='—'
 assert page.locator('#loadBar').evaluate("el=>el.style.width")=='1.22%'
 assert '包含持续查询' in page.locator('#loadHint').inner_text()
 # App drops must update without waiting for a type-4 device status. Detail
 # delivery can suppress that status, including batches with stale config.
 page.evaluate("onNative('motorSamples',{dropped:23,key:'stale config',items:[]})")
 assert page.locator('#dropStatus').inner_text().endswith('App 23')
 assert '设备累计丢弃 0' in page.locator('#dropStatus').get_attribute('aria-label')
 assert '不含 CAN 错误计数' in page.locator('#dropStatus').get_attribute('title')
 assert 'App 丢弃 23' in page.locator('#lossNote').inner_text()
 page.evaluate("onNative('packets',{dropped:24,items:[]})")
 assert page.locator('#dropStatus').inner_text().endswith('App 24')
 page.evaluate("onNative('motorSamples',{dropped:0,key:'stale config',items:[]})")
 assert page.locator('#dropStatus').inner_text().endswith('App 0')
 page.evaluate("window.dropLabelChanges=0;window.dropObserver=new MutationObserver(rows=>{dropLabelChanges+=rows.length});dropObserver.observe($('dropStatus'),{childList:true});for(let i=0;i<1000;i++)onNative('motorSamples',{dropped:0,key:'stale config',items:[]})")
 assert page.evaluate('dropLabelChanges')==0
 page.evaluate('dropObserver.disconnect()')
 # Raw traffic, dropped telemetry and type-4 batching cannot change device load.
 page.evaluate("""() => {
 onNative('packets',{dropped:900,items:['0101000801020000010203040506070800000000']});
 const a=new Uint8Array(20);a[0]=1;a[1]=4;put32(a,16,1200);processPacket(hexOf(a));
 updateMonitor();
 }""")
 assert page.locator('#busLoad').inner_text()=='≈ 1.22%'
 assert 'BLE 监控丢帧不会改变设备统计' in page.locator('#loadHint').inner_text()
 # Reject corrupt/unsupported windows without replacing the last valid sample.
 page.evaluate("""() => {
 for(const [offset,value] of [[12,0],[16,0],[16,333333]]){
 const a=loadPacket.slice();put32(a,offset,value);processPacket(hexOf(a));
 }
 const over=loadPacket.slice();over[2]=17;over[3]=39;processPacket(hexOf(over));
 const badVersion=loadPacket.slice();badVersion[0]=2;processPacket(hexOf(badVersion));
 processPacket(hexOf(loadPacket).slice(2));updateMonitor();
 }""")
 assert page.locator('#busLoad').inner_text()=='≈ 1.22%'
 assert page.evaluate('deviceLoadSample.windowUs')==1000000
 assert page.evaluate('deviceLoadSample.bitrate')==1000000
 # Load has its own freshness; stale frame-rate statistics do not hide it.
 page.evaluate('rateSample={rx:0,tx:0,time:performance.now()-5000};updateMonitor()')
 assert page.locator('#rxCount').inner_text()=='—' and page.locator('#busLoad').inner_text()=='≈ 1.22%'
 page.evaluate('deviceLoadSample.time-=5000;updateMonitor()')
 assert page.locator('#busLoad').inner_text()=='—'
 assert page.locator('#loadBar').evaluate("el=>el.style.width")=='0%'
 assert '超时' in page.locator('#loadHint').inner_text()
 # Quiet/home/detail/other channels clear caches and cannot reuse load windows.
 page.evaluate("onNative('telemetryMode',{mode:1});processPacket(hexOf(loadPacket))")
 assert page.evaluate('deviceLoadSample') is None and page.locator('#busLoad').inner_text()=='—'
 page.evaluate("onNative('telemetryMode',{mode:2});processPacket(hexOf(loadPacket))")
 assert page.evaluate('deviceLoadSample') is None
 page.evaluate("onNative('telemetryMode',{mode:3});processPacket(hexOf(loadPacket))")
 assert page.evaluate('deviceLoadSample') is None
 page.evaluate("onNative('telemetryMode',{mode:0});processPacket(hexOf(loadPacket));onNative('busLoadReset',{})")
 assert page.evaluate('deviceLoadSample') is None
 # Reconnection and older firmware cannot fall back to BLE-derived estimation.
 page.evaluate("processPacket(hexOf(loadPacket));onNative('state',{value:'idle',message:'断线'});processPacket(hexOf(loadPacket))")
 assert page.locator('#busLoad').inner_text()=='—' and page.evaluate('deviceLoadSample') is None
 page.evaluate("""() => {
 onNative('state',{value:'ready',name:'旧固件'});
 const cfg=new Uint8Array(20);cfg[0]=1;cfg[1]=5;put32(cfg,4,1000000);processPacket(hexOf(cfg));
 for(let i=0;i<100;i++)processPacket('0101000801020000010203040506070800000000');
 updateMonitor();
 }""")
 assert page.locator('#busLoad').inner_text()=='—'
 assert '旧固件需更新' in page.locator('#loadHint').inner_text()
 # A reported zero or 100 percent is valid; never clamp invalid percentages.
 page.evaluate('loadPacket[2]=loadPacket[3]=0;processPacket(hexOf(loadPacket))')
 assert page.locator('#busLoad').inner_text()=='≈ 0.00%'
 page.evaluate('loadPacket[2]=16;loadPacket[3]=39;processPacket(hexOf(loadPacket))')
 assert page.locator('#busLoad').inner_text()=='≈ 100.00%'
 # Switching bitrate invalidates the previous window until a new device sample.
 page.evaluate("""() => {const a=new Uint8Array(20);a[0]=1;a[1]=5;put32(a,4,500000);processPacket(hexOf(a))}""")
 assert page.locator('#busLoad').inner_text()=='—'
 page.evaluate('put32(loadPacket,16,500000);processPacket(hexOf(loadPacket))')
 assert page.locator('#busLoad').inner_text()=='≈ 100.00%'
 # Burst arrivals retain the device rate rather than turning 1000 Hz into ~7000 Hz.
 page.evaluate("onNative('busRates',{hex:hexOf(ratesPacket(1000,100))});rateSample.time-=143;onNative('busRates',{hex:hexOf(ratesPacket(1000,100))})")
 assert page.locator('#rxCount').inner_text()=='1,000' and page.locator('#txCount').inner_text()=='100'
 page.evaluate("onNative('busRates',{hex:hexOf(ratesPacket(2500,250,2500000))})")
 assert page.locator('#rxCount').inner_text()=='1,000'
 # Type4 counters, lost/duplicated raw frames, and list filters cannot change rates.
 page.evaluate("const old=new Uint8Array(20);old[0]=1;old[1]=4;put32(old,4,700000);put32(old,8,900000);processPacket(hexOf(old))")
 assert page.locator('#rxCount').inner_text()=='1,000'
 for window,bitrate,count in [(0,1000000,1000),(999999,1000000,1000),(1000000,333333,1000),(1000000,1000000,0xffffffff)]:
  page.evaluate('d=>processPacket(hexOf(ratesPacket(d[2],1,d[0],d[1])))',[window,bitrate,count])
  assert page.locator('#rxCount').inner_text()=='1,000'
 page.evaluate("onNative('busRates',{hex:hexOf(ratesPacket(0,0))})")
 assert page.locator('#rxCount').inner_text()=='0'
 page.evaluate('rateSample.time-=5000;updateMonitor()')
 assert page.locator('#rxCount').inner_text()=='—' and '超时' in page.locator('#rateHint').inner_text()
 for mode in (1,2,3):
  page.evaluate("m=>{onNative('telemetryMode',{mode:m});processPacket(hexOf(ratesPacket(1000,100)));}",mode)
  assert page.locator('#rxCount').inner_text()=='—'
 page.evaluate("onNative('telemetryMode',{mode:0});processPacket(hexOf(ratesPacket(1000,100)));onNative('busLoadReset',{})")
 assert page.locator('#rxCount').inner_text()=='—'
 page.evaluate("onNative('state',{value:'idle'});processPacket(hexOf(ratesPacket(1000,100)))")
 assert page.locator('#rxCount').inner_text()=='—'
 page.evaluate("onNative('state',{value:'ready',name:'旧固件'});const old=new Uint8Array(20);old[0]=1;old[1]=4;put32(old,4,700000);processPacket(hexOf(old));updateMonitor()")
 assert page.locator('#rxCount').inner_text()=='—' and '固件' in page.locator('#rateHint').inner_text()
 # New firmware separates pure arbitration from real failures; do not expose the
 # internal arbitration count from config events or legacy marked TX records.
 page.evaluate("""() => {connected=true;resetMonitorMetrics();const cfg=new Uint8Array(20);cfg[0]=1;cfg[1]=5;cfg[3]=63;put32(cfg,4,1000000);put32(cfg,8,12345);processPacket(hexOf(cfg));const status=new Uint8Array(20);status[0]=1;status[1]=4;put32(status,12,0);processPacket(hexOf(status));window.statsPacket=status;window.cfgPacket=cfg;}""")
 assert page.locator('#errorCount').inner_text()=='0'
 assert not page.locator('#errorCount').evaluate("el=>el.classList.contains('red')")
 page.evaluate("put32(statsPacket,12,2);processPacket(hexOf(statsPacket))")
 assert page.locator('#errorCount').inner_text()=='2'
 assert page.locator('#errorCount').evaluate("el=>el.classList.contains('red')")
 page.evaluate("cfgPacket[3]=31;processPacket(hexOf(cfgPacket))")
 assert page.locator('#errorCount').inner_text()=='需更新'
 page.evaluate("cfgPacket[3]=63;processPacket(hexOf(cfgPacket))")
 assert page.locator('#errorCount').inner_text()=='2'
 page.evaluate("frames=[];processPacket('0103800401070000000000000000000000000000')")
 assert page.evaluate('frames.length')==0
 page.evaluate("processPacket('0103000401070000000000000000000000000000')")
 assert page.evaluate('frames.length')==1
 page.evaluate('resetMonitorMetrics()')
 assert page.locator('#errorCount').inner_text()=='—'
 # Real firmware query completion events are searchable TX frames, not synthetic counters.
 page.evaluate("""() => {
 frames=[];paused=false;$('rxLimit').value=300;$('txLimit').value=3;applyHistoryLimits();showTab('live');
 const tx=new Uint8Array(20);tx[0]=1;tx[1]=2;tx[3]=4;put32(tx,4,0x7ff);tx[8]=1;tx[10]=0xcc;
 const failed=tx.slice();failed[1]=3;failed[8]=0x23;failed[9]=1;
 const arbitration=tx.slice();arbitration[1]=3;arbitration[2]=0x80;
 onNative('packets',{dropped:0,items:[hexOf(tx),hexOf(failed),hexOf(arbitration)]});renderFrames();
 }""")
 for query in ('7FF','7ff','0x7FF'):
  page.locator('#filter').fill(query)
  assert page.locator('.frame').count()==2
  assert page.locator('.can-id').all_text_contents()==['0x7FF','0x7FF']
  assert page.locator('.direction').all_text_contents()==['! FAIL','↗ TX']
  assert page.locator('.frame').nth(1).locator('.byte').all_text_contents()==['01','00','CC','00']
  assert page.locator('.frame').first.locator('.byte').all_text_contents()==['23','01','CC','00']
 page.evaluate("$('txLimit').value=1;applyHistoryLimits()")
 assert page.locator('.frame').count()==1
 page.evaluate("$('txLimit').value=0;applyHistoryLimits()")
 assert page.locator('.frame').count()==0
 # Statistic cards select a direction; the failure card replaces payload rows with reasons.
 page.evaluate("""() => {
 clearFrames();$('filter').value='';$('rxLimit').value=30;$('txLimit').value=30;applyHistoryLimits();
 window.emitTest=(kind,id,flags=0)=>{const a=new Uint8Array(20);a[0]=1;a[1]=kind;a[2]=flags;a[3]=4;put32(a,4,id);a[8]=1;a[10]=0xcc;processPacket(hexOf(a));};
 emitTest(1,0x201);emitTest(1,0x7ff);emitTest(2,0x123);emitTest(2,0x7ff);
 emitTest(3,0x7ff,0x7c);emitTest(3,0x7ff);emitTest(3,0x123,0x40);emitTest(3,0x7ff,0x84);emitTest(3,0x7ff,0x80);
 renderFrames();
 }""")
 assert page.evaluate('frames.length')==8
 baseline=page.evaluate('frames.map(f=>[f.kind,f.id,f.flags])')
 page.locator('#rxFilter').click()
 assert page.locator('.direction').all_text_contents()==['↙ RX','↙ RX']
 assert page.locator('#frameViewTitle').inner_text()=='接收 RX 数据流'
 page.locator('#filter').fill('7FF');assert page.locator('.frame').count()==1
 page.locator('#txFilter').focus();page.keyboard.press('Space')
 assert page.locator('#txFilter').get_attribute('aria-pressed')=='true'
 assert page.locator('.direction').all_text_contents()==['↗ TX']
 page.locator('#failureFilter').click()
 assert page.locator('#frameViewTitle').inner_text()=='失败原因'
 assert page.locator('#failureNote').is_visible()
 assert page.locator('.frame').count()==3
 reasons=page.locator('.failure-reason').all_text_contents()
 assert reasons[0]=='未收到应答（ACK）' and reasons[1]=='设备未提供具体原因'
 assert all(label in reasons[2] for label in ['未收到应答（ACK）','位错误','格式错误','位填充错误','驱动拒绝发送'])
 assert page.locator('.failure-data').count()==3
 assert page.locator('.byte:visible').count()==0
 page.locator('.failure-data summary').first.click()
 assert page.locator('.byte:visible').all_text_contents()==['01','00','CC','00']
 page.locator('#filter').fill('123');assert page.locator('.failure-reason').all_text_contents()==['驱动拒绝发送']
 page.locator('#filter').fill('');page.locator('#allFrames').click()
 assert page.locator('.frame').count()==8 and not page.locator('#failureNote').is_visible()
 assert page.evaluate('frames.map(f=>[f.kind,f.id,f.flags])')==baseline
 assert page.locator('[data-frame-filter][aria-pressed="true"]').count()==1
 page.locator('#rxFilter').focus();page.keyboard.press('Enter')
 assert page.locator('#rxFilter').get_attribute('aria-pressed')=='true'
 # Pausing freezes displayed history for direction/ID filtering while live counters continue.
 page.locator('#allFrames').click();page.locator('#pauseButton').click()
 page.evaluate("emitTest(1,0x777);emitTest(2,0x777);renderFrames()")
 assert page.evaluate('frames.length')==10 and page.locator('.frame').count()==8
 page.locator('#txFilter').click();assert page.locator('.frame').count()==2
 page.locator('#filter').fill('777');assert page.locator('.frame').count()==0
 page.locator('#pauseButton').click();assert page.locator('.frame').count()==1
 page.locator('#filter').fill('');page.locator('#pauseButton').click()
 page.evaluate("$('txLimit').value=0;applyHistoryLimits()")
 assert page.locator('.frame').count()==0
 page.locator('#failureFilter').click();assert page.locator('.frame').count()==0
 page.locator('#rxFilter').click();assert page.locator('.frame').count()==3
 page.evaluate("rxSaveLimit=0;trimHistory();renderFrames()")
 assert page.locator('.frame').count()==0,'Detail-page RX limit changes also constrain paused snapshots'
 page.evaluate("window.cleanTxCounters=true;cleanFailureCount=99;renderFailureCount();clearFrames()")
 page.locator('#failureFilter').click()
 assert page.locator('#errorCount').inner_text()=='99' and page.locator('.frame').count()==0
 assert '累计' in page.locator('#frames').inner_text()
 page.locator('#pauseButton').click();assert not page.evaluate('paused')
 page.evaluate("$('txLimit').value=30;applyHistoryLimits();emitTest(3,0x7ff,0x7c);emitTest(3,0x123);renderFrames();$('toast').hidden=true")
 for width in (320,412,820):
  page.set_viewport_size({'width':width,'height':915})
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert page.locator('[data-frame-filter][aria-pressed="true"]').count()==1
 page.set_viewport_size({'width':412,'height':915})
 page.screenshot(path=str(ROOT/'android/dist/preview-failure-filter.png'),full_page=True)
 # Full channel view has no motor-ID restriction; ID search is display-only.
 page.evaluate("clearFrames();$('rxLimit').value=30;applyHistoryLimits();[0x201,0x202,0x205,0x7ff].forEach(id=>emitTest(1,id));selectFrameFilter('rx')")
 assert page.locator('.can-id').all_text_contents()==['0x7FF','0x205','0x202','0x201']
 page.locator('#filter').fill('202');assert page.locator('.can-id').all_text_contents()==['0x202']
 page.locator('#filter').fill('');assert page.locator('.frame').count()==4
 before=page.evaluate('JSON.stringify(watchedMotors.map(m=>m.config))')
 page.evaluate("showTab('motor');openWatchEditor();$('watchName').value='discarded draft';$('deviceModal').hidden=false")
 page.keyboard.press('Escape')
 assert page.locator('#deviceModal').is_hidden()
 assert page.locator('#watchEditor').is_visible(),'First Back/Escape closes the device picker only'
 page.keyboard.press('Escape')
 assert page.locator('#watchEditor').is_hidden()
 assert page.evaluate('JSON.stringify(watchedMotors.map(m=>m.config))')==before,'Cancel must not save a draft'
 assert page.evaluate('connected&&consumeChannelBack()===false'),'No transient UI: native Back can return home'
 page.evaluate("openWatchEditor();showTab('live')")
 assert page.evaluate("consumeChannelBack()===false&&!$('watchEditor').hidden"),'An editor in an inactive tab must not consume Back'
 page.evaluate('closeWatchEditor()')
 assert page.evaluate('sent.length')==0,'Filtering, pause and history limits cannot send CAN/BLE commands'
 assert not errors,errors
 b.close()
print('PASS: RX/TX/failure card views, ID filters, actual/unknown/multiple failure reasons, pure-arbitration exclusion, keyboard, paused snapshots and retention/clear, 320/412/820px, no sends; 7FF payloads, counters, CAN load and legacy regressions.')
