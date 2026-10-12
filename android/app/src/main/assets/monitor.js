'use strict';
const $=id=>document.getElementById(id);
let connected=false,paused=false,dirty=false,toastTimer,deviceMap=new Map(),lastStatus=0,firmwareDrops=0,appDrops=0;
const frameHistory=new FrameHistory();
Object.defineProperty(window,'frames',{get:()=>frameHistory.snapshot(),set:records=>frameHistory.replace(records)});
let frameFilter='all',displayedFrameHistory=[],frozenFrameHistory=null;
let cleanFailureCount=null;window.cleanTxCounters=false;
let bucketRx=0,bucketTx=0,rxHistory=Array(30).fill(0),txHistory=Array(30).fill(0),gotStatus=false;
const native=()=>window.CanAndroid;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500)}
function invoke(name,...args){if(native()&&typeof native()[name]==='function')native()[name](...args);else if(!native())toast('请在安卓 App 中连接设备；此处为界面预览。')}
function connectAction(){if(connected){invoke('disconnect');return} $('deviceModal').hidden=false;scan()}
function scan(){deviceMap.clear();$('deviceList').replaceChildren();$('scanHint').textContent=typeof transportMode!=='undefined'&&transportMode==='wifi'?'正在寻找局域网中的已配对设备…':'正在扫描附近的 ESP32…';invoke('scan')}
function closeDevices(){$('deviceModal').hidden=true}
function renderChannelTab(name){document.querySelectorAll('.tabs .tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!=='view-'+name);if(native()&&typeof native().setMonitorVisible==='function')native().setMonitorVisible(name==='live')}
function showTab(name){if(!['motor','live','settings'].includes(name))return;if(window.closeMotorDetail)closeMotorDetail(true);renderChannelTab(name)}
function setConnection(d){
 connected=d.value==='ready';$('connectionMessage').textContent=d.message||'';$('badgeText').textContent=connected?'已连接':d.value==='connecting'?'连接中':d.value==='scanning'?'扫描中':'未连接';$('connectionBadge').classList.toggle('online',connected);$('connectButton').textContent=connected?'断开连接':'连接设备';$('connectButton').disabled=d.value==='connecting';updateFrameViewState();$('deviceName').textContent=connected?d.name:'连接 CAN 设备';
 resetMonitorMetrics();gotStatus=false;lastStatus=Date.now();rxHistory.fill(0);txHistory.fill(0);bucketRx=bucketTx=0;
 if(window.deviceConnection)deviceConnection();if(window.resetWatchedFeedback)resetWatchedFeedback();
 if(connected)closeDevices();else $('busStatus').textContent='未连接';if(d.value==='idle')$('scanHint').textContent=d.message||'';
}
function u32(a,o){return (a[o]|a[o+1]<<8|a[o+2]<<16|a[o+3]<<24)>>>0}
function put32(a,o,n){for(let i=0;i<4;i++)a[o+i]=(n>>>(8*i))&255}
function hexOf(a){return Array.from(a,x=>x.toString(16).padStart(2,'0')).join('')}
function parsePacket(hex){if(!/^[0-9a-f]{40}$/i.test(hex))return null;const a=Uint8Array.from(hex.match(/../g),x=>parseInt(x,16));if(a[0]!==1)return null;return a}
function processPacket(hex,age=0){
 const a=parsePacket(hex);if(!a)return;const kind=a[1];if(window.devicePacket)devicePacket(a);if(window.watchedPacket)watchedPacket(a,age);
 if(kind===4){gotStatus=true;lastStatus=Date.now()-Math.max(0,age);cleanFailureCount=u32(a,12);renderFailureCount();firmwareDrops=u32(a,16);$('busStatus').textContent=['正常 · Error active','错误警告 · Error warning','错误被动 · Error passive','总线离线 · 正在恢复'][a[2]]||'未知状态';$('dropStatus').textContent='设备 '+firmwareDrops+' · App '+appDrops;updateHistoryNote();return}
 if(kind===13){receiveDeviceLoad(a,age);return}
 if(kind===14){receiveDeviceRates(a,age);return}
 if(kind===3&&(a[2]&0x80)&&!(a[2]&0x7c))return; // pure arbitration is diagnostic only
 if(![1,2,3].includes(kind)||a[3]>8)return;if(kind===1)bucketRx++;if(kind===2)bucketTx++;
 frameHistory.push({kind,id:u32(a,4),flags:a[2],ext:!!(a[2]&1),rtr:!!(a[2]&2),dlc:a[3],data:Array.from(a.slice(8,8+((a[2]&2)?0:a[3]))),receivedAt:Date.now()-Math.max(0,age),seq:u32(a,16)});dirty=true;
}
window.onNative=function(type,d){
 const deliveryAge=Number.isFinite(d.sentAt)?Math.max(0,Date.now()-d.sentAt):0;if(Number.isFinite(d.age))d={...d,age:d.age+deliveryAge};
 if(window.networkEvent)networkEvent(type,d);
 if(type==='busLoad'||type==='busRates'){processPacket(d.hex,d.age||0)}else if(type==='busLoadReset'){clearDeviceLoad();clearDeviceRates()}else if(type==='motorRates'){if(window.motorDetailRates)motorDetailRates(d)}else if(type==='motorSample'){if(window.motorDetailSample)motorDetailSample(d)}else if(type==='detailSupport'){if(window.motorDetailSupport)motorDetailSupport(d)}else if(type==='telemetryMode'){monitorMode=Number(d.mode);clearDeviceLoad();clearDeviceRates();if(window.motorDetailMode)motorDetailMode(d);if(window.motorTelemetryMode)motorTelemetryMode(d)}else if(type==='onlineSummary'){if(window.motorOnlineSummary)motorOnlineSummary(d)}else if(type==='motorTemperatures'){if(window.motorTemperatures)motorTemperatures(d)}else if(type==='queryState'){if(window.motorQueryState)motorQueryState(d)}else if(type==='state')setConnection(d);else if(type==='deviceInfo')$('deviceName').textContent=d.name;
 else if(type==='error'){toast(d.message);if(window.deviceWriteFailed)deviceWriteFailed(d.message)}
 else if(type==='scanStart'){deviceMap.clear();$('deviceList').replaceChildren()}
 else if(type==='device'){deviceMap.set(d.address,d);renderDevices()}
 else if(type==='packets'){appDrops=d.dropped||0;d.items.forEach(item=>typeof item==='string'?processPacket(item):processPacket(item.hex,(item.age||0)+deliveryAge))}
 else if(type==='motorSamples'){appDrops=d.dropped||0;for(const item of d.packets||[])processPacket(item.hex,(item.age||0)+deliveryAge);for(const item of d.items||[])if(window.motorDetailSample)motorDetailSample({key:d.key,hex:item.hex,age:(item.age||0)+deliveryAge});}
 else if(type==='writeDone'&&!d.success&&window.deviceWriteFailed)deviceWriteFailed('设备未接受设置，请重试');
};
function renderDevices(){const root=$('deviceList');root.replaceChildren();for(const d of deviceMap.values()){const button=document.createElement('button');button.className='device';const text=document.createElement('div'),name=document.createElement('strong'),addr=document.createElement('small'),signal=document.createElement('span');name.textContent=d.name;addr.textContent=d.address;text.append(name,addr);signal.className='chip';signal.textContent=d.transport==='wifi'?'Wi-Fi':d.rssi+' dBm';button.append(text,signal);button.onclick=()=>{$('scanHint').textContent='正在连接 '+d.name+'…';invoke('connect',d.address)};root.append(button)}}
function selectFrameFilter(value){
 if(!['all','rx','tx','fail'].includes(value))return;
 frameFilter=value;renderFrames();
}
function updateFrameViewState(){
 const titles={all:'数据流',rx:'接收 RX 数据流',tx:'成功 TX 数据流',fail:'失败原因'};
 $('frameViewTitle').textContent=titles[frameFilter];
 $('streamState').textContent=paused?'列表已暂停 · 统计继续':connected?'实时更新':'等待连接';
 $('failureNote').hidden=frameFilter!=='fail';
 document.querySelectorAll('[data-frame-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.frameFilter===frameFilter)));
}
function failureReasons(frame){
 if(frame.kind!==3)return [];
 const bits=frame.flags||0,reasons=[];
 for(const [bit,label] of [[4,'未收到应答（ACK）'],[8,'位错误'],[16,'格式错误'],[32,'位填充错误'],[64,'驱动拒绝发送']])if(bits&bit)reasons.push(label);
 return reasons.length?reasons:['设备未提供具体原因'];
}
function frameBytes(frame){
 const bytes=document.createElement('div');bytes.className='bytes';
 for(const value of frame.data||[]){const byte=document.createElement('span');byte.className='byte';byte.textContent=value.toString(16).toUpperCase().padStart(2,'0');bytes.append(byte)}
 if(!frame.data||!frame.data.length){const note=document.createElement('span');note.className='mini';note.textContent=frame.rtr?'远程请求 · 无数据字段':'零长度数据帧';bytes.append(note)}
 return bytes;
}
function renderFrames(){
 updateFrameViewState();
 if(!paused){displayedFrameHistory=frames.slice();dirty=false;}
 else if(frozenFrameHistory!==null)frozenFrameHistory=boundedFrameHistory(frozenFrameHistory);
 const history=paused?(frozenFrameHistory||[]):frames;
 const query=$('filter').value.trim().replace(/^0x/i,'').toUpperCase(),kind={rx:1,tx:2,fail:3}[frameFilter];
 const list=history.filter(frame=>(!kind||frame.kind===kind)&&(!query||frame.id.toString(16).toUpperCase().includes(query)));
 $('frameCount').textContent=list.length+' 条';
 const root=$('frames');root.replaceChildren();
 if(!list.length){
  const empty=document.createElement('div');empty.className='empty';
  empty.textContent=query?'没有匹配此 CAN ID 的'+(frameFilter==='fail'?'失败事件。':'报文。'):frameFilter==='fail'?'暂无保留的失败事件。设备累计次数可能与此列表不同。':paused?'暂停快照中暂无'+(frameFilter==='rx'?'RX':frameFilter==='tx'?'成功 TX':'CAN')+'报文。':!connected?'连接 ESP32 后，CAN 帧会显示在这里。':frameFilter==='rx'?'等待接收 RX 报文…':frameFilter==='tx'?'等待成功发送的 TX 报文…':'等待 CAN 数据；请检查对端是否正在发送。';
  root.append(empty);return;
 }
 for(const frame of list.slice(0,80)){
  const row=document.createElement('article');row.className='frame'+(frame.kind===3?' failure-frame':'');
  if(frame.kind===3){const reason=document.createElement('h4');reason.className='failure-reason';reason.textContent=failureReasons(frame).join(' · ');row.append(reason);}
  const top=document.createElement('div');top.className='frame-top';
  const meta=document.createElement('div');meta.className='frame-meta';
  const direction=document.createElement('span');direction.className='direction '+(frame.kind===2?'tx':frame.kind===3?'fail':'');direction.textContent=frame.kind===1?'↙ RX':frame.kind===2?'↗ TX':'! FAIL';
  const id=document.createElement('span');id.className='can-id';id.textContent='0x'+frame.id.toString(16).toUpperCase().padStart(frame.ext?8:3,'0');
  const info=document.createElement('span');info.className='frame-info';info.textContent=(frame.ext?'EXT':'STD')+' · '+(frame.rtr?'RTR · ':'')+(frame.dlc||0)+'B';meta.append(direction,id,info);
  const time=document.createElement('span');time.className='time';time.textContent=frame.time||(frame.receivedAt?new Date(frame.receivedAt).toLocaleTimeString('zh-CN',{hour12:false}):'');top.append(meta,time);row.append(top);
  if(frameFilter==='fail'){
   const raw=document.createElement('details');raw.className='failure-data';const summary=document.createElement('summary');summary.textContent='查看原始数据';raw.append(summary,frameBytes(frame));row.append(raw);
  }else row.append(frameBytes(frame));
  root.append(row);
 }
}
function togglePause(){
 paused=!paused;frozenFrameHistory=paused?displayedFrameHistory.slice():null;
 $('pauseButton').classList.toggle('active',paused);$('pauseButton').setAttribute('aria-label',paused?'恢复显示':'暂停显示');$('pauseButton').setAttribute('aria-pressed',String(paused));renderFrames();
}
function clearFrames(){frames=[];displayedFrameHistory=[];if(paused)frozenFrameHistory=[];dirty=false;renderFrames();toast('帧记录已清空，设备统计继续')}
let rxSaveLimit=300,txSaveLimit=300,rateSample=null,rxRate=null,txRate=null;
let deviceLoadSample=null,monitorMode=0;
const deviceLoadNote='ESP32 统计全部 RX 与成功 TX，包含持续查询；不含位填充、错误帧、失败发送尝试及硬件漏帧。BLE 监控丢帧不会改变设备统计。';
function updateHistoryNote(){$('lossNote').textContent='内存记录上限 RX '+rxSaveLimit+' / TX '+txSaveLimit+' · 单次显示最新 80 条匹配记录 · 设备监控丢弃 '+firmwareDrops+' · App 丢弃 '+appDrops+' · 暂停仅冻结列表，筛选作用于暂停快照；设备统计继续更新。'}
function boundedFrameHistory(history){let rx=0,tx=0;return history.filter(f=>f.kind===1?++rx<=rxSaveLimit:++tx<=txSaveLimit)}
function trimHistory(){frameHistory.resize(rxSaveLimit,txSaveLimit)}
function applyHistoryLimits(){const read=id=>{const text=$(id).value.trim(),n=Number(text);if(!text||!Number.isInteger(n)||n<0||n>10000)throw Error('上限必须是 0–10000 的整数');return n};try{const rx=read('rxLimit'),tx=read('txLimit');rxSaveLimit=rx;txSaveLimit=tx;try{ConfigIO.writeField('historyLimits',{rx,tx})}catch(e){}trimHistory();if(frozenFrameHistory!==null)frozenFrameHistory=boundedFrameHistory(frozenFrameHistory);displayedFrameHistory=boundedFrameHistory(displayedFrameHistory);renderFrames();updateHistoryNote();$('historyResult').textContent='已应用：RX '+rx+'，TX '+tx}catch(e){$('historyResult').textContent=e.message}}
(function(){try{const v=ConfigIO.readField('historyLimits');if(v&&Number.isInteger(v.rx)&&Number.isInteger(v.tx)&&v.rx>=0&&v.rx<=10000&&v.tx>=0&&v.tx<=10000){rxSaveLimit=v.rx;txSaveLimit=v.tx}}catch(e){}trimHistory();$('rxLimit').value=rxSaveLimit;$('txLimit').value=txSaveLimit;updateHistoryNote()})();
function renderFailureCount(){const supported=!!window.cleanTxCounters;$('errorCount').textContent=cleanFailureCount===null?'—':supported?cleanFailureCount.toLocaleString():'需更新';$('errorCount').classList.toggle('red',supported&&cleanFailureCount>0);$('errorCount').title=supported?'累计通信失败次数':'更新 ESP32 固件后显示准确的通信失败次数'}
function resetMonitorMetrics(){cleanFailureCount=null;window.cleanTxCounters=false;renderFailureCount();monitorMode=0;clearDeviceRates();clearDeviceLoad();$('rxCount').textContent=$('txCount').textContent='—';$('busLoad').textContent='—';$('loadBar').style.width='0%'}
function clearDeviceRates(){rateSample=null;rxRate=txRate=null;renderDeviceRates()}
function receiveDeviceRates(a,age=0){
 const windowUs=u32(a,12),bitrate=u32(a,16);
 if(!connected||monitorMode!==0||a[2]!==0||a[3]!==0||windowUs<1000000||![125000,250000,500000,1000000].includes(bitrate))return;
 const rx=u32(a,4)*1000000/windowUs,tx=u32(a,8)*1000000/windowUs;
 if(rx>50000||tx>50000)return;
 // Arrival time is only a freshness check. The rate denominator is the
 // actual ESP32 sampling window, even when BLE delivers windows in a burst.
 rateSample={time:performance.now()-Math.max(0,age),windowUs,bitrate};rxRate=rx;txRate=tx;renderDeviceRates();
}
function renderDeviceRates(){
 const fresh=connected&&monitorMode===0&&rateSample&&performance.now()-rateSample.time<=4000;
 $('rxCount').textContent=fresh?formatRate(rxRate):'—';$('txCount').textContent=fresh?formatRate(txRate):'—';
 $('rateHint').textContent=fresh?'ESP32 实际统计窗口 '+(rateSample.windowUs/1000000).toFixed(2)+' 秒 · 全部 CAN ID；列表筛选不影响速率。':!connected?'连接后读取 ESP32 实际收发速率。':monitorMode!==0?'进入通道监控后读取收发速率。':rateSample?'设备速率统计超时，等待新数据。':'等待设备时间统计；需配套 1.16.3 或更新固件，不再按蓝牙到达间隔估算。';
}
function formatRate(n){return n.toLocaleString('zh-CN',{maximumFractionDigits:1})}
function clearDeviceLoad(){deviceLoadSample=null;renderDeviceLoad()}
function receiveDeviceLoad(a,age=0){
 const hundredths=a[2]|a[3]<<8,windowUs=u32(a,12),bitrate=u32(a,16);
 if(!connected||monitorMode!==0||hundredths>10000||!windowUs||![125000,250000,500000,1000000].includes(bitrate))return;
 // The percentage is computed from the ESP32's complete CAN counters, independently
 // of raw notification delivery, UI batching and type-4 arrival timing.
 deviceLoadSample={percent:hundredths/100,rxBits:u32(a,4),txBits:u32(a,8),windowUs,bitrate,time:performance.now()-Math.max(0,age)};renderDeviceLoad();
}
function renderDeviceLoad(){
 const fresh=connected&&monitorMode===0&&deviceLoadSample&&performance.now()-deviceLoadSample.time<=4000;
 $('busLoad').textContent=fresh?'≈ '+deviceLoadSample.percent.toFixed(2)+'%':'—';
 $('loadBar').style.width=fresh?deviceLoadSample.percent+'%':'0%';
 $('loadHint').textContent=(fresh?'':!connected?'连接后读取设备负载。':monitorMode!==0?'进入通道监控后读取设备负载。':deviceLoadSample?'设备负载统计超时，等待新数据。':'等待 ESP32 负载统计；旧固件需更新后显示。')+deviceLoadNote;
}
function updateMonitor(){const now=performance.now(),fresh=connected&&rateSample&&now-rateSample.time<=4000;rxHistory.push(fresh&&rxRate!==null?rxRate:0);rxHistory.shift();txHistory.push(fresh&&txRate!==null?txRate:0);txHistory.shift();$('rate').textContent=fresh&&rxRate!==null?formatRate(rxRate+txRate)+' 帧/秒':'等待速率数据';bucketRx=bucketTx=0;const max=Math.max(4,...rxHistory,...txHistory),path=arr=>arr.map((v,i)=>(i?'L':'M')+(i*600/29).toFixed(1)+' '+(62-v/max*55).toFixed(1)).join(' ');$('rxGraph').setAttribute('d',path(rxHistory));$('txGraph').setAttribute('d',path(txHistory));renderDeviceLoad();renderDeviceRates();if(!fresh){$('rxCount').textContent=$('txCount').textContent='—';if(connected&&Date.now()-lastStatus>4000){$('busStatus').textContent='设备状态更新超时，请检查连接'}}}
setInterval(()=>{if(dirty&&!paused){dirty=false;renderFrames()}},150);
setInterval(updateMonitor,1000);
