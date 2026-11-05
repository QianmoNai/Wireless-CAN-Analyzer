const assert=require('node:assert/strict');
const {resolve}=require('node:path');
const {chromium}=require('playwright');

(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 try{
  const page=await browser.newPage();const errors=[];
  page.on('pageerror',error=>errors.push(String(error)));
  await page.route('https://app.local/**',route=>route.fulfill({path:resolve(__dirname,'../android/app/src/main/assets',new URL(route.request().url()).pathname.slice(1))}));
  await page.addInitScript(()=>{window.CanAndroid={dashboard(){},showDetail(){},setMonitorVisible(){}}});
  await page.goto('https://app.local/index.html');
  const result=await page.evaluate(()=>{
   const check=(value,message)=>{if(!value)throw Error(message)};
   let cases=0;
   for(const size of [0,1,1600,1601,3202,60000])for(const extra of [null,'mos']){
    const points=Array.from({length:size},(_,i)=>({t:i+(i>=1601?400:0),values:{position:i%17?Math.sin(i):NaN,mos:i%19?-i:NaN}}));
    const before=JSON.stringify(points),bounds={};
    const selected=detailBoundedPeakPoints(points,'position',100000,0,1,bounds,extra);
    const prior=detailPeakPoints(points,'position',100000,0,1);
    check(selected.length===prior.length&&selected.every((p,i)=>p===prior[i]),'changed extrema/time-gap order or sample identity');
    let count=0,lo=Infinity,hi=-Infinity;
    for(const point of points)for(const key of extra?['position',extra]:['position']){
     const value=point.values[key];if(Number.isFinite(value)){count++;lo=Math.min(lo,value);hi=Math.max(hi,value)}
    }
    check(bounds.count===count&&bounds.lo===lo&&bounds.hi===hi,'incorrect finite primary/secondary bounds');
    check(JSON.stringify(points)===before,'mutated immutable history');
    if(size<=1600)check(selected===points,'copied small history');
    cases++;
   }
   // Visible geometry includes both temperatures on a shared scale, with a
   // NaN gap and a genuine time discontinuity. Freezing must keep the paths.
   onNative('state',{value:'ready'});
   commitWatch({profile:'dm4310',id:1,master:17,mode:'mit',limits:{p:12.5,v:30,t:10}});
   dashboardOpen(JSON.stringify(watchedMotors[0].config));
   onNative('detailSupport',{supported:true,maxHz:1000});onNative('telemetryMode',{mode:2,hz:1000});
   cancelDetailPaint();
   detailSamples=Array.from({length:3202},(_,i)=>({t:i+(i>=1601?400:0),values:{position:0,speed:0,effort:0,temperature:i===10?NaN:40,mosTemperature:i===1700?90:50}}));
   detailClock=detailSamples.at(-1).t;detailSampleHead=0;detailLastArrival=0;$('detailWindow').value='60';
   for(const series of detailSeries)series.card.detailVisible=true;
   drawDetail();const root=detailSeries.find(s=>s.extra).card;
   const paths=[...root.querySelectorAll('.trace,.trace-extra')].map(p=>p.getAttribute('d'));
   check(paths.every(p=>p&&p.includes('L')&&p.split('M').length>=3),'lost temperature gap or empty curve');
   check(root.querySelector('.detail-scale').textContent.includes('94'),'secondary temperature missing from shared scale');
   detailTogglePause();const frozen=paths.join('|');detailClock+=1000;drawDetail();
   check([...root.querySelectorAll('.trace,.trace-extra')].map(p=>p.getAttribute('d')).join('|')===frozen,'frozen geometry changed');
   return {cases};
  });
  assert.equal(result.cases,12);assert.deepEqual(errors,[]);
  console.log('Motor curves: finite shared bounds, NaN/time gaps, immutable extrema references, 60000 samples and frozen dual-temperature geometry PASS');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
