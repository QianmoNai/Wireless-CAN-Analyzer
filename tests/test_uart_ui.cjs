// Actual shared WebView assets, mock native transport; no physical UART claim.
const {chromium}=require('playwright');
const {resolve}=require('path');
const assert=require('assert/strict');
const assets=resolve(__dirname,'../android/app/src/main/assets');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:412,height:915}});
 await context.route('https://app.local/**',route=>route.fulfill({path:resolve(assets,new URL(route.request().url()).pathname.slice(1))}));
 await context.addInitScript(()=>{window.writes=[];window.CanAndroid={networkState(){},send(p){writes.push(p)},setMonitorVisible(){},dashboard(){},leaveDetail(){}};});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('https://app.local/index.html');
 const status=async(uart=true)=>page.evaluate(uart=>{
  onNative('state',{value:'ready',name:'ESP32-CAN'});
  const p=new Uint8Array(20);p[0]=1;p[1]=21;p[2]=uart?1:0;put32(p,4,115200);processPacket(hexOf(p));
 },uart);
 await status();
 assert(await page.locator('#view-uart').isVisible());
 assert(await page.locator('[data-tab="motor"]').isHidden());
 assert(await page.locator('#saveBitrate').isDisabled());
 await page.evaluate(()=>{for(const [seq,data] of [[1,[0,255,65]],[3,[228,184]],[4,[173]]]){const p=new Uint8Array(20);p.set([1,19,data.length,0,...data]);put32(p,16,seq);processPacket(hexOf(p));}});
 await page.waitForFunction(()=>document.getElementById('uartLog').textContent.includes('00 FF 41 E4 B8 AD'));
 assert((await page.locator('#uartLoss').textContent()).includes('缺失 1 个'));
 await page.selectOption('#uartDisplay','text');assert((await page.locator('#uartLog').textContent()).includes('中'));
 // HEX segmentation, ACK arriving before writeDone, token isolation, exact bytes.
 await page.fill('#uartInput','00 FF 01 02 03 04 05 06 07 08 09 0A 0B');await page.click('#uartSend');
 assert.equal(await page.evaluate(()=>writes.length),1);
 await page.evaluate(()=>{const a=parsePacket(writes[0]);a[1]=20;a[3]=a[2];a[2]=0;processPacket(hexOf(a));});
 assert.equal(await page.evaluate(()=>writes.length),1);
 await page.evaluate(()=>onNative('writeDone',{success:true}));
 await page.waitForFunction(()=>writes.length===2);
 assert.equal(await page.evaluate(()=>parsePacket(writes[1])[4]),11);
 await page.evaluate(()=>{onNative('writeDone',{success:true});const a=parsePacket(writes[1]);a[1]=20;a[3]=a[2];a[2]=0;put32(a,16,1);processPacket(hexOf(a));});
 assert(await page.locator('#uartSend').isDisabled());
 await page.evaluate(()=>{const a=parsePacket(writes[1]);a[1]=20;a[3]=a[2];a[2]=0;processPacket(hexOf(a));});
 await page.waitForFunction(()=>!document.getElementById('uartSend').disabled);
 assert((await page.locator('#uartResult').textContent()).includes('13 / 13'));
 // UTF-8 send with CRLF; invalid HEX never enters native writes.
 await page.selectOption('#uartFormat','text');await page.selectOption('#uartEnding','crlf');await page.fill('#uartInput','中');await page.click('#uartSend');
 assert.deepEqual(await page.evaluate(()=>Array.from(parsePacket(writes[2]).slice(4,9))),[228,184,173,13,10]);
 await page.evaluate(()=>{onNative('writeDone',{success:true});const a=parsePacket(writes[2]);a[1]=20;a[3]=a[2];a[2]=0;processPacket(hexOf(a));});
 await page.waitForFunction(()=>!document.getElementById('uartSend').disabled);
 await page.selectOption('#uartFormat','hex');await page.fill('#uartInput','ZZ');await page.click('#uartSend');assert.equal(await page.evaluate(()=>writes.length),3);
 // Switching to CAN cancels a multi-chunk send and never replays after reconnect.
 await page.fill('#uartInput','01 '.repeat(30));await page.click('#uartSend');assert.equal(await page.evaluate(()=>writes.length),4);
 await page.evaluate(()=>{const p=new Uint8Array(20);p[0]=1;p[1]=21;processPacket(hexOf(p));onNative('writeDone',{success:true});});
 assert(await page.locator('[data-tab="motor"]').isVisible());assert.equal(await page.evaluate(()=>writes.length),4);
 await status();await page.evaluate(()=>{const p=new Uint8Array(20);p.set([1,19,6,0,0,255,65,228,184,173]);put32(p,16,5);processPacket(hexOf(p));});await page.selectOption('#uartDisplay','hex');await page.fill('#uartInput','01 02 FF 00');await page.selectOption('#uartEnding','');await page.waitForTimeout(150);
 await page.screenshot({path:resolve(__dirname,'../android/dist/preview-uart-phone.png'),fullPage:true});
 await page.setViewportSize({width:1280,height:900});await page.screenshot({path:resolve(__dirname,'../android/dist/preview-uart-desktop.png'),fullPage:true});
 await page.evaluate(()=>onNative('state',{value:'idle'}));assert(await page.locator('#uartSend').isDisabled());assert(await page.locator('[data-tab="motor"]').isVisible());
 assert.deepEqual(errors,[]);await browser.close();console.log('UART UI: mode isolation, binary/UTF-8 receive/send, packet gaps, token/ACK ordering, segmentation and disconnect cancellation PASS');
})().catch(e=>{console.error(e);process.exit(1)});
