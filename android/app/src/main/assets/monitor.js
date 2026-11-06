'use strict';
const $=id=>document.getElementById(id);
function consumeChannelBack(){
 if(typeof closeDetailChartFullscreen==='function'&&closeDetailChartFullscreen())return true;
 const devices=$('deviceModal');if(devices&&!devices.hidden&&devices.getClientRects().length){closeDevices();return true}
 const editor=$('watchEditor');if(editor&&!editor.hidden&&editor.getClientRects().length&&typeof closeWatchEditor==='function'){closeWatchEditor();return true}
 return false;
}
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&consumeChannelBack()){event.preventDefault();event.stopImmediatePropagation()}});
let connected=false,connectionPending=false,paused=false,dirty=false,toastTimer,deviceMap=new Map(),lastStatus=0,firmwareDrops=0,appDrops=0;
const frameHistory=new FrameHistory();
Object.defineProperty(window,'frames',{get:()=>frameHistory.snapshot(),set:records=>frameHistory.replace(records)});
let frameFilter='all',displayedFrameHistory=[],frozenFrameHistory=null;
let cleanFailureCount=null;window.cleanTxCounters=false;
let bucketRx=0,bucketTx=0,rxHistory=Array(30).fill(0),txHistory=Array(30).fill(0),gotStatus=false;
const native=()=>window.CanAndroid;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500)}
function invoke(name,...args){if(native()&&typeof native()[name]==='function')native()[name](...args);else if(!native())toast('请在安卓 App 中连接设备；此处为界面预览。')}
function connectAction(){if(connected||connectionPending){invoke('disconnect');return} $('deviceModal').hidden=false;scan()}
function scan(){deviceMap.clear();$('deviceList').replaceChildren();$('scanHint').textContent=typeof transportMode!=='undefined'&&transportMode==='usb'?'正在扫描 USB 串口…':typeof transportMode!=='undefined'&&transportMode==='wifi'?'正在寻找局域网中的已配对设备…':'正在扫描附近的 ESP32…';invoke('scan')}
function closeDevices(){$('deviceModal').hidden=true}
function renderChannelTab(name){document.querySelectorAll('.tabs .tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!=='view-'+name);if(native()&&typeof native().setMonitorVisible==='function')native().setMonitorVisible(name==='live'||name==='uart')}
function showTab(name){if(window.uartMode&&(name==='motor'||name==='live'))name='uart';if(!['motor','live','settings','uart'].includes(name))return;if(window.closeMotorDetail)closeMotorDetail(true,name);renderChannelTab(name)}
function setConnection(d){
 connected=d.value==='ready';connectionPending=d.value==='connecting';$('connectionMessage').textContent=d.message||'';$('badgeText').textContent=connected?'已连接':connectionPending?'连接中':d.value==='scanning'?'扫描中':'未连接';$('connectionBadge').classList.toggle('online',connected);$('connectButton').textContent=connected?'断开连接':connectionPending?'取消连接':'连接设备';$('connectButton').disabled=false;updateFrameViewState();$('deviceName').textContent=connected?d.name:'连接 CAN 设备';
 resetMonitorMetrics();gotStatus=false;lastStatus=Date.now();rxHistory.fill(0);txHistory.fill(0);bucketRx=bucketTx=0;
 if(window.deviceConnection)deviceConnection();if(window.resetWatchedFeedback)resetWatchedFeedback();
 if(connected)closeDevices();else $('busStatus').textContent='未连接';if(d.value==='idle')$('scanHint').textContent=d.message||'';
}
function u32(a,o){return (a[o]|a[o+1]<<8|a[o+2]<<16|a[o+3]<<24)>>>0}
function put32(a,o,n){for(let i=0;i<4;i++)a[o+i]=(n>>>(8*i))&255}
function hexOf(a){return Array.from(a,x=>x.toString(16).padStart(2,'0')).join('')}
const packetNibbles=new Int8Array(128).fill(-1);
for(let i=0;i<16;i++){packetNibbles['0123456789abcdef'.charCodeAt(i)]=i;packetNibbles['0123456789ABCDEF'.charCodeAt(i)]=i;}
function parsePacket(hex){
 if(typeof hex!=='string'||hex.length!==40)return null;
 const a=new Uint8Array(20);
 for(let i=0;i<20;i++){const hi=packetNibbles[hex.charCodeAt(i*2)],lo=packetNibbles[hex.charCodeAt(i*2+1)];if(hi===undefined||lo===undefined||hi<0||lo<0)return null;a[i]=hi*16+lo;}
 return a[0]===1?a:null;
}
function nativePacket(item,deliveryAge){if(item&&item.uart!==undefined&&window.uartBatch)uartBatch(item);else if(typeof item==='string')processPacket(item);else processPacket(item.hex,(item.age||0)+deliveryAge)}
function processPacket(hex,age=0){
 const a=parsePacket(hex);if(!a)return;const kind=a[1];if(window.uartPacket&&uartPacket(a))return;if(window.devicePacket)devicePacket(a);if(window.watchedPacket)watchedPacket(a,age);
 if(kind===4){gotStatus=true;lastStatus=Date.now()-Math.max(0,age);cleanFailureCount=u32(a,12);renderFailureCount();firmwareDrops=u32(a,16);$('busStatus').textContent=['正常 · Error active','错误警告 · Error warning','错误被动 · Error passive','总线离线 · 正在恢复'][a[2]]||'未知状态';$('dropStatus').textContent='设备 '+firmwareDrops+' · App '+appDrops;updateHistoryNote();return}
 if(kind===13){receiveDeviceLoad(a,age);return}
 if(kind===14){receiveDeviceRates(a,age);return}
 if(kind===3&&(a[2]&0x80)&&!(a[2]&0x7c))return; // pure arbitration is diagnostic only
 if(![1,2,3].includes(kind)||a[3]>8)return;if(kind===1)bucketRx++;if(kind===2)bucketTx++;
 frameHistory.push({kind,id:u32(a,4),flags:a[2],ext:!!(a[2]&1),rtr:!!(a[2]&2),dlc:a[3],data:Array.from(a.slice(8,8+((a[2]&2)?0:a[3]))),receivedAt:Date.now()-Math.max(0,age),seq:u32(a,16)});dirty=true;
}
window.onNative=function(type,d){
 const deliveryAge=Number.isFinite(d.sentAt)?Math.max(0,Date.now()-d.sentAt):0;if(Number.isFinite(d.age))d={...d,age:d.age+deliveryAge};
 if(window.networkEvent)networkEvent(type,d);if(window.uartEvent)uartEvent(type,d);
 if(type==='busLoad'||type==='busRates'){processPacket(d.hex,d.age||0)}else if(type==='busLoadReset'){clearDeviceLoad();clearDeviceRates()}else if(type==='motorRates'){if(window.motorDetailRates)motorDetailRates(d)}else if(type==='motorSample'){if(window.motorDetailSample)motorDetailSample(d)}else if(type==='detailSupport'){if(window.motorDetailSupport)motorDetailSupport(d)}else if(type==='telemetryMode'){monitorMode=Number(d.mode);clearDeviceLoad();clearDeviceRates();if(window.motorDetailMode)motorDetailMode(d);if(window.motorTelemetryMode)motorTelemetryMode(d)}else if(type==='onlineSummary'){if(window.motorOnlineSummary)motorOnlineSummary(d)}else if(type==='motorTemperatures'){if(window.motorTemperatures)motorTemperatures(d)}else if(type==='queryState'){if(window.motorQueryState)motorQueryState(d)}else if(type==='state')setConnection(d);else if(type==='deviceInfo')$('deviceName').textContent=d.name;
 else if(type==='error'){toast(d.message);if(window.deviceWriteFailed)deviceWriteFailed(d.message)}
 else if(type==='scanStart'){deviceMap.clear();$('deviceList').replaceChildren()}
 else if(type==='device'){deviceMap.set(d.address,d);renderDevices()}
 else if(type==='packets'){appDrops=d.dropped||0;d.items.forEach(item=>nativePacket(item,deliveryAge))}
 else if(type==='motorSamples'){appDrops=d.dropped||0;for(const item of d.packets||[])nativePacket(item,deliveryAge);if(window.motorDetailSamples)motorDetailSamples(d,deliveryAge);else for(const item of d.items||[])if(window.motorDetailSample)motorDetailSample({key:d.key,hex:item.hex,age:(item.age||0)+deliveryAge});}
 else if(type==='writeDone'&&!d.success&&window.deviceWriteFailed)deviceWriteFailed('设备未接受设置，请重试');
};
function renderDevices(){const root=$('deviceList');root.replaceChildren();for(const d of deviceMap.values()){const button=document.createElement('button');button.className='device';const text=document.createElement('div'),name=document.createElement('strong'),addr=document.createElement('small'),signal=document.createElement('span');name.textContent=d.name;addr.textContent=d.address;text.append(name,addr);signal.className='chip';signal.textContent=d.transport==='usb'?'USB':d.transport==='wifi'?'Wi-Fi':d.rssi+' dBm';button.append(text,signal);button.onclick=()=>{$('scanHint').textContent='正在连接 '+d.name+'…';invoke('connect',d.address)};root.append(button)}}
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
// Reuse row and byte nodes; replacing hundreds of text/layout nodes every
// refresh causes excessive allocator churn in long-running Qt WebEngine views.
function setFrameText(node,text){if(node.textContent===text)return;if(node.childNodes.length===1&&node.firstChild.nodeType===3)node.firstChild.data=text;else node.textContent=text}
function createFrameRow(){
 const row=document.createElement('article');
 row.innerHTML='<h4 class="failure-reason" hidden></h4><div class="frame-top"><div class="frame-meta"><span class="direction"></span><span class="can-id"></span><span class="frame-info"></span></div><span class="time"></span></div><div class="bytes"></div><details class="failure-data" hidden><summary>查看原始数据</summary></details>';
 row.parts={reason:row.querySelector('h4'),direction:row.querySelector('.direction'),id:row.querySelector('.can-id'),info:row.querySelector('.frame-info'),time:row.querySelector('.time'),bytes:row.querySelector('.bytes'),raw:row.querySelector('details')};return row;
}
function updateFrameRow(row,frame,raw=false){
 const p=row.parts;row.className='frame'+(frame.kind===3?' failure-frame':'');
 p.reason.hidden=frame.kind!==3;if(frame.kind===3)setFrameText(p.reason,failureReasons(frame).join(' · '));
 p.direction.className='direction '+(frame.kind===2?'tx':frame.kind===3?'fail':'');setFrameText(p.direction,frame.kind===1?'↙ RX':frame.kind===2?'↗ TX':'! FAIL');
 setFrameText(p.id,'0x'+frame.id.toString(16).toUpperCase().padStart(frame.ext?8:3,'0'));
 setFrameText(p.info,(frame.ext?'EXT':'STD')+' · '+(frame.rtr?'RTR · ':'')+(frame.dlc||0)+'B');
 setFrameText(p.time,frame.time||(frame.receivedAt?frameTimeFormatter.format(frame.receivedAt):''));
 p.raw.hidden=!raw;
 if(raw&&p.bytes.parentNode!==p.raw)p.raw.append(p.bytes);
 if(!raw&&p.bytes.parentNode!==row)row.insertBefore(p.bytes,p.raw);
 if(row.frame!==frame)p.raw.open=false;row.frame=frame;
 const data=frame.data||[],size=Math.max(1,data.length);
 while(p.bytes.children.length>size)p.bytes.lastChild.remove();
 for(let i=0;i<size;i++){
  let byte=p.bytes.children[i];if(!byte){byte=document.createElement('span');p.bytes.append(byte)}
  byte.className=data.length?'byte':'mini';setFrameText(byte,data.length?data[i].toString(16).toUpperCase().padStart(2,'0'):frame.rtr?'远程请求 · 无数据字段':'零长度数据帧');
 }
}
let frameTimeSecond=null,frameTimeText='';
const frameTimeFormatter={format(ms){const second=Math.floor(ms/1000);if(second!==frameTimeSecond){const d=new Date(ms);frameTimeSecond=second;frameTimeText=[d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':')}return frameTimeText}};
const frameRows=[];
function renderFrames(){
 updateFrameViewState();
 // FrameHistory creates a new snapshot when its rings advance. Keep the last
 // displayed snapshot for Pause without copying the full history each paint.
 if(!paused){displayedFrameHistory=frames;dirty=false;}
 else if(frozenFrameHistory!==null)frozenFrameHistory=boundedFrameHistory(frozenFrameHistory);
 const history=paused?(frozenFrameHistory||[]):displayedFrameHistory;
 const query=$('filter').value.trim().replace(/^0x/i,'').toUpperCase(),kind={rx:1,tx:2,fail:3}[frameFilter];
 let count=history.length,visible;
 if(!kind&&!query)visible=history.slice(0,80);
 else{count=0;visible=[];for(const frame of history)if((!kind||frame.kind===kind)&&(!query||frame.id.toString(16).toUpperCase().includes(query))){count++;if(visible.length<80)visible.push(frame)}}
 $('frameCount').textContent=count+' 条';
 const root=$('frames');
 if(!count){
  const empty=document.createElement('div');empty.className='empty';
  empty.textContent=query?'没有匹配此 CAN ID 的'+(frameFilter==='fail'?'失败事件。':'报文。'):frameFilter==='fail'?'暂无保留的失败事件。设备累计次数可能与此列表不同。':paused?'暂停快照中暂无'+(frameFilter==='rx'?'RX':frameFilter==='tx'?'成功 TX':'CAN')+'报文。':!connected?'连接 ESP32 后，CAN 帧会显示在这里。':frameFilter==='rx'?'等待接收 RX 报文…':frameFilter==='tx'?'等待成功发送的 TX 报文…':'等待 CAN 数据；请检查对端是否正在发送。';
  root.replaceChildren(empty);return;
 }
 if(root.firstElementChild&&!root.firstElementChild.classList.contains('frame'))root.replaceChildren();
 while(root.children.length>visible.length)root.lastChild.remove();
 visible.forEach((frame,i)=>{const row=frameRows[i]||(frameRows[i]=createFrameRow());updateFrameRow(row,frame,frameFilter==='fail');if(root.children[i]!==row)root.append(row)});
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
setInterval(()=>{if(dirty&&!paused&&!$('view-live').hidden&&!document.hidden){dirty=false;renderFrames()}},150);
setInterval(updateMonitor,1000);
