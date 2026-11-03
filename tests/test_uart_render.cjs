const {chromium}=require('playwright');
const {resolve}=require('path');
const assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 try{
 const page=await browser.newPage();
 await page.route('https://app.local/**',route=>route.fulfill({path:resolve(__dirname,'../android/app/src/main/assets',new URL(route.request().url()).pathname.slice(1))}));
 await page.addInitScript(()=>{window.CanAndroid={networkState(){},dashboard(){},leaveDetail(){},setMonitorVisible(){}}});
 await page.goto('https://app.local/index.html');
 await page.evaluate(()=>{
  onNative('state',{value:'ready',name:'Render test'});
  window.total=0;window.sequence=0;
  window.feed=n=>{for(let offset=0;offset<n;){const size=Math.min(12,n-offset),p=new Uint8Array(20);p.set([1,19,size]);for(let i=0;i<size;i++)p[4+i]=(total++)%256;put32(p,16,++sequence);processPacket(hexOf(p));offset+=size;}};
  const p=new Uint8Array(20);p.set([1,21,1,0]);put32(p,4,115200);processPacket(hexOf(p));feed(100000);
 });
 await page.waitForTimeout(400);
 const snapshot=await page.evaluate(()=>({text:$('uartLog').textContent,nodes:$('uartLog').children.length,max:Math.max(...Array.from($('uartLog').children,n=>n.textContent.length))}));
 const expected=Array.from({length:65536},(_,i)=>((100000-65536+i)%256).toString(16).padStart(2,'0').toUpperCase()).join(' ');
 assert.equal(snapshot.text,expected);assert(snapshot.nodes<200);assert(snapshot.max<=1023);
 await page.click('#uartPause');await page.evaluate(()=>feed(1200));await page.waitForTimeout(350);
 assert.equal(await page.locator('#uartLog').textContent(),expected);
 await page.click('#uartPause');assert.notEqual(await page.locator('#uartLog').textContent(),expected);
 const before=await page.locator('#uartLog').textContent();
 await page.evaluate(()=>{showTab('settings');feed(1200)});await page.waitForTimeout(350);
 assert.equal(await page.locator('#uartLog').textContent(),before);
 await page.evaluate(()=>showTab('uart'));await page.waitForTimeout(350);
 assert.notEqual(await page.locator('#uartLog').textContent(),before);
 // The desktop's contiguous-run representation preserves ring bytes and UTF-8.
 await page.evaluate(()=>{
  $('uartClear').click();
  let offset=0,seq=0;
  while(offset<100000){const size=Math.min(6144,100000-offset),bytes=Uint8Array.from({length:size},(_,i)=>(offset+i)%256),count=Math.ceil(size/12);
   onNative('packets',{items:[{uart:hexOf(bytes),first:seq+1,last:seq+count,count,age:0}]});seq+=count;offset+=size;}
 });
 await page.waitForTimeout(350);
 assert.equal(await page.locator('#uartLog').textContent(),expected);
 await page.evaluate(()=>{
  $('uartClear').click();$('uartDisplay').value='text';$('uartDisplay').onchange();
  onNative('packets',{items:[{uart:'e4b8',first:0xffffffff,last:0xffffffff,count:1},{uart:'adf09f',first:0,last:0,count:1},{uart:'9982',first:2,last:2,count:1}]});
 });
 await page.waitForTimeout(350);
 assert.equal(await page.locator('#uartLog').textContent(),'中🙂');
 assert((await page.locator('#uartLoss').textContent()).includes('缺失 1 个'));
 console.log('PASS: exact latest 64 KiB, bounded layout blocks, pause/resume, hidden view suppression, contiguous UART batches/sequence wrap/gaps and split UTF-8');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
