// Shared production dispatch with generated frames; no physical CAN claim.
const assert=require('node:assert/strict');
const {resolve}=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.route('https://app.local/**',r=>r.fulfill({path:resolve(__dirname,'../android/app/src/main/assets',new URL(r.request().url()).pathname.slice(1))}));
  await page.addInitScript(()=>{window.queryWrites=[];window.CanAndroid={dashboard(){},showDetail(){},leaveDetail(){},setMonitorVisible(){},setPolling(...a){queryWrites.push(a)}}});
  await page.goto('https://app.local/index.html');
  const result=await page.evaluate(()=>{
   const check=(v,m)=>{if(!v)throw Error(m)},equal=(a,b,m)=>check(JSON.stringify(a)===JSON.stringify(b),m);
   const now=1770000000000,oldNow=Date.now,oldPaint=requestDetailPaint;Date.now=()=>now;requestDetailPaint=()=>{};
   const state=()=>JSON.stringify({samples:detailSamples.slice(detailSampleHead),clock:detailClock,last:detailLastStamp,feedback:detailMotor.feedback,time:detailMotor.time,frames:detailRxFrames,count:detailAppCount});
   function frame(data,stamp,{flags=0,kind=11,dlc=data.length}={}){const a=new Uint8Array(20);a[0]=1;a[1]=kind;a[2]=flags;a[3]=dlc;a.set(data,8);put32(a,16,stamp);return hexOf(a)}
   function open(config){closeMotorDetail(false);watchedMotors.splice(0);commitWatch(config);dashboardOpen(JSON.stringify(watchedMotors[0].config));onNative('detailSupport',{supported:true,maxHz:1000});onNative('telemetryMode',{mode:2,hz:1000});cancelDetailPaint();}
   function deliver(items,key=JSON.stringify(detailMotor.config),extra={}){onNative('motorSamples',{key,items,sentAt:now-37,...extra})}
   try{
    onNative('state',{value:'ready'});rxSaveLimit=10000;
    open({profile:'c620',id:1});
    const payload=[0x10,0,0xff,0xfe,0xff,0xff,85,0];
    const items=Array.from({length:128},(_,i)=>({hex:frame(payload,(0xfffffff0+i)>>>0),age:13}));
    deliver(items);
    check(detailSamples.length===128&&detailAppCount===128&&detailClock===127&&detailLastStamp===111,'lost samples or wrap order');
    equal(detailMotor.feedback.values,{position:180,speed:-2,effort:-1,temperature:85},'known DJI values');
    check(detailMotor.time===now-50&&detailLastArrival===now-50,'batch delivery or receipt age lost');
    equal(detailRxFrames.map(f=>f.stamp),items.map(i=>u32(parsePacket(i.hex),16)).reverse(),'stream order changed');
    const before=state(),key=JSON.stringify(detailMotor.config);
    deliver(items,'stale config',{dropped:17});check(state()===before&&appDrops===17,'stale batch changed samples or lost drop count');
    const bad=[frame(payload,112,{flags:1}),frame(payload,112,{flags:2}),frame(payload,112,{kind:1}),frame(payload,112,{dlc:9}),frame(payload.slice(0,6),112),'00'+items[0].hex.slice(2),'gg'+items[0].hex.slice(2),items[0].hex.slice(2)];
    deliver(bad.map(hex=>({hex,age:0})));check(state()===before,'invalid sample changed retained state');
    detailReady=false;deliver(items);check(state()===before,'unconfirmed detail accepted samples');detailReady=true;
    connected=false;deliver(items);check(state()===before,'disconnected detail accepted samples');connected=true;
    // Raw management/stream packets still run before a stale detail batch.
    const rx=bucketRx;deliver([], 'stale config',{packets:[{hex:frame(payload,0,{kind:1}),age:0}]});check(bucketRx===rx+1,'batch discarded accompanying raw packet');
    onNative('motorSample',{key,hex:frame(payload,112),age:3,sentAt:now-7});check(detailSamples.length===129&&detailMotor.time===now-10,'legacy single-sample age/order');
    deliver([{hex:frame(payload,20),age:0}]);check(detailSamples.length===1&&detailClock===0&&detailLastStamp===20,'clock regression did not reset history');
    const oldKey=JSON.stringify(detailMotor.config);commitWatch({...detailMotor.config,name:'Edited test motor'},detailMotor);const edited=state();deliver(items,oldKey);check(state()===edited,'old config accepted after editing');
    open({profile:'dm_imu_l1',id:0x50,feedbackId:0x60});
    const imu=[1,2,3].map((group,i)=>({hex:frame([group,73,0,0,0,128,255,255],i),age:i}));deliver(imu);
    check(detailSamples.length===3&&Object.keys(detailMotor.feedback.values).length===10,'sparse IMU groups not accumulated');
    equal(detailSamples[0].values,{accelX:-235.2,accelY:-235.2+32768*470.4/65535,accelZ:235.2,temperature:73},'IMU acceleration changed');
    check(!('gyroX'in detailSamples[0].values)&&detailMotor.feedback.times.accelX===now-37&&detailMotor.feedback.times.gyroX===now-38&&detailMotor.feedback.times.pitch===now-39,'sparse history mutated or group age lost');
    open({profile:'c620',id:1});
    for(let start=0;start<60020;start+=128)deliver(Array.from({length:Math.min(128,60020-start)},(_,i)=>({hex:frame(payload,start+i),age:0})));
    check(detailAppCount===60020&&detailSamples.length-detailSampleHead===60000&&detailSamples[detailSampleHead].t===20&&detailClock===60019,'history cap lost accepted samples');
    check(detailRxFrames.length===10000&&detailRxFrames[0].stamp===60019&&detailRxFrames.at(-1).stamp===50020,'stream cap or chronology changed');
    check(queryWrites.length===0,'sample dispatch issued query commands');
    return {accepted:detailAppCount,retained:detailSamples.length-detailSampleHead,stream:detailRxFrames.length};
   }finally{Date.now=oldNow;requestDetailPaint=oldPaint;closeMotorDetail(false)}
  });
  assert.deepEqual(result,{accepted:60020,retained:60000,stream:10000});assert.deepEqual(errors,[]);
  console.log('Detail samples: batched/legacy dispatch, age, wrap/reset, sparse IMU, stale config, invalid frames, raw packets and full histories PASS');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
