"""Burst handling, screen-paced painting, stale receipt times and home coalescing."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'android/app/src/main/assets'
with sync_playwright() as p:
    browser=p.chromium.launch(args=['--no-sandbox'])
    ctx=browser.new_context(viewport={'width':412,'height':915})
    ctx.route('https://**/*',lambda route:route.fulfill(path=str(ASSETS/route.request.url.rsplit('/',1)[-1])))
    ctx.add_init_script('window.sent=[];window.snapshots=[];window.priority=[];window.CanAndroid={send(h){sent.push(h)},dashboard(s){snapshots.push(JSON.parse(s))},showDetail(){},setMonitorVisible(v){priority.push(v)}};')
    page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.goto('https://channel.local/index.html')
    result=page.evaluate("""async()=>{
        onNative('state',{value:'ready',name:'Bench'});commitWatch({profile:'c620',id:1});dashboardOpen(JSON.stringify(watchedMotors[0].config));
        onNative('detailSupport',{supported:true,maxHz:1000});onNative('telemetryMode',{mode:2,hz:1000});
        let draws=0;const original=drawDetail;drawDetail=()=>{draws++;original()};
        const items=[];for(let i=0;i<1000;i++){const a=new Uint8Array(20);a[0]=1;a[1]=11;a[3]=8;a[14]=i===500?99:35;put32(a,16,i);items.push({hex:hexOf(a),age:0})}
        onNative('motorSamples',{key:JSON.stringify(detailMotor.config),items,dropped:7,sentAt:Date.now()});
        if(draws!==0||detailSamples.length!==1000||detailAppCount!==1000||appDrops!==7)throw Error('batch lost samples or painted per frame');
        await new Promise(requestAnimationFrame);
        if(draws!==1||!detailSamples.some(p=>p.values.temperature===99)||detailMotor.feedback.values.temperature!==35)throw Error('screen refresh lost peak/latest sample');
        drawDetail=original;return {samples:detailSamples.length,draws};
    }""")
    assert result=={'samples':1000,'draws':1},result
    page.evaluate("""async()=>{
        let draws=0;const original=drawDetail;drawDetail=()=>{draws++;original()};
        Object.defineProperty(document,'hidden',{value:true,configurable:true});document.dispatchEvent(new Event('visibilitychange'));
        const a=new Uint8Array(20);a[0]=1;a[1]=11;a[3]=8;a[14]=44;put32(a,16,1000);
        onNative('motorSamples',{key:JSON.stringify(detailMotor.config),items:[{hex:hexOf(a),age:0}]});
        await new Promise(requestAnimationFrame);
        if(draws!==0||detailSamples.length!==1001)throw Error('hidden detail painted or dropped accepted data');
        delete document.hidden;document.dispatchEvent(new Event('visibilitychange'));await new Promise(requestAnimationFrame);
        if(draws!==1||detailMotor.feedback.values.temperature!==44)throw Error('visible detail did not restore latest reading');
        drawDetail=original;
    }""")
    page.evaluate("""async()=>{
        const a=new Uint8Array(20);a[0]=1;a[1]=11;a[3]=8;a[14]=40;put32(a,16,1001);
        onNative('motorSamples',{key:JSON.stringify(detailMotor.config),items:[{hex:hexOf(a),age:3000}],sentAt:Date.now()-1500});
        await new Promise(requestAnimationFrame);
        if(!document.querySelector('#detailState').textContent.includes('超时')||Date.now()-detailMotor.time<4500)throw Error('queued sample appeared fresh');
        const key=JSON.stringify(detailMotor.config);
        onNative('motorRates',{key,received:1000,sampled:1000,elapsed:1000,age:3000,sentAt:Date.now()-1500});
        onNative('motorRates',{key,received:2000,sampled:2000,elapsed:2000,age:3000,sentAt:Date.now()-1500});
        await new Promise(requestAnimationFrame);
        if(document.querySelector('#detailCanRate').textContent!=='— Hz')throw Error('queued detail rate appeared fresh');
        showTab('live');onNative('telemetryMode',{mode:0,quiet:false});
        const load=new Uint8Array(20);load[0]=1;load[1]=13;load[2]=50;put32(load,12,1000000);put32(load,16,1000000);
        onNative('busLoad',{hex:hexOf(load),age:3000,sentAt:Date.now()-1500});
        const rate=load.slice();rate[1]=14;rate[2]=0;put32(rate,4,1000);put32(rate,8,50);
        onNative('busRates',{hex:hexOf(rate),age:3000,sentAt:Date.now()-1500});
        if(document.querySelector('#busLoad').textContent!=='—'||document.querySelector('#rxCount').textContent!=='—')throw Error('queued bus statistics appeared fresh');
        showTab('motor');
    }""")
    assert page.evaluate('priority.slice(-2)')==[True,False]
    # Complete or partial temperature cycles publish once within a bounded window.
    roster=ctx.new_page();roster.on('pageerror',lambda e:errors.append(str(e)));roster.goto('https://roster.local/index.html')
    roster.evaluate("""()=>{
        onNative('state',{value:'ready',name:'Bench'});
        for(let id=1;id<=32;id++)commitWatch({profile:'dm4310',id,master:id,mode:'mit',limits:{p:12.5,v:30,t:10}});
        onNative('telemetryMode',{quiet:true,mode:1});snapshots=[];
        onNative('onlineSummary',{count:32,online:0xffffffff,seen:0xffffffff});
        for(let start=0;start<32;start+=10)onNative('motorTemperatures',{start,count:32,temperatures:Array(Math.min(10,32-start)).fill(35)});
        if(snapshots.length!==0)throw Error('summary burst published immediately');
    }""")
    roster.wait_for_function('snapshots.length===1')
    assert roster.evaluate('snapshots[0].every(m=>m.temperature===35&&m.connectionStatus==="online")')
    roster.evaluate("snapshots=[];onNative('onlineSummary',{count:32,online:1,seen:0xffffffff});onNative('motorTemperatures',{start:0,count:32,temperatures:[42,null,null,null,null,null,null,null,null,null]})")
    roster.wait_for_function('snapshots.length===1')
    assert roster.evaluate('snapshots[0][0].temperature===42&&snapshots[0][1].connectionStatus==="offline"')
    # No full-card mutations when an identical home snapshot arrives again.
    home=ctx.new_page();home.on('pageerror',lambda e:errors.append(str(e)));home.goto('https://home.local/home.html')
    data=[{'slot':1,'name':'Bench','connected':True,'label':'已连接','age':0,'motors':roster.evaluate('dashboardSnapshot()')}]
    home.evaluate('updateHome',data)
    mutations=home.evaluate("""async data=>{
        const changes=[];const observer=new MutationObserver(records=>changes.push(...records));observer.observe(document.querySelector('#cards'),{subtree:true,childList:true,characterData:true,attributes:true});
        updateHome(data);await Promise.resolve();observer.disconnect();return changes.length;
    }""",data)
    assert mutations==0,mutations
    assert not errors,errors
    assert page.evaluate('sent.length')==0 and roster.evaluate('sent.length')==0
    browser.close()
print('PASS: 1000-sample burst -> one screen paint, retained peak/latest sample, frame counts, receipt age, monitor priority hints, bounded complete/partial home cycles and unchanged-card DOM reuse.')
