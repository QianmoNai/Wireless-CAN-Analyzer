const assert=require('node:assert/strict');
const {resolve}=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.route('https://app.local/**',r=>r.fulfill({path:resolve(__dirname,'../android/app/src/main/assets',new URL(r.request().url()).pathname.slice(1))}));
  await page.addInitScript(()=>{window.CanAndroid={dashboard(){},showDetail(){},leaveDetail(){},setPolling(){}}});
  await page.goto('https://app.local/index.html');
  await page.addScriptTag({path:resolve(__dirname,'../desktop/can_scope/desktop.js')});
  await page.evaluate(()=>{
   onNative('state',{value:'ready'});for(let id=1;id<=2;id++)commitWatch({profile:'dm4310',id,master:16+id,pollHz:1000,mode:'mit',limits:{p:12.5,v:30,t:10}});
   dashboardOpen(JSON.stringify(watchedMotors[0].config));window.devicePollingSupported=true;
   window.queryKey=JSON.stringify(detailMotor.config);setMotorPolling(queryKey,true);
   onNative('queryMetrics',{key:queryKey,requested:1000,actual:620,message:'确认前旧统计620'});refreshDetail();
  });
  const note=page.locator('#detailQueryNote'),checkbox=page.locator('#detailQuery');
  assert(await checkbox.isChecked());assert(await checkbox.isDisabled());assert(!(await note.innerText()).includes('620'));
  await page.evaluate(()=>{
   motorQueryState({key:queryKey,enabled:true,message:'1000 Hz · 已确认'});
   onNative('queryMetrics',{key:queryKey,requested:1000,actual:633,message:'设定 1000 Hz · 实际查询 633 Hz'});refreshDetail();
  });
  assert((await note.innerText()).includes('633'));assert(await checkbox.isChecked());
  await page.evaluate(()=>{onNative('queryMetrics',{key:queryKey,requested:500,actual:450,message:'旧频率统计450'});refreshDetail()});
  assert((await note.innerText()).includes('633'));
  await checkbox.click();
  await page.evaluate(()=>{onNative('queryMetrics',{key:queryKey,requested:1000,actual:644,message:'旧统计644'});refreshDetail()});
  assert(await checkbox.isDisabled());assert(!(await checkbox.isChecked()));assert(!(await note.innerText()).includes('644'));
  await page.evaluate(()=>{motorQueryState({key:queryKey,enabled:false,message:'查询已关闭'});onNative('queryMetrics',{key:queryKey,requested:1000,actual:650,message:'旧统计650'});refreshDetail()});
  assert.equal(await note.innerText(),'查询已关闭');assert(!(await checkbox.isChecked()));
  const isolation=await page.evaluate(()=>{
   const other=watchedMotors[1];motorQueryState({key:JSON.stringify(other.config),enabled:true,message:'另一路已确认'});
   onNative('queryMetrics',{key:JSON.stringify(other.config),requested:1000,actual:500,message:'另一路实际500'});refreshDetail();
   return {other:other.queryMessage,selected:detailMotor.queryMessage};
  });assert.deepEqual(isolation,{other:'另一路实际500',selected:'查询已关闭'});
  await page.evaluate(()=>{motorQueryState({key:queryKey,enabled:true,message:'已确认'});onNative('state',{value:'idle'});onNative('queryMetrics',{key:queryKey,requested:1000,actual:660,message:'旧会话660'});refreshDetail()});
  assert(!(await checkbox.isChecked()));assert(!(await note.innerText()).includes('660'));assert.deepEqual(errors,[]);
  console.log('Desktop production handler: actual query rate, pending/stop, stale rate, motor isolation and disconnect PASS');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
