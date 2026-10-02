'use strict';
// One active motor, bounded history. Samples carry the ESP32 CAN receive timestamp.
let detailMotor=null,detailSamples=[],detailClock=0,detailLastStamp=null,detailLastArrival=0,detailFrozen=null,detailReady=false,detailSupported=null,detailChannel='';
let detailSeries=[],detailFrozenSamples=[];
let detailSampleHead=0,detailMaxHz=0,detailAppliedHz=20,detailRatePrevious=null,detailRateAt=0,detailCanHz=null,detailSampleCountHz=null;
let detailAppCount=0,detailAppWindow=0,detailAppHz=null;
let detailChartKey=null,detailChartFocus=null,detailChartScroll=0;
function detailChartSeries(){return detailChartKey===null?null:detailSeries.find(s=>s.key===detailChartKey)||null}
function detailSeriesValue(s){if(s.imu){const f=detailMotor&&detailMotor.feedback,v=f&&f.values[s.key],at=f&&f.times&&f.times[s.key];return Number.isFinite(v)&&at&&Date.now()-at<2000?Number(v.toFixed(4))+(s.unit?' '+s.unit:''):'—'}const f=detailMotor&&detailMotor.feedback;return f?(s.extra?f.values.temperature+' / '+f.values.mosTemperature+' °C':f[s.display]):'—'}
function refreshDetailChart(){
 const s=detailChartSeries();if(!s||!detailMotor)return;
 const c=detailMotor.config,p=Motors.profiles[c.profile],f=detailMotor.feedback;
 $('detailChartTitle').textContent=s.label;
 $('detailChartMotor').textContent=(c.name||p.name)+(p.family==='imu'?' · IMU ID ':' · 电机 ID ')+watchHex(c.id)+' · '+(detailChannel||$('detailSource').textContent||$('deviceName').textContent);
 const number=key=>f&&(!s.imu||(f.times&&Date.now()-f.times[key]<2000))&&Number.isFinite(f.values[key])?Number(f.values[key].toFixed(3)).toString():'—';
 $('detailChartValue').textContent=s.extra?number(s.key)+' / '+number(s.extra):number(s.key);
 $('detailChartUnit').textContent=s.unit;$('detailChartLegend').hidden=!s.extra;
 $('detailChartState').textContent=$('detailState').textContent+(c.profile==='c610'&&s.key==='temperature'?' · C610 不提供温度反馈':'');
 $('detailChartState').className=$('detailState').className;
 $('detailChartPause').textContent=detailFrozen===null?'暂停曲线':'继续曲线';
 $('detailChartWindow').value=$('detailWindow').value;
}
function openDetailChart(key){
 if(!detailMotor||$('detailChartsPanel').hidden)return false;
 const s=detailSeries.find(s=>s.key===key);if(!s)return false;
 const first=detailChartKey===null;if(first)detailChartScroll=window.scrollY;
 detailChartKey=key;detailChartFocus=s.card;
 $('detailChartFullscreen').hidden=false;document.body.classList.add('chart-fullscreen');
 const app=document.querySelector('.app');app.inert=true;app.setAttribute('aria-hidden','true');
 refreshDetailChart();drawDetail();$('detailChartClose').focus({preventScroll:true});
 if(first&&window.CanAndroid&&typeof CanAndroid.setChartFullscreen==='function')CanAndroid.setChartFullscreen(true);
 return true;
}
window.closeDetailChartFullscreen=function(notify=true){
 if(detailChartKey===null)return false;
 const focus=detailChartFocus;detailChartKey=null;detailChartFocus=null;
 $('detailChartFullscreen').hidden=true;document.body.classList.remove('chart-fullscreen');
 const app=document.querySelector('.app');app.inert=false;app.removeAttribute('aria-hidden');
 if(notify&&window.CanAndroid&&typeof CanAndroid.setChartFullscreen==='function')CanAndroid.setChartFullscreen(false);
 if(detailMotor&&!$('detailChartsPanel').hidden)drawDetail();
 if(focus&&focus.isConnected)focus.focus({preventScroll:true});
 window.scrollTo(0,detailChartScroll);return true;
};
function detailChartWindowChanged(value){$('detailWindow').value=value;drawDetail()}
window.addEventListener('keydown',event=>{
 if(detailChartKey===null)return;
 if(event.key==='Escape'){event.preventDefault();closeDetailChartFullscreen();return;}
 if(event.key==='Tab'){
  const controls=Array.from($('detailChartFullscreen').querySelectorAll('button,select')).filter(e=>!e.disabled),first=controls[0],last=controls[controls.length-1],active=document.activeElement;
  if(event.shiftKey&&(active===first||!$('detailChartFullscreen').contains(active))){event.preventDefault();last.focus();}
  else if(!event.shiftKey&&(active===last||!$('detailChartFullscreen').contains(active))){event.preventDefault();first.focus();}
 }
});
window.addEventListener('pagehide',()=>closeDetailChartFullscreen());
document.addEventListener('visibilitychange',()=>{if(document.hidden)closeDetailChartFullscreen()});
window.addEventListener('resize',()=>{if(detailChartKey!==null)drawDetail()});
function detailRateKey(m){const c=m.config;return c.profile+':'+c.id+':'+c.feedbackId}
window.detailRateFor=function(m){try{const rates=ConfigIO.readField('detailRates')||{},hz=rates[detailRateKey(m)];return Number.isInteger(hz)&&hz>=1&&hz<=1000?hz:100}catch(e){return 100}};
function resetDetailRates(){detailRatePrevious=null;detailRateAt=0;detailCanHz=null;detailSampleCountHz=null;detailAppCount=0;detailAppWindow=performance.now();detailAppHz=null;}
function saveDetailSampleRate(){
 if(!detailMotor)return;const raw=$('detailSampleHz').value.trim(),hz=Number(raw);
 if(!raw||!Number.isInteger(hz)||hz<1||hz>1000){$('detailSampleResult').textContent='频率须为 1–1000 Hz 的整数';return;}
 if(!connected||!detailMaxHz){$('detailSampleResult').textContent='请连接并更新 ESP32 固件后设置';return;}
 if(hz>detailMaxHz){$('detailSampleResult').textContent='当前固件最高支持 '+detailMaxHz+' Hz';return;}
 try{const previous=ConfigIO.readField('detailRates')||{},rates={};for(const m of watchedMotors){const key=detailRateKey(m);if(Number.isInteger(previous[key]))rates[key]=previous[key];}rates[detailRateKey(detailMotor)]=hz;ConfigIO.writeField('detailRates',rates);}catch(e){$('detailSampleResult').textContent='保存失败，请重试';return;}
 if(detailReady&&hz===detailAppliedHz){$('detailSampleResult').textContent='已应用上报上限 '+hz+' Hz';publishDashboard();return;}
 detailReady=false;clearDetail();resetDetailStream();resetDetailRates();$('detailSampleResult').textContent='正在应用 '+hz+' Hz…';publishDashboard();
}
window.motorDetailRates=function(d){
 if(!detailMotor||!connected||!detailReady||d.key!==JSON.stringify(detailMotor.config))return;
 if(detailRatePrevious){const ms=(d.elapsed-detailRatePrevious.elapsed)>>>0;if(ms>0&&ms<=5000){detailCanHz=((d.received-detailRatePrevious.received)>>>0)*1000/ms;detailSampleCountHz=((d.sampled-detailRatePrevious.sampled)>>>0)*1000/ms;}}
 detailRatePrevious=d;detailRateAt=Date.now();
};
function clearDetail(){detailSampleHead=0;detailFrozenSamples=[];detailSamples=[];detailClock=0;detailLastStamp=null;detailLastArrival=0;detailFrozen=null;$('detailPause').textContent='暂停曲线';drawDetail()}
function openMotorDetail(m){
 closeDetailChartFullscreen();
 detailMotor=m;activeWatch=m;detailReady=false;resetDetailRates();$('detailSampleHz').value=detailRateFor(m);$('detailSampleResult').textContent='';m.feedback=null;m.time=0;
 document.body.classList.add('motor-detail');document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!=='view-detail');
 const c=m.config,p=Motors.profiles[c.profile],dm=p.family==='dm';
 $('detailName').textContent=c.name||p.name;$('detailAddress').textContent=p.name+(p.family==='imu'?' · IMU ID ':' · 电机 ID ')+watchHex(c.id)+' · RX '+watchHex(c.feedbackId);
 $('detailSource').textContent=detailChannel||$('deviceName').textContent;
 $('detailQueryPanel').hidden=!dm;$('detailHz').value=c.pollHz||100;
 detailSeries=p.rawAngle?[{label:'机械角度',key:'position',unit:'raw',display:'angle'},{label:'输出转矩',key:'effort',unit:'raw',display:'effort'}]:p.family==='imu'?Motors.imuSeries():[{label:dm?'位置':'转子角度',key:'position',unit:dm?'rad':'°',display:'angle'},{label:'速度',key:'speed',unit:dm?'rad/s':'rpm',display:'speed'},{label:dm?'扭矩':'电流反馈',key:'effort',unit:dm?'N·m':'raw',display:'effort'},{label:dm?'温度 · 电机 / MOS':'温度',key:'temperature',extra:dm?'mosTemperature':null,unit:'°C',display:'temperature'}];
 const root=$('detailPlots');root.replaceChildren();for(const s of detailSeries){const card=document.createElement('article');card.className='detail-plot';card.tabIndex=0;card.setAttribute('role','button');card.setAttribute('aria-haspopup','dialog');card.setAttribute('aria-controls','detailChartFullscreen');card.setAttribute('aria-label','放大'+s.label+'曲线');card.innerHTML='<div class="detail-plot-title"><h3></h3><span class="detail-expand" aria-hidden="true">↗</span></div><div class="detail-number">—</div><div class="detail-scale"><span></span><span></span></div><svg viewBox="0 0 400 140" preserveAspectRatio="none" role="img"><path d="M0 20H400 M0 70H400 M0 120H400" stroke="#243044" stroke-dasharray="3 5"/><path class="trace" stroke="#65e6cb" stroke-width="2" fill="none" vector-effect="non-scaling-stroke"/><path class="trace-extra" stroke="#83aaff" stroke-width="2" fill="none" vector-effect="non-scaling-stroke"/></svg><div class="detail-axis"><span></span><span></span></div>';card.querySelector('h3').textContent=s.label+' · '+s.unit;card.querySelector('svg').setAttribute('aria-label',s.label+'动态曲线');card.addEventListener('click',()=>openDetailChart(s.key));card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openDetailChart(s.key)}});root.append(card);s.card=card;}
 clearDetail();resetDetailStream();showDetailTab('charts');publishDashboard();if(window.CanAndroid&&typeof CanAndroid.showDetail==='function')CanAndroid.showDetail(JSON.stringify(c));
 window.scrollTo(0,0);refreshDetail();
}
function closeMotorDetail(notify=true){closeDetailChartFullscreen();if(!detailMotor)return;detailMotor=null;detailReady=false;clearDetail();resetDetailStream();document.body.classList.remove('motor-detail');renderChannelTab('motor');if(notify&&window.CanAndroid&&typeof CanAndroid.leaveDetail==='function')CanAndroid.leaveDetail()}
function detailHome(){closeMotorDetail(false);if(window.CanAndroid&&typeof CanAndroid.home==='function')CanAndroid.home();else showTab('motor')}
function detailRetarget(m){if(m!==detailMotor)return;closeDetailChartFullscreen();detailReady=false;clearDetail();resetDetailStream();if(window.CanAndroid&&typeof CanAndroid.showDetail==='function')CanAndroid.showDetail(JSON.stringify(m.config))}
window.motorDetailSupport=d=>{detailSupported=!!d.supported;detailMaxHz=d.maxHz||0;detailChannel=d.channel||'';if(detailMotor)$('detailSource').textContent=detailChannel;refreshDetail()};
window.motorDetailMode=d=>{if(detailMotor){if(d.mode!==2)closeDetailChartFullscreen();detailReady=d.mode===2;detailAppliedHz=d.hz||20;resetDetailRates();$('detailSampleResult').textContent=detailReady?'已应用上报上限 '+detailAppliedHz+' Hz':'';clearDetail();resetDetailStream();detailMotor.feedback=null;detailMotor.time=0;refreshDetail()}};
window.motorDetailSample=function(d){
 const m=detailMotor;if(!m||!connected||!detailReady||JSON.stringify(m.config)!==d.key||!/^([0-9a-f]{2}){20}$/i.test(d.hex))return;
 const a=Uint8Array.from(d.hex.match(/../g).map(x=>parseInt(x,16)));if(a[1]!==11||(a[2]&3)||a[3]>8)return;
 const c=m.config,p=Motors.profiles[c.profile],data=Array.from(a.slice(8,8+a[3]));let f;
 try{f=p.family==='imu'?Motors.imuFeedback(c.feedbackId,c.feedbackId,data):p.family==='dji'?Motors.djiFeedback(c.profile,c.id,c.feedbackId,data,c.feedbackId):Motors.dmFeedback(c.id,c.feedbackId,c.feedbackId,data,c.limits)}catch(e){return}
 if(!f||!f.values)return;
 const stamp=u32(a,16),now=Date.now();if(detailLastStamp!==null){const delta=(stamp-detailLastStamp)>>>0;if(delta>0x7fffffff){clearDetail();resetDetailStream();}else detailClock+=delta;}
 detailLastStamp=stamp;detailLastArrival=now;detailAppCount++;recordDetailFrame(c.feedbackId,data,stamp);if(p.family==='imu'){const old=m.feedback;const times={...(old&&old.times)};for(const k of Object.keys(f.values))times[k]=now;m.feedback={...f,values:{...(old&&old.values),...f.values},times};}else m.feedback=f;m.time=now;watchedDirty=true;
 detailSamples.push({t:detailClock,values:f.values});
 while(detailSamples.length-detailSampleHead>60000||(detailSamples.length-detailSampleHead>1&&detailClock-detailSamples[detailSampleHead].t>60000))detailSampleHead++;
 if(detailSampleHead>=2048){detailSamples=detailSamples.slice(detailSampleHead);detailSampleHead=0;}
 // Every accepted frame updates the visible view immediately; no batch/timer gate.
 refreshDetail();if(!$('detailChartsPanel').hidden)drawDetail();
 if(!$('detailStreamPanel').hidden&&detailStreamFrozen===null)renderDetailStream();
};
function detailTogglePause(){detailFrozenSamples=detailFrozen===null?detailSamples.slice(detailSampleHead):[];detailFrozen=detailFrozen===null?detailEnd():null;$('detailPause').textContent=detailFrozen===null?'暂停曲线':'继续曲线';drawDetail()}
function detailEnd(){return detailClock+(detailLastArrival?Date.now()-detailLastArrival:0)}
function refreshDetail(){
 if(!detailMotor)return;
 const tick=performance.now(),dt=tick-detailAppWindow;if(dt>=1000){detailAppHz=detailAppCount*1000/dt;detailAppCount=0;detailAppWindow=tick;}
 const rateText=v=>v===null?'— Hz':v.toFixed(1)+' Hz';
 $('detailCanRate').textContent=rateText(connected&&Date.now()-detailRateAt<3000?detailCanHz:null);
 $('detailAppRate').textContent=rateText(connected?detailAppHz:null);
 $('detailSampleSave').disabled=!connected||!detailMaxHz||watchBusy();
 $('detailSampleHz').disabled=watchBusy();
 $('detailRateNote').textContent=!detailMaxHz?'当前固件固定 20 Hz，更新 ESP32 后支持可调频率。':!detailReady?'等待订阅确认…':'上报上限 '+detailAppliedHz+' Hz · '+(detailSampleCountHz===null?'等待设备统计':'设备采样 '+detailSampleCountHz.toFixed(1)+' Hz')+' · 每帧更新界面';
 const m=detailMotor,fresh=connected&&m.time&&Date.now()-m.time<2000;
 $('detailState').textContent=!connected?'通道未连接':!m.time?'等待设备反馈':fresh?'反馈在线 · '+m.feedback.state:'反馈超时 · 保留最后一次读数';$('detailState').className='detail-status'+(fresh?' fresh':m.time&&connected?' stale':'');
 $('detailNotice').textContent=!connected?'返回首页连接该通道后查看反馈。':detailSupported===false?'请更新该通道的 ESP32 固件以启用单电机订阅。':!detailReady?'正在订阅设备…':Motors.profiles[m.config.profile].family==='imu'?'仅接收主动上报；各类数据独立更新，超过 2 秒未更新显示 —。上报上限按四类数据分配；CAN 数据流显示采样后的帧。':Motors.profiles[m.config.profile].rawAngle?'GM3510 仅上报角度与转矩原始值，无速度和温度字段。':detailMotor.config.profile==='c610'?'C610 不提供温度反馈。':Motors.profiles[m.config.profile].family==='dm'?'温度曲线：绿色为电机，蓝色为 MOS。':'角度为转子单圈角度；电流显示协议原始值 raw。';
 const dm=Motors.profiles[m.config.profile].family==='dm';if(dm){$('detailQuery').checked=!!m.polling;$('detailQuery').disabled=!connected||watchBusy()||!window.devicePollingSupported;$('detailHz').disabled=watchBusy();if(document.activeElement!==$('detailHz'))$('detailHz').value=m.config.pollHz;$('detailQueryNote').textContent=m.queryMessage||'查询默认关闭；开启后 ESP32 按设定频率问询此电机。修改频率会停止查询。';}
 for(const s of detailSeries)s.card.querySelector('.detail-number').textContent=detailSeriesValue(s);
 refreshDetailChart();
}
function drawDetail(){
 if(!detailMotor)return;const end=detailFrozen===null?detailEnd():detailFrozen,span=Number($('detailWindow').value)*1000,start=end-span;
 const history=detailFrozen===null?detailSamples:detailFrozenSamples;
 const first=detailLowerBound(history,start,detailFrozen===null?detailSampleHead:0),last=detailLowerBound(history,end,first,true);
 const points=history.slice(first,last);
 const expanded=detailChartSeries();
 if(expanded){
  refreshDetailChart();const svg=$('detailChartSvg'),width=Math.max(1,Math.round(svg.clientWidth)),height=Math.max(1,Math.round(svg.clientHeight));
  svg.setAttribute('viewBox','0 0 '+width+' '+height);
  const inset=Math.min(15,height*.1),lines=[];for(let i=0;i<5;i++){const y=inset+(height-2*inset)*i/4;lines.push('M0 '+y.toFixed(2)+'H'+width);}
  $('detailChartGrid').setAttribute('d',lines.join(' '));
  drawDetailSeries(expanded,$('detailChartFullscreen'),points,span,start,width,height);
 }else for(const s of detailSeries)drawDetailSeries(s,s.card,points,span,start,400,140);
}
function drawDetailSeries(s,root,points,span,start,width,height){
 const fmt=v=>Math.abs(v)>=10000?v.toExponential(1):Number(v.toFixed(3)).toString();
 let count=0,lo=Infinity,hi=-Infinity;for(const p of points)for(const v of [p.values[s.key],s.extra?p.values[s.extra]:null])if(Number.isFinite(v)){count++;lo=Math.min(lo,v);hi=Math.max(hi,v);}if(!count){lo=0;hi=1;}if(hi===lo){lo-=1;hi+=1;}const pad=(hi-lo)*.08;lo-=pad;hi+=pad;
 const inset=Math.min(15,height*.1),plotHeight=height-2*inset;
 const path=key=>{let result='',last=null;for(const p of detailPeakPoints(s.imu?points.filter(p=>Number.isFinite(p.values[key])):points,key,span,start,width)){const v=p.values[key];if(!Number.isFinite(v)){last=null;continue}const x=(p.t-start)/span*width,y=height-inset-(v-lo)/(hi-lo)*plotHeight;result+=(last===null||p.t-last>Math.max(250,3000/detailAppliedHz)?'M':'L')+x.toFixed(2)+','+y.toFixed(2);if(last===null||p.t-last>Math.max(250,3000/detailAppliedHz))result+='l.1,0';last=p.t;}return result;};
 root.querySelector('.trace').setAttribute('d',path(s.key));root.querySelector('.trace-extra').setAttribute('d',s.extra?path(s.extra):'');const labels=root.querySelectorAll('.detail-scale span');labels[0].textContent=count?fmt(lo)+' '+s.unit:'等待数据';labels[1].textContent=count?fmt(hi)+' '+s.unit:'';const axis=root.querySelectorAll('.detail-axis span');axis[0].textContent=(span<1000?span+' ms':span/1000+' 秒')+'前';axis[1].textContent=detailFrozen===null?'现在':'已暂停';
}
function detailQueryToggle(enabled){if(detailMotor)setMotorPolling(JSON.stringify(detailMotor.config),enabled)}
function detailQueryRate(hz){if(detailMotor)setMotorQueryHz(JSON.stringify(detailMotor.config),hz);refreshDetail()}
setInterval(()=>{if(!detailMotor)return;if(!connected){closeDetailChartFullscreen();if(detailSamples.length||detailReady){detailReady=false;clearDetail();resetDetailStream();resetDetailRates();}}refreshDetail();if(!$('detailChartsPanel').hidden)drawDetail();if(!$('detailStreamPanel').hidden&&detailStreamDirty)renderDetailStream()},100);

// Reuse event11 payloads: no second subscription or additional BLE traffic.
let detailRxFrames=[],detailStreamFrozen=null,detailStreamPage=0,detailStreamDirty=true;
const detailStreamPageSize=40;
function showDetailTab(tab){
 closeDetailChartFullscreen();
 const stream=tab==='stream';$('detailChartsPanel').hidden=stream;$('detailStreamPanel').hidden=!stream;
 $('detailChartsTab').setAttribute('aria-selected',String(!stream));$('detailStreamTab').setAttribute('aria-selected',String(stream));
 if(stream)renderDetailStream();else drawDetail();
}
function resetDetailStream(){
 detailRxFrames=[];detailStreamFrozen=null;detailStreamPage=0;detailStreamDirty=true;
 $('detailStreamPause').textContent='暂停列表';$('detailStreamLimit').value=rxSaveLimit;
 $('detailStreamLimitNote').textContent='沿用本通道 RX 上限，0 表示不保留；暂停仅冻结列表显示。';renderDetailStream();
}
function recordDetailFrame(id,data,stamp){
 if(rxSaveLimit>0)detailRxFrames.unshift({id,data,stamp});
 if(detailRxFrames.length>rxSaveLimit)detailRxFrames.length=rxSaveLimit;
 if(detailStreamFrozen===null)detailStreamDirty=true;
}
function toggleDetailStream(){
 detailStreamFrozen=detailStreamFrozen===null?detailRxFrames.slice():null;detailStreamPage=0;
 $('detailStreamPause').textContent=detailStreamFrozen===null?'暂停列表':'继续列表';renderDetailStream();
}
function clearDetailStream(){detailRxFrames=[];if(detailStreamFrozen!==null)detailStreamFrozen=[];detailStreamPage=0;renderDetailStream()}
function pageDetailStream(delta){
 if(detailStreamFrozen===null){detailStreamFrozen=detailRxFrames.slice();$('detailStreamPause').textContent='继续列表';}
 detailStreamPage+=delta;renderDetailStream();
}
function saveDetailStreamLimit(){
 const raw=$('detailStreamLimit').value.trim(),n=Number(raw);
 if(!raw||!Number.isInteger(n)||n<0||n>10000){$('detailStreamLimitNote').textContent='上限必须是 0–10000 的整数';return;}
 try{ConfigIO.writeField('historyLimits',{rx:n,tx:txSaveLimit});}catch(e){$('detailStreamLimitNote').textContent='保存失败，请重试';return;}
 rxSaveLimit=n;$('rxLimit').value=n;trimHistory();renderFrames();updateHistoryNote();
 if(detailRxFrames.length>n)detailRxFrames.length=n;
 if(detailStreamFrozen!==null&&detailStreamFrozen.length>n)detailStreamFrozen.length=n;
 $('detailStreamLimitNote').textContent='已保存：本通道 RX 上限 '+n+'；TX 上限不变。';renderDetailStream();
}
function renderDetailStream(){
 detailStreamDirty=false;const list=detailStreamFrozen===null?detailRxFrames:detailStreamFrozen;
 const pages=Math.max(1,Math.ceil(list.length/detailStreamPageSize));detailStreamPage=Math.max(0,Math.min(detailStreamPage,pages-1));
 $('detailStreamId').textContent=detailMotor?watchHex(detailMotor.config.feedbackId):'';
 $('detailStreamState').textContent=detailStreamFrozen!==null?'列表已暂停':!connected?'通道未连接':!detailReady?'等待订阅':'采样更新中';
 $('detailStreamPage').textContent=(detailStreamPage+1)+' / '+pages+' 页';
 $('detailStreamNewer').disabled=detailStreamPage===0;$('detailStreamOlder').disabled=detailStreamPage>=pages-1;
 const root=$('detailStreamFrames');root.replaceChildren();
 for(const f of list.slice(detailStreamPage*detailStreamPageSize,(detailStreamPage+1)*detailStreamPageSize)){
  const row=document.createElement('article');row.className='detail-can-frame';
  row.innerHTML='<div class="frame-top"><div class="frame-meta"><span class="direction">↙ RX</span><span class="can-id"></span><span class="frame-info"></span></div><span class="time"></span></div><div class="bytes"></div>';
  row.querySelector('.can-id').textContent=watchHex(f.id);row.querySelector('.frame-info').textContent='STD · DLC '+f.data.length;
  row.querySelector('.time').textContent=(f.stamp/1000).toFixed(3)+' s';
  for(const value of f.data){const byte=document.createElement('span');byte.className='byte';byte.textContent=value.toString(16).toUpperCase().padStart(2,'0');row.querySelector('.bytes').append(byte);}
  root.append(row);
 }
 if(!list.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=rxSaveLimit===0?'RX 上限为 0，不保留报文；可在下方修改。':!connected?'通道未连接，暂无报文。':'等待设备的反馈报文…';root.append(empty);}
}

function detailPeakPoints(points,key,span,start,width=400){
 if(points.length<=1600)return points;
 const out=[];let group=[],bucket=-1,last=null;
 function flush(){if(!group.length)return;let lo=0,hi=0;for(let i=1;i<group.length;i++){if(group[i].values[key]<group[lo].values[key])lo=i;if(group[i].values[key]>group[hi].values[key])hi=i;}for(const i of [...new Set([0,lo,hi,group.length-1])].sort((a,b)=>a-b))out.push(group[i]);group=[];}
 for(const p of points){const b=Math.floor((p.t-start)*width/span);if(b!==bucket||(last!==null&&p.t-last>Math.max(250,3000/detailAppliedHz)))flush();group.push(p);bucket=b;last=p.t;}flush();return out;
}

function detailLowerBound(points,time,first=0,after=false){let end=points.length;while(first<end){const mid=(first+end)>>>1;if(points[mid].t<time||(after&&points[mid].t===time))first=mid+1;else end=mid;}return first;}
