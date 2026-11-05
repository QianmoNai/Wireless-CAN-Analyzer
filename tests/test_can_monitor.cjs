// Shared production page with synthetic histories; no physical CAN claim.
const {chromium}=require('playwright');
const {resolve}=require('path');
const assert=require('node:assert/strict');
const assets=resolve(__dirname,'../android/app/src/main/assets');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:412,height:915}});
  await context.route('https://app.local/**',route=>route.fulfill({path:resolve(assets,new URL(route.request().url()).pathname.slice(1))}));
  await context.addInitScript(()=>{window.writes=[];window.CanAndroid={send(p){writes.push(p)},setMonitorVisible(){}}});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('https://app.local/index.html');
  await page.evaluate(()=>{
   connected=true;showTab('live');$('rxLimit').value=10000;$('txLimit').value=10000;applyHistoryLimits();
   frames=Array.from({length:20000},(_,i)=>({kind:i%2?1:i%4?3:2,id:i%2?0x201:i%4?0x7ff:0x202,flags:i%4===2?4:0,seq:i,dlc:0,data:[]}));renderFrames();
  });
  const view=async(count,visible)=>{
   assert.equal(await page.locator('#frameCount').textContent(),count+' 条');
   assert.equal(await page.locator('.frame').count(),visible);
  };
  await view(20000,80);
  await page.click('#rxFilter');await view(10000,80);
  await page.fill('#filter','202');await view(0,0);
  await page.click('#allFrames');await view(5000,80);
  await page.fill('#filter','0x7ff');await view(5000,80);
  await page.click('#failureFilter');await view(5000,80);
  assert.equal(await page.locator('.failure-reason').first().textContent(),'未收到应答（ACK）');
  // Receive after the last paint, then Pause synchronously: freeze the visible
  // snapshot, even when the full RX ring has already evicted an old record.
  assert(await page.evaluate(()=>{
   $('filter').value='';selectFrameFilter('all');
   const shown=displayedFrameHistory.map(f=>f.seq);
   frameHistory.push({kind:1,id:0x555,seq:20000,dlc:0,data:[]});togglePause();
   return JSON.stringify(frozenFrameHistory.map(f=>f.seq))===JSON.stringify(shown);
  }));
  await page.fill('#filter','555');await view(0,0);
  await page.click('#pauseButton');await view(1,1);
  assert.equal(await page.locator('.can-id').textContent(),'0x555');
  // Retention changes constrain both the live rings and paused snapshots.
  await page.click('#pauseButton');
  await page.evaluate(()=>{$('rxLimit').value=0;$('txLimit').value=1;applyHistoryLimits()});
  await view(0,0);await page.fill('#filter','');await view(1,1);
  await page.evaluate(()=>{$('txLimit').value=0;applyHistoryLimits()});await view(0,0);
  assert.equal(await page.evaluate(()=>writes.length),0);
  assert.deepEqual(errors,[]);
  console.log('CAN monitor: 20000 retained records, complete direction/ID counts, 80 rows, frozen display across ring eviction, resume/zero limits and no command writes PASS');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
