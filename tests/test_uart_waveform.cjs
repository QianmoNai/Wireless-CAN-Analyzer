// Shared UART waveform parser and renderer; no hardware claim.
const {chromium}=require('playwright');
const {resolve}=require('path');
const assert=require('assert/strict');
const assets=resolve(__dirname,'../android/app/src/main/assets');
const packet=(bytes,seq)=>{const p=new Uint8Array(20);p.set([1,19,bytes.length,0,...bytes]);new DataView(p.buffer).setUint32(16,seq>>>0,true);return p;};
const bytes=(...groups)=>Uint8Array.from(groups.flat());
const just=(...rows)=>{const out=[];for(const row of rows){const p=new ArrayBuffer(row.length*4);const v=new DataView(p);row.forEach((n,i)=>v.setFloat32(i*4,n,true));out.push(...new Uint8Array(p),0,0,0x80,0x7f);}return Uint8Array.from(out);};
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||undefined,args:['--no-sandbox']});
 const context=await browser.newContext({viewport:{width:412,height:915}});await context.route('https://app.local/**',route=>route.fulfill({path:resolve(assets,new URL(route.request().url()).pathname.slice(1))}));
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('https://app.local/index.html');
 await page.evaluate(()=>{onNative('state',{value:'ready',name:'wave'});const p=new Uint8Array(20);p.set([1,21,1,0]);put32(p,4,115200);processPacket(hexOf(p));});
 assert(await page.locator('#uartWaveform').isVisible());
 await page.selectOption('#uartWaveProtocol','firewater');await page.selectOption('#uartWaveChannels','2');
 const send=async(part,seq)=>page.evaluate(({part,seq})=>{const p=new Uint8Array(20);p.set([1,19,part.length,0,...part]);put32(p,16,seq);processPacket(hexOf(p));},{part:Array.from(part),seq});
 const fire=Array.from(new TextEncoder().encode('1.25,-2\n3.5,4.75\n'));for(let i=0;i<fire.length;i+=3)await send(fire.slice(i,i+3),i/3+1);
 await page.waitForFunction(()=>uartWaveformState().frames===2);let state=await page.evaluate(()=>uartWaveformState());assert.equal(state.detected,'FireWater');assert.equal(state.channels,2);assert.deepEqual(await page.locator('#uartWaveValues b').allTextContents(),['3.5000','4.7500']);
 await page.evaluate(()=>{let text='';for(let i=0;i<4200;i++)text+=`${i},${-i}\n`;uartWaveformBytes(new TextEncoder().encode(text));});state=await page.evaluate(()=>uartWaveformState());assert.equal(state.samples,4096);assert.equal(state.frames,4202);
 const jf=just([1.5,-2.25],[8,9.5]);await page.selectOption('#uartWaveProtocol','auto');await page.selectOption('#uartWaveChannels','0');
 await send(jf.slice(0,5),100);await send(jf.slice(5,17),101);await send(jf.slice(17),102);await page.waitForFunction(()=>uartWaveformState().frames===2);state=await page.evaluate(()=>uartWaveformState());assert.equal(state.detected,'JustFloat');assert.equal(state.channels,2);assert.deepEqual(await page.locator('#uartWaveValues b').allTextContents(),['8.0000','9.5000']);
 await page.selectOption('#uartWaveProtocol','justfloat');await page.selectOption('#uartWaveChannels','2');await send(jf.slice(0,12),200);await send(jf.slice(12),201);await page.waitForFunction(()=>uartWaveformState().frames===2);state=await page.evaluate(()=>uartWaveformState());assert.equal(state.detected,'JustFloat');
 await page.click('#uartWavePause');assert.equal((await page.evaluate(()=>uartWaveformState())).paused,true);await page.click('#uartWavePause');assert.equal((await page.evaluate(()=>uartWaveformState())).paused,false);assert.deepEqual(errors,[]);await browser.close();console.log('UART waveform: FireWater streaming, JustFloat split frames, channel selection, pause/resume and shared canvas UI PASS');
})().catch(e=>{console.error(e);process.exit(1)});
