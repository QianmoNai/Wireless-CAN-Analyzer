// Actual shared WebView assets; mock transport, independent full-window oracle.
const {chromium}=require('playwright');
const {resolve}=require('path');
const assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined,args:['--no-sandbox','--enable-precise-memory-info']});
 try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://app.local/**',route=>route.fulfill({path:resolve(__dirname,'../android/app/src/main/assets',new URL(route.request().url()).pathname.slice(1))}));
 await page.addInitScript(()=>{
  window.CanAndroid={networkState(){},dashboard(){},leaveDetail(){},setMonitorVisible(){}};
  const interval=window.setInterval;
  window.setInterval=(fn,ms,...args)=>{
   if(String(fn).includes('lastStatusAt')&&String(fn).includes('render()')){window.renderTick=fn;return 0;}
   return interval(fn,ms,...args);
  };
 });
 await page.goto('https://app.local/index.html');
 await page.evaluate(()=>{
  onNative('state',{value:'ready',name:'Render test'});
  const p=new Uint8Array(20);p.set([1,21,1,0]);put32(p,4,921600);processPacket(hexOf(p));
  window.oracle=new Uint8Array();window.sequence=0;window.simHidden=false;
  Object.defineProperty(document,'hidden',{configurable:true,get:()=>simHidden});
  window.metrics={render:[],longtasks:[],nodesMax:0,heap:[],checks:0};
  new PerformanceObserver(list=>metrics.longtasks.push(...list.getEntries().filter(e=>e.startTime>=window.soakStarted).map(e=>e.duration))).observe({entryTypes:['longtask']});
  window.feed=(bytes,packets=false)=>{
   const next=new Uint8Array(oracle.length+bytes.length);next.set(oracle);next.set(bytes,oracle.length);oracle=next.slice(-65536);
   if(packets){for(let offset=0;offset<bytes.length;offset+=12){const part=bytes.slice(offset,offset+12),p=new Uint8Array(20);p.set([1,19,part.length]);p.set(part,4);put32(p,16,++sequence);processPacket(hexOf(p));}}
   else {const first=(sequence+1)>>>0;sequence=(sequence+Math.ceil(bytes.length/12))>>>0;uartBatch({uart:hexOf(bytes),first,last:sequence});}
  };
  window.clearLog=()=>{$('uartClear').click();oracle=new Uint8Array();sequence=0;};
  window.check=()=>{
   const expected=!oracle.length?'等待串口数据…':$('uartDisplay').value==='text'?new TextDecoder().decode(oracle):Array.from(oracle,b=>b.toString(16).padStart(2,'0').toUpperCase()).join(' ');
   if($('uartLog').textContent!==expected)throw Error(`tail mismatch: format=${$('uartDisplay').value}, bytes=${oracle.length}, check=${metrics.checks}`);
   const nodes=$('uartLog').children;
   if(nodes.length>=200||Array.from(nodes,n=>n.textContent.length).some(n=>n>1023))throw Error('unbounded layout blocks');
   metrics.nodesMax=Math.max(metrics.nodesMax,nodes.length);metrics.checks++;
  };
  window.tick=()=>{const t=performance.now();renderTick();metrics.render.push(performance.now()-t);check();};
  window.format=value=>{$('uartDisplay').value=value;$('uartDisplay').onchange();check();};
  feed(Uint8Array.from({length:100000},(_,i)=>i%256),true);tick();
  // A small append retains all complete interior nodes and their text nodes.
  const old=Array.from($('uartLog').children).slice(2,-2),text=old.map(n=>n.firstChild);
  const observer=new MutationObserver(()=>{});observer.observe($('uartLog'),{subtree:true,characterData:true});
  feed(new Uint8Array([1,2,3]));tick();
  if(observer.takeRecords().some(r=>text.includes(r.target)))throw Error('interior text rewritten');observer.disconnect();
  if(!old.every((n,i)=>n.isConnected&&n.firstChild===text[i]))throw Error('interior nodes replaced');
  // Sequence wrap/gap and UTF-8 split over physical packets and render ticks.
  clearLog();format('text');sequence=0xfffffffe;
  feed(new Uint8Array([0xe4,0xb8]),true);tick();feed(new Uint8Array([0xad,0xf0,0x9f]),true);tick();
  sequence++;feed(new Uint8Array([0x99,0x82]),true);tick();
  if(!$('uartLoss').textContent.includes('缺失 1 个'))throw Error('sequence gap lost');
  // BOM, malformed prefixes, truncated multibyte characters at every block and
  // ring boundary; render incomplete tails before completing them.
  const patterns=[[0xef,0xbb,0xbf],[0xe4,0xb8,0xad],[0xf0,0x9f,0x99,0x82],[0xe0,0x80,0x80],[0xed,0xa0,0x80],[0xf4,0x90,0x80,0x80],[0xc0,0xaf],[0xe2,0x82,0x41]];
  for(const boundary of [336,672,65536])for(const delta of [-3,-2,-1,0,1])for(const pattern of patterns){
   clearLog();feed(new Uint8Array(boundary+delta).fill(65));tick();
   for(const byte of pattern){feed(new Uint8Array([byte]),true);tick();}
  }
  // Eviction can start the retained window inside a UTF-8 character or BOM.
  for(const pattern of patterns){
   clearLog();feed(new Uint8Array([...pattern,...new Uint8Array(65536-pattern.length).fill(65)]));tick();
   for(let i=0;i<=pattern.length;i++){feed(new Uint8Array([66]));tick();}
  }
  // More than one whole window while paused, hidden by tab or document.
  for(const kind of ['pause','tab','document']){
   clearLog();feed(new TextEncoder().encode('起始🙂'));tick();const before=$('uartLog').textContent;
   if(kind==='pause')$('uartPause').click();else if(kind==='tab')showTab('settings');else simHidden=true;
   feed(Uint8Array.from({length:150000},(_,i)=>i%256));renderTick();
   if($('uartLog').textContent!==before)throw Error('hidden/paused log changed');
   $('uartDisplay').value='hex';$('uartDisplay').onchange();
   if($('uartLog').textContent!==before)throw Error('hidden format rebuilt log');
   if(kind==='pause')$('uartPause').click();else if(kind==='tab')showTab('uart');else simHidden=false;
   tick();format('text');format('hex');
  }
  // Reset plus new data before the next tick must invalidate old block IDs.
  clearLog();feed(new Uint8Array(65536).fill(42));tick();
  onNative('state',{value:'ready',name:'reset'});oracle=new Uint8Array();sequence=0;
  processPacket(hexOf(p));feed(new Uint8Array(65536).fill(43));tick();
  clearLog();format('hex');metrics.render=[];metrics.longtasks=[];metrics.heap=[];
 });
 // Real 60 s at ~60 KiB/s, 25 ms receive batches, 250 ms render cadence.
 // Every visible refresh uses the independent last-65536-byte conversion.
 // Let observer entries from the synchronous boundary tests drain first.
 await page.waitForTimeout(100);
 const result=await page.evaluate(()=>new Promise((resolve,reject)=>{
  metrics.longtasks=[];window.soakStarted=performance.now();
  let received=0,ticks=0;const started=performance.now();
  const pattern=new TextEncoder().encode('\ufeff中文🙂\r\ninvalid:');
  const feeder=setInterval(()=>{
   const bytes=Uint8Array.from({length:1536},(_,i)=>{
    const pos=received+i;return pos%71===0?0xff:pattern[pos%pattern.length];
   });received+=bytes.length;feed(bytes);
  },25);
  const renderer=setInterval(()=>{
   try{
    ticks++;
    if(ticks===40)$('uartPause').click();
    if(ticks===48)$('uartPause').click();
    if(ticks===80)showTab('settings');
    if(ticks===88)showTab('uart');
    if(ticks===120)simHidden=true;
    if(ticks===128)simHidden=false;
    if(ticks%20===0){$('uartDisplay').value=$('uartDisplay').value==='hex'?'text':'hex';$('uartDisplay').onchange();}
    const visible=ticks<40||(ticks>=48&&ticks<80)||(ticks>=88&&ticks<120)||ticks>=128;
    if(visible)tick();else renderTick();
    if(performance.memory)metrics.heap.push(performance.memory.usedJSHeapSize);
    if(performance.now()-started>=60000){
     clearInterval(feeder);clearInterval(renderer);simHidden=false;showTab('uart');tick();
     resolve({...metrics,durationMs:performance.now()-started,received,domNodes:$('uartLog').querySelectorAll('*').length});
    }
   }catch(e){clearInterval(feeder);clearInterval(renderer);reject(e);}
  },250);
 }));
 const cdp=await page.context().newCDPSession(page);const heap=await cdp.send('Runtime.getHeapUsage');
 const distribution=values=>{const sorted=[...values].sort((a,b)=>a-b);return {count:sorted.length,p95:sorted[Math.floor((sorted.length-1)*.95)]||0,max:sorted.at(-1)||0};};
 assert(result.durationMs>=60000);assert(result.checks>500);assert(result.nodesMax<200);assert(heap.usedSize<128*1024*1024,'bounded JS heap');assert.deepEqual(errors,[]);
 console.log(JSON.stringify({scope:'shared JS Chromium simulation, not Android hardware',duration_ms:result.durationMs,bytes:result.received,strict_tail_checks:result.checks,render_ms:distribution(result.render),longtasks_ms:distribution(result.longtasks),dom_nodes_max:result.nodesMax,heap_samples_bytes:distribution(result.heap),heap_final:heap}));
 console.log('PASS: 60 s exact latest 64 KiB; stable nodes; UTF-8 packet/block/ring boundaries, invalid/BOM/truncated input; pause/tab/document resume; format/reset/sequence transitions');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
