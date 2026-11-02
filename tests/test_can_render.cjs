const {chromium}=require('playwright');const {resolve}=require('path');const assert=require('assert/strict');
(async()=>{const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']});try{
 const page=await browser.newPage();
 await page.route('https://app.local/**',route=>route.fulfill({path:resolve(__dirname,'../android/app/src/main/assets',new URL(route.request().url()).pathname.slice(1))}));
 await page.addInitScript(()=>{window.CanAndroid={networkState(){},dashboard(){},leaveDetail(){},setMonitorVisible(){}}});await page.goto('https://app.local/index.html');
 await page.evaluate(()=>{
  onNative('state',{value:'ready',name:'CAN render test'});showTab('live');window.seq=0;
  window.feedCan=(n,id)=>{for(let i=0;i<n;i++){const p=new Uint8Array(20);p.set([1,1,0,8]);put32(p,4,id);put32(p,16,++seq);processPacket(hexOf(p));}};
  Date.prototype.toLocaleTimeString=()=>{throw Error('Per-row formatter allocation')};feedCan(100,0x123);
 });
 await page.waitForTimeout(250);assert.equal(await page.locator('#frames .frame').count(),80);
 assert.equal(await page.locator('#frames .can-id').first().textContent(),'0x123');
 await page.evaluate(()=>window.firstRow=$('frames').firstElementChild);
 const before=await page.locator('#frames').textContent();
 await page.evaluate(()=>{showTab('settings');feedCan(100,0x456)});await page.waitForTimeout(250);assert.equal(await page.locator('#frames').textContent(),before);
 await page.evaluate(()=>showTab('live'));await page.waitForTimeout(250);assert.equal(await page.locator('#frames .can-id').first().textContent(),'0x456');
 await page.evaluate(()=>togglePause());const frozen=await page.locator('#frames').textContent();
 await page.evaluate(()=>feedCan(100,0x789));await page.waitForTimeout(250);assert.equal(await page.locator('#frames').textContent(),frozen);
 await page.evaluate(()=>togglePause());assert.equal(await page.locator('#frames .can-id').first().textContent(),'0x789');
 assert(await page.evaluate(()=>firstRow===$('frames').firstElementChild));
 await page.evaluate(()=>{const p=new Uint8Array(20);p.set([1,3,4,0]);put32(p,4,0x321);processPacket(hexOf(p));selectFrameFilter('fail')});
 assert((await page.locator('#frames .failure-reason').first().textContent()).includes('ACK'));
 assert(await page.locator('#frames details').first().isVisible());
 await page.locator('#frames summary').first().click();assert((await page.locator('#frames .bytes').first().textContent()).includes('零长度'));
 await page.evaluate(()=>{clearFrames();selectFrameFilter('all');feedCan(1,0x111);renderFrames()});
 assert.equal(await page.locator('#frames .frame').count(),1);
 assert(await page.locator('#frames details').first().isHidden());
 console.log('PASS: cached time formatter, 80 visible rows, hidden view, pause snapshot and resume');
 }finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
