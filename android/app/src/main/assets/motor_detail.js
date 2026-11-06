'use strict';
// One active motor, bounded history. Samples carry the ESP32 CAN receive timestamp.
let detailMotor=null,detailSamples=[],detailClock=0,detailLastStamp=null,detailLastArrival=0,detailFrozen=null,detailReady=false,detailSupported=null,detailChannel='';
let detailSeries=[],detailFrozenSamples=[];
let detailSampleHead=0,detailMaxHz=0,detailAppliedHz=20,detailRatePrevious=null,detailRateAt=0,detailCanHz=null,detailSampleCountHz=null;
let detailAppCount=0,detailAppWindow=0,detailAppHz=null;
let detailChartKey=null,detailChartFocus=null,detailChartScroll=0;
let detailPaint=0;
let detailFrozenDraw=null;
const detailPlotObserver=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
 for(const entry of entries)entry.target.detailVisible=entry.isIntersecting;
 requestDetailPaint();
},{rootMargin:'120px'}):null;
function requestDetailPaint(){
 if(detailPaint||!detailMotor||document.hidden)return;
 detailPaint=requestAnimationFrame(()=>{detailPaint=0;if(!detailMotor||document.hidden)return;refreshDetail();if(!$('detailChartsPanel').hidden)drawDetail();if(!$('detailStreamPanel').hidden&&detailStreamDirty&&performance.now()-detailStreamPaintAt>=100)renderDetailStream()});
}
function cancelDetailPaint(){if(detailPaint)cancelAnimationFrame(detailPaint);detailPaint=0}
function detailChartSeries(){return detailChartKey===null?null:detailSeries.find(s=>s.key===detailChartKey)||null}
function detailSeriesValue(s){if(s.imu){const f=detailMotor&&detailMotor.feedback,v=f&&f.values[s.key],at=f&&f.times&&f.times[s.key];return Number.isFinite(v)&&at&&Date.now()-at<2000?Number(v.toFixed(4))+(s.unit?' '+s.unit:''):'—'}const f=detailMotor&&detailMotor.feedback;return f?(s.extra?f.values.temperature+' / '+f.values.mosTemperature+' °C':f[s.display]):'—'}
function refreshDetailChart(){
 const s=detailChartSeries();if(!s||!detailMotor)return;
 const c=detailMotor.config,p=Motors.profiles[c.profile],f=detailMotor.feedback;
 setFrameText($('detailChartTitle'),s.label);
 setFrameText($('detailChartMotor'),(c.name||p.name)+(p.family==='imu'?' · IMU ID ':' · 电机 ID ')+watchHex(c.id)+' · '+(detailChannel||$('detailSource').textContent||$('deviceName').textContent));
 const number=key=>f&&(!s.imu||(f.times&&Date.now()-f.times[key]<2000))&&Number.isFinite(f.values[key])?Number(f.values[key].toFixed(3)).toString():'—';
 setFrameText($('detailChartValue'),s.extra?number(s.key)+' / '+number(s.extra):number(s.key));
 setFrameText($('detailChartUnit'),s.unit);if($('detailChartLegend').hidden!==!s.extra)$('detailChartLegend').hidden=!s.extra;
 setFrameText($('detailChartState'),$('detailState').textContent+(c.profile==='c610'&&s.key==='temperature'?' · C610 不提供温度反馈':''));
 if($('detailChartState').className!==$('detailState').className)$('detailChartState').className=$('detailState').className;
 setFrameText($('detailChartPause'),detailFrozen===null?'暂停曲线':'继续曲线');
 if($('detailChartWindow').value!==$('detailWindow').value)$('detailChartWindow').value=$('detailWindow').value;
}
function openDetailChart(key){
 if(!detailMotor||$('detailChartsPanel').hidden)return false;
 const s=detailSeries.find(s=>s.key===key);if(!s)return false;
 const first=detailChartKey===null;if(first)detailChartScroll=window.scrollY;
 detailChartKey=key;detailChartFocus=s.card;
 $('detailChartFullscreen').hidden=false;document.body.classList.add('chart-fullscreen');
 const app=document.querySelector('.app');app.inert=true;app.setAttribute('aria-hidden','true');
 refreshDetailChart();drawDetail();$('detailChartClose').focus({preventScroll:true});
 // Native tabs may have restored their window before this page finishes its
 // hidden-page close. Reassert ownership even when switching an expanded plot.
 if(window.CanAndroid&&typeof CanAndroid.setChartFullscreen==='function')CanAndroid.setChartFullscreen(true);
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
document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelDetailPaint();closeDetailChartFullscreen()}else if(detailMotor)requestDetailPaint()});
window.addEventListener('resize',()=>{if(detailChartKey!==null)drawDetail()});
function detailRateKey(m){const c=m.config;return c.profile+':'+c.id+':'+c.feedbackId}
let detailRateCache;try{detailRateCache=ConfigIO.readField('detailRates')||{}}catch(e){detailRateCache={}}
window.detailRateFor=function(m){const hz=detailRateCache[detailRateKey(m)];return Number.isInteger(hz)&&hz>=1&&hz<=1000?hz:100};
function resetDetailRates(){detailRatePrevious=null;detailRateAt=0;detailCanHz=null;detailSampleCountHz=null;detailAppCount=0;detailAppWindow=performance.now();detailAppHz=null;}
function saveDetailSampleRate(){
 if(!detailMotor)return;const raw=$('detailSampleHz').value.trim(),hz=Number(raw);
 if(!raw||!Number.isInteger(hz)||hz<1||hz>1000){$('detailSampleResult').textContent='频率须为 1–1000 Hz 的整数';return;}
 if(!connected||!detailMaxHz){$('detailSampleResult').textContent='请连接并更新 ESP32 固件后设置';return;}
 if(hz>detailMaxHz){$('detailSampleResult').textContent='当前固件最高支持 '+detailMaxHz+' Hz';return;}
 try{const previous=detailRateCache,rates={};for(const m of watchedMotors){const key=detailRateKey(m);if(Number.isInteger(previous[key]))rates[key]=previous[key];}rates[detailRateKey(detailMotor)]=hz;ConfigIO.writeField('detailRates',rates);detailRateCache=rates;}catch(e){$('detailSampleResult').textContent='保存失败，请重试';return;}
 if(detailReady&&hz===detailAppliedHz){$('detailSampleResult').textContent='已应用上报上限 '+hz+' Hz';publishDashboard();return;}
 detailReady=false;clearDetail();resetDetailStream();resetDetailRates();$('detailSampleResult').textContent='正在应用 '+hz+' Hz…';publishDashboard();
}
window.motorDetailRates=function(d){
 if(!detailMotor||!connected||!detailReady||d.key!==JSON.stringify(detailMotor.config))return;
 if(detailRatePrevious){const ms=(d.elapsed-detailRatePrevious.elapsed)>>>0;if(ms>0&&ms<=5000){detailCanHz=((d.received-detailRatePrevious.received)>>>0)*1000/ms;detailSampleCountHz=((d.sampled-detailRatePrevious.sampled)>>>0)*1000/ms;}}
 detailRatePrevious=d;detailRateAt=Date.now()-Math.max(0,Number(d.age)||0);
};
function clearDetail(){detailFrozenDraw=null;detailSampleHead=0;detailFrozenSamples=[];detailSamples=[];detailClock=0;detailLastStamp=null;detailLastArrival=0;detailFrozen=null;$('detailPause').textContent='暂停曲线';drawDetail()}
function openMotorDetail(m){
 closeDetailChartFullscreen();
 if(detailPlotObserver)detailPlotObserver.disconnect();
 detailMotor=m;activeWatch=m;detailReady=false;resetDetailRates();$('detailSampleHz').value=detailRateFor(m);$('detailSampleResult').textContent='';m.feedback=null;m.time=0;
 document.body.classList.add('motor-detail');document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!=='view-detail');
 const c=m.config,p=Motors.profiles[c.profile],dm=p.family==='dm';
 $('detailName').textContent=c.name||p.name;$('detailAddress').textContent=p.name+(p.family==='imu'?' · IMU ID ':' · 电机 ID ')+watchHex(c.id)+' · RX '+watchHex(c.feedbackId);
 $('detailSource').textContent=detailChannel||$('deviceName').textContent;
 $('detailQueryPanel').hidden=!dm;$('detailHz').value=c.pollHz||100;
 detailSeries=p.rawAngle?[{label:'机械角度',key:'position',unit:'raw',display:'angle'},{label:'输出转矩',key:'effort',unit:'raw',display:'effort'}]:p.family==='imu'?Motors.imuSeries():[{label:dm?'位置':'转子角度',key:'position',unit:dm?'rad':'°',display:'angle'},{label:'速度',key:'speed',unit:dm?'rad/s':'rpm',display:'speed'},{label:dm?'扭矩':'电流反馈',key:'effort',unit:dm?'N·m':'raw',display:'effort'},{label:dm?'温度 · 电机 / MOS':'温度',key:'temperature',extra:dm?'mosTemperature':null,unit:'°C',display:'temperature'}];
 const root=$('detailPlots');root.replaceChildren();for(const s of detailSeries){const card=document.createElement('article');card.className='detail-plot';card.tabIndex=0;card.setAttribute('role','button');card.setAttribute('aria-haspopup','dialog');card.setAttribute('aria-controls','detailChartFullscreen');card.setAttribute('aria-label','放大'+s.label+'曲线');card.innerHTML='<div class="detail-plot-title"><h3></h3><span class="detail-expand" aria-hidden="true">↗</span></div><div class="detail-number">—</div><div class="detail-scale"><span></span><span></span></div><svg viewBox="0 0 400 140" preserveAspectRatio="none" role="img"><path d="M0 20H400 M0 70H400 M0 120H400" stroke="#243044" stroke-dasharray="3 5"/><path class="trace" stroke="#65e6cb" stroke-width="2" fill="none" vector-effect="non-scaling-stroke"/><path class="trace-extra" stroke="#83aaff" stroke-width="2" fill="none" vector-effect="non-scaling-stroke"/></svg><div class="detail-axis"><span></span><span></span></div>';card.querySelector('h3').textContent=s.label+' · '+s.unit;card.querySelector('svg').setAttribute('aria-label',s.label+'动态曲线');card.addEventListener('click',()=>openDetailChart(s.key));card.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openDetailChart(s.key)}});root.append(card);s.card=card;}
 if(detailPlotObserver)for(const s of detailSeries)detailPlotObserver.observe(s.card);
 clearDetail();resetDetailStream();showDetailTab('charts');publishDashboard();if(window.CanAndroid&&typeof CanAndroid.showDetail==='function')CanAndroid.showDetail(JSON.stringify(c));
 window.scrollTo(0,0);refreshDetail();
}
function closeMotorDetail(notify=true,target='motor'){closeDetailChartFullscreen();if(!detailMotor)return;if(detailPlotObserver)detailPlotObserver.disconnect();cancelDetailPaint();detailMotor=null;detailReady=false;clearDetail();resetDetailStream();document.body.classList.remove('motor-detail');renderChannelTab(target);if(notify&&window.CanAndroid&&typeof CanAndroid.leaveDetail==='function')CanAndroid.leaveDetail()}
function detailHome(){closeMotorDetail(false);if(window.CanAndroid&&typeof CanAndroid.home==='function')CanAndroid.home();else showTab('motor')}
function detailRetarget(m){if(m!==detailMotor)return;closeDetailChartFullscreen();detailReady=false;clearDetail();resetDetailStream();if(window.CanAndroid&&typeof CanAndroid.showDetail==='function')CanAndroid.showDetail(JSON.stringify(m.config))}
window.motorDetailSupport=d=>{detailSupported=!!d.supported;detailMaxHz=d.maxHz||0;detailChannel=d.channel||'';if(detailMotor)$('detailSource').textContent=detailChannel;refreshDetail()};
window.motorDetailMode=d=>{if(detailMotor){if(d.mode!==2)closeDetailChartFullscreen();detailReady=d.mode===2;detailAppliedHz=d.hz||20;resetDetailRates();$('detailSampleResult').textContent=detailReady?'已应用上报上限 '+detailAppliedHz+' Hz':'';clearDetail();resetDetailStream();detailMotor.feedback=null;detailMotor.time=0;refreshDetail()}};
function appendDetailSample(m,hex,age){
 const a=parsePacket(hex);if(!a||a[1]!==11||(a[2]&3)||a[3]>8)return;
 const c=m.config,p=Motors.profiles[c.profile],data=Array.from(a.slice(8,8+a[3]));let f;
 try{f=p.family==='imu'?Motors.imuFeedback(c.feedbackId,c.feedbackId,data):p.family==='dji'?Motors.djiFeedback(c.profile,c.id,c.feedbackId,data,c.feedbackId):Motors.dmFeedback(c.id,c.feedbackId,c.feedbackId,data,c.limits)}catch(e){return}
 if(!f||!f.values)return;
 const stamp=u32(a,16),now=Date.now()-Math.max(0,Number(age)||0);if(detailLastStamp!==null){const delta=(stamp-detailLastStamp)>>>0;if(delta>0x7fffffff){clearDetail();resetDetailStream();}else detailClock+=delta;}
 detailLastStamp=stamp;detailLastArrival=now;detailAppCount++;recordDetailFrame(c.feedbackId,data,stamp);if(p.family==='imu'){const old=m.feedback;const times={...(old&&old.times)};for(const k of Object.keys(f.values))times[k]=now;m.feedback={...f,values:{...(old&&old.values),...f.values},times};}else m.feedback=f;m.time=now;watchedDirty=true;
 detailSamples.push({t:detailClock,values:f.values});
 while(detailSamples.length-detailSampleHead>60000||(detailSamples.length-detailSampleHead>1&&detailClock-detailSamples[detailSampleHead].t>60000))detailSampleHead++;
 if(detailSampleHead>=2048){detailSamples=detailSamples.slice(detailSampleHead);detailSampleHead=0;}
 // Record every sample; paint the latest state once per screen refresh.
 requestDetailPaint();
}
window.motorDetailSample=function(d){const m=detailMotor;if(!m||!connected||!detailReady||JSON.stringify(m.config)!==d.key)return;appendDetailSample(m,d.hex,d.age)};
window.motorDetailSamples=function(d,deliveryAge){
 // A native batch has one config key. Validate it once before synchronous
 // delivery; each sample still retains its own bytes, age and timestamp.
 const m=detailMotor;if(!m||!connected||!detailReady||JSON.stringify(m.config)!==d.key)return;
 for(const item of d.items||[])appendDetailSample(m,item.hex,(item.age||0)+deliveryAge);
};
function detailTogglePause(){detailFrozenSamples=detailFrozen===null?detailSamples.slice(detailSampleHead):[];detailFrozen=detailFrozen===null?detailEnd():null;$('detailPause').textContent=detailFrozen===null?'暂停曲线':'继续曲线';drawDetail()}
function detailEnd(){return detailClock+(detailLastArrival?Date.now()-detailLastArrival:0)}
function refreshDetail(){
 if(!detailMotor)return;
 const tick=performance.now(),dt=tick-detailAppWindow;if(dt>=1000){detailAppHz=detailAppCount*1000/dt;detailAppCount=0;detailAppWindow=tick;}
 const rateText=v=>v===null?'— Hz':v.toFixed(1)+' Hz';
 setFrameText($('detailCanRate'),rateText(connected&&Date.now()-detailRateAt<3000?detailCanHz:null));
 setFrameText($('detailAppRate'),rateText(connected?detailAppHz:null));
 $('detailSampleSave').disabled=!connected||!detailMaxHz||watchBusy();
 $('detailSampleHz').disabled=watchBusy();
 setFrameText($('detailRateNote'),!detailMaxHz?'当前固件固定 20 Hz，更新 ESP32 后支持可调频率。':!detailReady?'等待订阅确认…':'上报上限 '+detailAppliedHz+' Hz · '+(detailSampleCountHz===null?'等待设备统计':'设备采样 '+detailSampleCountHz.toFixed(1)+' Hz')+' · 逐帧记录，按屏幕刷新绘图');
 const m=detailMotor,fresh=connected&&m.time&&Date.now()-m.time<2000;
 setFrameText($('detailState'),!connected?'通道未连接':!m.time?'等待设备反馈':fresh?'反馈在线 · '+m.feedback.state:'反馈超时 · 保留最后一次读数');const stateClass='detail-status'+(fresh?' fresh':m.time&&connected?' stale':'');if($('detailState').className!==stateClass)$('detailState').className=stateClass;
 setFrameText($('detailNotice'),!connected?'返回首页连接该通道后查看反馈。':detailSupported===false?'请更新该通道的 ESP32 固件以启用单电机订阅。':!detailReady?'正在订阅设备…':Motors.profiles[m.config.profile].family==='imu'?'仅接收主动上报；各类数据独立更新，超过 2 秒未更新显示 —。上报上限按四类数据分配；CAN 数据流显示采样后的帧。':Motors.profiles[m.config.profile].rawAngle?'GM3510 仅上报角度与转矩原始值，无速度和温度字段。':detailMotor.config.profile==='c610'?'C610 不提供温度反馈。':Motors.profiles[m.config.profile].family==='dm'?'温度曲线：绿色为电机，蓝色为 MOS。':'角度为转子单圈角度；电流显示协议原始值 raw。');
 const dm=Motors.profiles[m.config.profile].family==='dm';if(dm){$('detailQuery').checked=m.queryPending?!!m.queryTarget:!!m.polling;$('detailQuery').disabled=!connected||watchBusy()||!window.devicePollingSupported||!!m.queryPending;$('detailHz').disabled=watchBusy()||!!m.queryPending;if(document.activeElement!==$('detailHz'))$('detailHz').value=m.config.pollHz;setFrameText($('detailQueryNote'),m.queryMessage||'查询默认关闭；开启后 ESP32 按设定频率问询此电机。修改频率会停止查询。');}
 for(const s of detailSeries)setFrameText(s.number||(s.number=s.card.querySelector('.detail-number')),detailSeriesValue(s));
 refreshDetailChart();
}
function drawDetail(){
 if(!detailMotor)return;const end=detailFrozen===null?detailEnd():detailFrozen,span=Number($('detailWindow').value)*1000,start=end-span;
 const history=detailFrozen===null?detailSamples:detailFrozenSamples;
 const expanded=detailChartSeries(),svg=expanded?$('detailChartSvg'):null;
 if(expanded)refreshDetailChart();
 const width=expanded?Math.max(1,Math.round(svg.clientWidth)):400,height=expanded?Math.max(1,Math.round(svg.clientHeight)):140;
 const visible=expanded?'':detailSeries.map(s=>s.card.detailVisible!==false?'1':'0').join('');
 const cached=detailFrozenDraw;
 // Frozen history is immutable. Keep live numbers and sample storage running,
 // but do not rescan it until the user changes the window, plot or geometry.
 if(detailFrozen!==null&&cached&&cached.history===history&&cached.series===detailSeries&&cached.expanded===expanded&&cached.start===start&&cached.end===end&&cached.width===width&&cached.height===height&&cached.visible===visible)return;
 const first=detailLowerBound(history,start,detailFrozen===null?detailSampleHead:0),last=detailLowerBound(history,end,first,true);
 const points=history.slice(first,last);
 if(expanded){
  const viewBox='0 0 '+width+' '+height;if(svg.getAttribute('viewBox')!==viewBox)svg.setAttribute('viewBox',viewBox);
  const inset=Math.min(15,height*.1),lines=[];for(let i=0;i<5;i++){const y=inset+(height-2*inset)*i/4;lines.push('M0 '+y.toFixed(2)+'H'+width);}
  const grid=lines.join(' ');if($('detailChartGrid').getAttribute('d')!==grid)$('detailChartGrid').setAttribute('d',grid);
  drawDetailSeries(expanded,$('detailChartFullscreen'),points,span,start,width,height);
 }else for(const s of detailSeries)if(s.card.detailVisible!==false)drawDetailSeries(s,s.card,points,span,start,400,140);
 detailFrozenDraw=detailFrozen===null?null:{history,series:detailSeries,expanded,start,end,width,height,visible};
}
function drawDetailSeries(s,root,points,span,start,width,height){
 const nodes=root.detailNodes||(root.detailNodes={trace:root.querySelector('.trace'),extra:root.querySelector('.trace-extra'),scale:root.querySelectorAll('.detail-scale span'),axis:root.querySelectorAll('.detail-axis span')});
 const fmt=v=>Math.abs(v)>=10000?v.toExponential(1):Number(v.toFixed(3)).toString();
 // IMU samples carry one subtype; retain its sparse finite-point path.
 // Motor curves collect bounds while selecting extrema, scanning once.
 const primary=s.imu?[]:points;let count=0,lo=Infinity,hi=-Infinity,selected=null,selectedExtra=null;
 if(s.imu){for(const p of points){const v=p.values[s.key];if(Number.isFinite(v)){count++;lo=Math.min(lo,v);hi=Math.max(hi,v);primary.push(p);}if(s.extra){const extra=p.values[s.extra];if(Number.isFinite(extra)){count++;lo=Math.min(lo,extra);hi=Math.max(hi,extra);}}}}
 else{const bounds={};selected=detailBoundedPeakPoints(primary,s.key,span,start,width,bounds,s.extra);selectedExtra=s.extra?detailPeakPoints(primary,s.extra,span,start,width):[];({count,lo,hi}=bounds);}
 if(!count){lo=0;hi=1;}if(hi===lo){lo-=1;hi+=1;}const pad=(hi-lo)*.08;lo-=pad;hi+=pad;
 const inset=Math.min(15,height*.1),plotHeight=height-2*inset,gap=Math.max(250,3000/detailAppliedHz);
 const path=key=>{let result='',last=null;for(const p of s.imu?detailPeakPoints(primary,key,span,start,width):key===s.key?selected:selectedExtra){const v=p.values[key];if(!Number.isFinite(v)){last=null;continue}const x=(p.t-start)/span*width,y=height-inset-(v-lo)/(hi-lo)*plotHeight;result+=(last===null||p.t-last>gap?'M':'L')+x.toFixed(2)+','+y.toFixed(2);if(last===null||p.t-last>gap)result+='l.1,0';last=p.t;}return result;};
 const setPath=(node,value)=>{if(node.getAttribute('d')!==value)node.setAttribute('d',value)};
 setPath(nodes.trace,path(s.key));setPath(nodes.extra,s.extra?path(s.extra):'');setFrameText(nodes.scale[0],count?fmt(lo)+' '+s.unit:'等待数据');setFrameText(nodes.scale[1],count?fmt(hi)+' '+s.unit:'');setFrameText(nodes.axis[0],(span<1000?span+' ms':span/1000+' 秒')+'前');setFrameText(nodes.axis[1],detailFrozen===null?'现在':'已暂停');
}
function detailQueryToggle(enabled){if(detailMotor)setMotorPolling(JSON.stringify(detailMotor.config),enabled)}
function detailQueryRate(hz){if(detailMotor)setMotorQueryHz(JSON.stringify(detailMotor.config),hz);refreshDetail()}
setInterval(()=>{if(!detailMotor)return;if(!connected){closeDetailChartFullscreen();if(detailSamples.length||detailReady){detailReady=false;clearDetail();resetDetailStream();resetDetailRates();}}requestDetailPaint()},100);

// Reuse event11 payloads: no second subscription or additional BLE traffic.
const detailFrameHistory=new RingBuffer(rxSaveLimit);
Object.defineProperty(window,'detailRxFrames',{get:()=>detailFrameHistory.snapshot()});
let detailStreamFrozen=null,detailStreamPage=0,detailStreamDirty=true;
let detailStreamPaintAt=0;
const detailStreamPageSize=40;
const detailStreamRows=[];
let detailStreamEmpty=null;
function showDetailTab(tab){
 closeDetailChartFullscreen();
 const stream=tab==='stream';$('detailChartsPanel').hidden=stream;$('detailStreamPanel').hidden=!stream;
 $('detailChartsTab').setAttribute('aria-selected',String(!stream));$('detailStreamTab').setAttribute('aria-selected',String(stream));
 if(stream)renderDetailStream();else drawDetail();
}
function resetDetailStream(){
 detailFrameHistory.resize(rxSaveLimit);detailFrameHistory.clear();detailStreamFrozen=null;detailStreamPage=0;detailStreamDirty=true;
 $('detailStreamPause').textContent='暂停列表';$('detailStreamLimit').value=rxSaveLimit;
 $('detailStreamLimitNote').textContent='沿用本通道 RX 上限，0 表示不保留；暂停仅冻结列表显示。';renderDetailStream();
}
function recordDetailFrame(id,data,stamp){
 detailFrameHistory.resize(rxSaveLimit);detailFrameHistory.push({id,data,stamp});
 if(detailStreamFrozen===null)detailStreamDirty=true;
}
function toggleDetailStream(){
 detailStreamFrozen=detailStreamFrozen===null?detailRxFrames.slice():null;detailStreamPage=0;
 $('detailStreamPause').textContent=detailStreamFrozen===null?'暂停列表':'继续列表';renderDetailStream();
}
function clearDetailStream(){detailFrameHistory.clear();if(detailStreamFrozen!==null)detailStreamFrozen=[];detailStreamPage=0;renderDetailStream()}
function pageDetailStream(delta){
 if(detailStreamFrozen===null){detailStreamFrozen=detailRxFrames.slice();$('detailStreamPause').textContent='继续列表';}
 detailStreamPage+=delta;renderDetailStream();
}
function saveDetailStreamLimit(){
 const raw=$('detailStreamLimit').value.trim(),n=Number(raw);
 if(!raw||!Number.isInteger(n)||n<0||n>10000){$('detailStreamLimitNote').textContent='上限必须是 0–10000 的整数';return;}
 try{ConfigIO.writeField('historyLimits',{rx:n,tx:txSaveLimit});}catch(e){$('detailStreamLimitNote').textContent='保存失败，请重试';return;}
 rxSaveLimit=n;$('rxLimit').value=n;trimHistory();renderFrames();updateHistoryNote();
 detailFrameHistory.resize(n);
 if(detailStreamFrozen!==null&&detailStreamFrozen.length>n)detailStreamFrozen.length=n;
 $('detailStreamLimitNote').textContent='已保存：本通道 RX 上限 '+n+'；TX 上限不变。';renderDetailStream();
}
function renderDetailStream(){
 detailStreamPaintAt=performance.now();
 detailStreamDirty=false;const list=detailStreamFrozen===null?detailRxFrames:detailStreamFrozen;
 const pages=Math.max(1,Math.ceil(list.length/detailStreamPageSize));detailStreamPage=Math.max(0,Math.min(detailStreamPage,pages-1));
 $('detailStreamId').textContent=detailMotor?watchHex(detailMotor.config.feedbackId):'';
 $('detailStreamState').textContent=detailStreamFrozen!==null?'列表已暂停':!connected?'通道未连接':!detailReady?'等待订阅':'采样更新中';
 $('detailStreamPage').textContent=(detailStreamPage+1)+' / '+pages+' 页';
 $('detailStreamNewer').disabled=detailStreamPage===0;$('detailStreamOlder').disabled=detailStreamPage>=pages-1;
 const root=$('detailStreamFrames');
 if(!list.length){
  if(!detailStreamEmpty){detailStreamEmpty=document.createElement('div');detailStreamEmpty.className='empty';}
  setFrameText(detailStreamEmpty,rxSaveLimit===0?'RX 上限为 0，不保留报文；可在下方修改。':!connected?'通道未连接，暂无报文。':'等待设备的反馈报文…');
  if(root.firstChild!==detailStreamEmpty)root.replaceChildren(detailStreamEmpty);
  return;
 }
 if(detailStreamEmpty&&detailStreamEmpty.parentNode===root)detailStreamEmpty.remove();
 const visible=list.slice(detailStreamPage*detailStreamPageSize,(detailStreamPage+1)*detailStreamPageSize);
 visible.forEach((f,i)=>{
  let row=detailStreamRows[i];
  if(!row){
   row=document.createElement('article');row.className='detail-can-frame';
   row.innerHTML='<div class="frame-top"><div class="frame-meta"><span class="direction">↙ RX</span><span class="can-id"></span><span class="frame-info"></span></div><span class="time"></span></div><div class="bytes"></div>';
   row.parts={id:row.querySelector('.can-id'),info:row.querySelector('.frame-info'),time:row.querySelector('.time'),bytes:row.querySelector('.bytes')};detailStreamRows[i]=row;
  }
  if(row.frame!==f){
   const p=row.parts;setFrameText(p.id,watchHex(f.id));setFrameText(p.info,'STD · DLC '+f.data.length);setFrameText(p.time,(f.stamp/1000).toFixed(3)+' s');
   while(p.bytes.children.length>f.data.length)p.bytes.lastChild.remove();
   f.data.forEach((value,j)=>{let byte=p.bytes.children[j];if(!byte){byte=document.createElement('span');byte.className='byte';p.bytes.append(byte)}setFrameText(byte,value.toString(16).toUpperCase().padStart(2,'0'))});row.frame=f;
  }
  if(root.children[i]!==row)root.append(row);
 });
 while(root.children.length>visible.length)root.lastChild.remove();
}

function detailPeakPoints(points,key,span,start,width=400){
 if(points.length<=1600)return points;
 // Retain indices into the immutable history instead of allocating an array
 // for every pixel bucket; gaps still terminate a bucket immediately.
 // Cache bucket values as well as indices; an invalid first value keeps
 // the original comparison semantics until the next bucket.
 const out=[];const gap=Math.max(250,3000/detailAppliedHz);let first=0,lo=0,hi=0,low,high,bucket=-1,last=null;
 function flush(end){
  if(end<first)return;
  out.push(points[first]);
  const a=lo<hi?lo:hi,b=lo<hi?hi:lo;
  if(a>first&&a<end)out.push(points[a]);
  if(b>a&&b<end)out.push(points[b]);
  if(end>first)out.push(points[end]);
 }
 for(let i=0;i<points.length;i++){
  const p=points[i],v=p.values[key],b=Math.floor((p.t-start)*width/span);
  if(i===0||b!==bucket||(last!==null&&p.t-last>gap)){flush(i-1);first=lo=hi=i;low=high=v;}
  else {if(v<low){lo=i;low=v;}if(v>high){hi=i;high=v;}}
  bucket=b;last=p.t;
 }
 flush(points.length-1);return out;
}

// Keep this motor bounds scan separate from the sparse IMU reducer. Sharing
// an optional bounds branch in its hot loop regressed the IMU benchmark.
function detailBoundedPeakPoints(points,key,span,start,width=400,bounds,extra=null){
 let count=0,min=Infinity,max=-Infinity;
 if(points.length<=1600){
  if(bounds){for(const p of points){const v=p.values[key];if(Number.isFinite(v)){count++;min=Math.min(min,v);max=Math.max(max,v);}if(extra){const v=p.values[extra];if(Number.isFinite(v)){count++;min=Math.min(min,v);max=Math.max(max,v);}}}Object.assign(bounds,{count,lo:min,hi:max});}
  return points;
 }
 // Retain indices into the immutable history instead of allocating an array
 // for every pixel bucket; gaps still terminate a bucket immediately.
 // Cache bucket values as well as indices; an invalid first value keeps
 // the original comparison semantics until the next bucket.
 const out=[];const gap=Math.max(250,3000/detailAppliedHz);let first=0,lo=0,hi=0,low,high,bucket=-1,last=null;
 function flush(end){
  if(end<first)return;
  out.push(points[first]);
  const a=lo<hi?lo:hi,b=lo<hi?hi:lo;
  if(a>first&&a<end)out.push(points[a]);
  if(b>a&&b<end)out.push(points[b]);
  if(end>first)out.push(points[end]);
 }
 for(let i=0;i<points.length;i++){
  const p=points[i],v=p.values[key],b=Math.floor((p.t-start)*width/span);
  if(bounds){if(Number.isFinite(v)){count++;min=Math.min(min,v);max=Math.max(max,v);}if(extra){const v=p.values[extra];if(Number.isFinite(v)){count++;min=Math.min(min,v);max=Math.max(max,v);}}}
  if(i===0||b!==bucket||(last!==null&&p.t-last>gap)){flush(i-1);first=lo=hi=i;low=high=v;}
  else {if(v<low){lo=i;low=v;}if(v>high){hi=i;high=v;}}
  bucket=b;last=p.t;
 }
 flush(points.length-1);if(bounds)Object.assign(bounds,{count,lo:min,hi:max});return out;
}

function detailLowerBound(points,time,first=0,after=false){let end=points.length;while(first<end){const mid=(first+end)>>>1;if(points[mid].t<time||(after&&points[mid].t===time))first=mid+1;else end=mid;}return first;}
