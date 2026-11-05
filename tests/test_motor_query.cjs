const assert=require('node:assert/strict');
const {resolve}=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',error=>errors.push(String(error)));
  await page.route('https://app.local/**',route=>route.fulfill({path:resolve(__dirname,'../android/app/src/main/assets',new URL(route.request().url()).pathname.slice(1))}));
  await page.addInitScript(()=>{window.queryRequests=[];window.CanAndroid={dashboard(){},showDetail(){},leaveDetail(){},setMonitorVisible(){},setPolling(...args){queryRequests.push(args)}}});
  await page.goto('https://app.local/index.html');
  await page.evaluate(()=>{
   onNative('state',{value:'ready'});for(let id=1;id<=2;id++)commitWatch({profile:'dm4310',id,master:16+id,pollHz:100,mode:'mit',limits:{p:12.5,v:30,t:10}});
   dashboardOpen(JSON.stringify(watchedMotors[0].config));onNative('detailSupport',{supported:true,maxHz:1000});onNative('telemetryMode',{mode:2});window.devicePollingSupported=true;refreshDetail();
  });
  const checkbox=page.locator('#detailQuery');
  await checkbox.click();await page.waitForTimeout(250);
  assert.equal(await checkbox.isChecked(),true);assert.equal(await checkbox.isDisabled(),true);assert.equal(await page.locator('#detailHz').isDisabled(),true);
  await page.evaluate(()=>{const key=JSON.stringify(detailMotor.config);setMotorPolling(key,true);motorQueryState({key,enabled:false,pending:true,message:'正在应用 100 Hz…'});refreshDetail()});
  assert.equal(await page.evaluate('queryRequests.length'),1);assert.equal(await checkbox.isChecked(),true);assert.equal(await checkbox.isDisabled(),true);
  await page.evaluate(()=>{motorQueryState({key:JSON.stringify(detailMotor.config),enabled:true,message:'100 Hz · 已确认'});refreshDetail()});
  assert.equal(await checkbox.isChecked(),true);assert.equal(await checkbox.isDisabled(),false);
  await checkbox.click();await page.waitForTimeout(250);assert.equal(await checkbox.isChecked(),false);assert.equal(await checkbox.isDisabled(),true);
  await page.evaluate(()=>{motorQueryState({key:JSON.stringify(detailMotor.config),enabled:false,pending:true,message:'正在关闭查询…'});refreshDetail()});assert.equal(await checkbox.isDisabled(),true);
  await page.evaluate(()=>{motorQueryState({key:JSON.stringify(detailMotor.config),enabled:false,message:'查询已关闭'});refreshDetail()});assert.equal(await checkbox.isChecked(),false);assert.equal(await checkbox.isDisabled(),false);
  for(const message of ['设备未接受查询配置','查询确认超时，已停止续租']){
   await checkbox.click();await page.evaluate(message=>{motorQueryState({key:JSON.stringify(detailMotor.config),enabled:false,message});refreshDetail()},message);assert.equal(await checkbox.isChecked(),false);assert.equal(await checkbox.isDisabled(),false);
  }
  const independent=await page.evaluate(()=>{
   queryRequests.length=0;const [a,b]=watchedMotors,ka=JSON.stringify(a.config),kb=JSON.stringify(b.config);
   setMotorPolling(ka,true);dashboardOpen(kb);refreshDetail();const otherAvailable=!$('detailQuery').disabled;
   setMotorPolling(kb,true);motorQueryState({key:ka,enabled:true,message:'100 Hz · 已确认'});refreshDetail();const otherPending=b.queryPending&&$('detailQuery').disabled;
   motorQueryState({key:kb,enabled:false,message:'查询未被接受'});refreshDetail();
   return {otherAvailable,otherPending,aConfirmed:a.polling,bRejected:!b.polling&&!b.queryPending,requests:queryRequests.length};
  });assert.deepEqual(independent,{otherAvailable:true,otherPending:true,aConfirmed:true,bRejected:true,requests:2});
  const stop=await page.evaluate(()=>{queryRequests.length=0;const key=JSON.stringify(detailMotor.config);setMotorPolling(key,true);setMotorPolling(key,false);setMotorPolling(key,false);return {requests:queryRequests.length,pending:detailMotor.queryPending,target:detailMotor.queryTarget}});assert.deepEqual(stop,{requests:2,pending:true,target:false});
  const edit=await page.evaluate(()=>{const m=detailMotor,old=JSON.stringify(m.config);commitWatch({...m.config,pollHz:25},m);motorQueryState({key:old,enabled:true,message:'旧配置迟到确认'});return {hz:m.config.pollHz,pending:m.queryPending,polling:m.polling}});assert.deepEqual(edit,{hz:25,pending:false,polling:false});
  const editor=await page.evaluate(()=>{const m=detailMotor;setMotorPolling(JSON.stringify(m.config),true);const before=queryRequests.length;openWatchEditor(m);return {pending:m.queryPending,polling:m.polling,stop:queryRequests.at(-1)[2],requests:queryRequests.length-before}});assert.deepEqual(editor,{pending:false,polling:false,stop:false,requests:1});
  await page.evaluate(()=>{onNative('state',{value:'idle'});refreshDetail()});assert.equal(await checkbox.isDisabled(),true);assert.equal(await page.evaluate('watchedMotors.some(m=>m.polling||m.queryPending)'),false);
  assert.deepEqual(errors,[]);console.log('Motor query UI: delayed enable/stop, duplicate suppression, terminal failure, independent motors, explicit cancel, edit and disconnect PASS');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
