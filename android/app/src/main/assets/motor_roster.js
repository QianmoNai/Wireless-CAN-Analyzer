'use strict';
// Each device channel has its own origin, registry and live feedback cache.
const watchedMotors=[];
let homeQuiet=false,summaryAt=0;
let watchedDirty=false,editingWatch=null,activeWatch=null,removedWatch=null,watchFeedbackAuto=true;
function watchMessage(text){$('rosterResult').textContent=text}
function watchHex(id){return '0x'+id.toString(16).toUpperCase().padStart(3,'0')}
function validateWatch(raw){
 const p=Motors.profiles[raw.profile];if(!p)throw Error('请选择有效型号');
 const c={profile:raw.profile,name:String(raw.name||'').trim().slice(0,32),id:Motors.number(raw.id,'电机 ID',1,p.family==='dji'?p.maxId:0x7fe,true)};
 if(p.family==='dm'){
  c.pollHz=Motors.number(raw.pollHz===undefined?100:raw.pollHz,'查询频率',1,1000,true);
  c.master=Motors.number(raw.master,'Master ID',0,0x7ff,true);
  if(!Motors.modes[raw.mode])throw Error('请选择有效模式');c.mode=raw.mode;Motors.dmId(c.id,c.mode);
  c.limits={};for(const k of ['p','v','t'])c.limits[k]=Motors.number(raw.limits&&raw.limits[k],k.toUpperCase()+'_MAX',0.000001,1e6);
 }
 c.feedbackId=Motors.number(raw.feedbackId===undefined?(p.family==='dji'?p.feedbackBase+c.id:c.master):raw.feedbackId,'返回帧 ID',0,0x7ff,true);
 if(p.family==='dm')c.master=c.feedbackId;
 return c;
}
function watchConflict(a,b){
 if(a.feedbackId!==b.feedbackId)return false;
 const af=Motors.profiles[a.profile].family,bf=Motors.profiles[b.profile].family;
 return af!=='dm'||bf!=='dm'||(a.id&15)===(b.id&15);
}
function saveWatchedMotors(){try{ConfigIO.writeField('watchedMotors',watchedMotors.map(m=>m.config))}catch(e){watchMessage('配置保存失败；本次运行仍可监控')}}
function watchBusy(){return !!window.deviceSettingsBusy}
function readWatchHex(text,label){if(!/^(?:0x)?[0-9a-fA-F]{1,3}$/i.test(text.trim()))throw Error(label+'应为 000–7FF 的十六进制数');return Motors.number(parseInt(text.trim().replace(/^0x/i,''),16),label,0,0x7ff,true)}
function commitWatch(raw,existing=null){
 if(watchBusy())throw Error('请等待设备设置保存完成后编辑');
 if(!existing&&watchedMotors.length>=32)throw Error('每个通道最多添加 32 个电机');
 const c=validateWatch(raw);
 const conflict=watchedMotors.find(m=>m!==existing&&watchConflict(c,m.config));
 if(conflict)throw Error('与「'+(conflict.config.name||Motors.profiles[conflict.config.profile].name)+'」的反馈地址重复或无法区分；请检查返回帧 ID 和电机 ID');
 if(watchedMotors.some(m=>m!==existing&&Motors.profiles[m.config.profile].family===Motors.profiles[c.profile].family&&m.config.id===c.id&&(Motors.profiles[c.profile].feedbackBase===Motors.profiles[m.config.profile].feedbackBase)))throw Error('此型号的电机 ID 已在列表中，请编辑已有电机');
 if(existing)stopMotorPolling(existing,"配置已更新，查询已停止");
 const m=existing||{};Object.assign(m,{config:c,feedback:null,time:0,homeTemperature:null,temperatureAt:0,mode:null,polling:false,queryPending:false,queryTarget:false,queryMessage:''});
 if(!existing)watchedMotors.push(m);
 saveWatchedMotors();renderWatchedMotors();return m;
}
function openWatchEditor(m=null){
 if(m&&(m.polling||m.queryPending))stopMotorPolling(m,"正在编辑配置，查询已停止");
 if(watchBusy()){watchMessage('请等待设备设置保存完成后编辑');return}
 editingWatch=m;$('watchEditorTitle').textContent=m?'编辑监控设备':'添加监控设备';$('watchSave').textContent=m?'保存修改':'添加到列表';
 $('watchEditorError').textContent='';$('watchName').value=m?m.config.name:'';
 $('watchBrand').value=m&&Motors.profiles[m.config.profile].family!=='dji'?'dm':'dji';populateWatchProfiles(m?m.config.profile:'c620');watchEditorProfileChanged();
 if(m){const c=m.config;$('watchId').value=Motors.profiles[c.profile].family==='dji'?c.id:c.id.toString(16);$('watchFeedback').value=c.feedbackId.toString(16).padStart(3,'0');watchFeedbackAuto=Motors.profiles[c.profile].family==='dji'&&c.feedbackId===Motors.profiles[c.profile].feedbackBase+c.id;if(c.mode){for(const k of ['p','v','t'])$('watch'+k.toUpperCase()).value=c.limits[k]}}
 else { // Suggest a free number for the default profile; never auto-add or auto-send.
  const p=Motors.profiles[$('watchProfile').value];for(let id=1;id<=p.maxId;id++)if(!watchedMotors.some(m=>m.config.feedbackId===p.feedbackBase+id)){ $('watchId').value=id;break }watchEditorIdChanged();
 }
 previewWatchAddress();$('watchEditor').hidden=false;$('watchEditor').scrollIntoView({behavior:'smooth',block:'start'});$('watchBrand').focus({preventScroll:true});
}
function closeWatchEditor(){$('watchEditor').hidden=true;editingWatch=null;$('rosterAdd').focus({preventScroll:true})}
const watchProfileLabels={c620:'M3508 · C620 电调',c610:'M2006 · C610 电调',gm6020:'GM6020',gm3510:'GM3510 · 旧款云台',dm_imu_l1:'DM-IMU-L1 · 六轴 IMU',dm4310:'DM4310 · 预设量程',dm_custom:'其他型号 · 自定义量程'};
function populateWatchProfiles(selected){
 const select=$('watchProfile');select.replaceChildren();
 const groups=new Map();
 for(const [id,p] of Object.entries(Motors.profiles))if(($('watchBrand').value==='dji'?p.family==='dji':p.family!=='dji')){
  const label=p.family==='imu'?'IMU':p.family==='dji'?(p.feedbackBase===0x204?'云台电机':'减速电机 + 电调'):p.limits?'电机 · 量程预设':'电机 · 自定义';
  if(!groups.has(label)){const group=document.createElement('optgroup');group.label=label;groups.set(label,group);select.append(group)}
  groups.get(label).append(new Option(watchProfileLabels[id]||p.name.replace(/^达妙 /,''),id));
 }

 selected=selected||($('watchBrand').value==='dji'?'c620':'dm4310');
 if(selected&&Array.from(select.options).some(o=>o.value===selected))select.value=selected;
}
function watchBrandChanged(){populateWatchProfiles();watchEditorProfileChanged()}
function watchEditorProfileChanged(){
 const p=Motors.profiles[$('watchProfile').value],dm=p.family==='dm',imu=p.family==='imu';
 $('watchProfileHint').textContent=imu?'接收 DM-IMU-L1 的主动 CAN 上报，显示姿态、加速度、角速度和四元数。请先用达妙上位机设置 CAN 主动模式，并填写模块实际 CAN_ID / MST_ID。':dm?(p.limits?'经典 CAN 反馈。已填入 '+p.name+' 的 SDK 预设量程，请核对实际 P_MAX / V_MAX / T_MAX；不同固件或参数设置可能不同。持续查询在详情页开启。':'适用于达妙经典 CAN 反馈协议。请按电机实际配置填写下方三项量程，避免参数解码不正确。'):p.rawAngle?'GM3510 旧款协议：仅显示机械角度与输出转矩 raw；不提供速度、温度。反馈 ID 为 0x204 + 编号，编号范围 1–3。':'按电机本体型号选择，电调名称用于区分配套设备。接收匹配的反馈帧即可更新状态，无需开启达妙持续查询。';
 $('watchDmFields').hidden=!dm;$('watchIdLabel').textContent=imu?'IMU CAN_ID · HEX':dm?'电机 ID · HEX':'电机编号 · 十进制';
 $('watchId').value='1';$('watchId').inputMode=p.family==='dji'?'numeric':'text';$('watchFeedback').value=dm||imu?'000':(p.feedbackBase+1).toString(16);watchFeedbackAuto=true;
 for(const k of ['p','v','t'])$('watch'+k.toUpperCase()).value=p.limits?p.limits[k]:'';
 $('watchLimits').open=dm&&!p.limits;
 $('watchAddressHint').textContent=imu?'返回帧 ID 填 MST_ID；同一总线每台 IMU 使用独立的反馈 ID，不能与电机反馈重叠。这里保存监听配置，不修改 IMU 参数。':dm?'返回帧 ID 即 Master ID；同一 Master ID 下电机 ID 的低 4 位必须不同。':'返回帧 ID 默认按型号和电机编号计算，也可手动指定反馈匹配地址。';
 previewWatchAddress();
}
function watchEditorIdChanged(){const p=Motors.profiles[$('watchProfile').value];if(p.family==='dji'&&watchFeedbackAuto&&/^\d+$/.test($('watchId').value))$('watchFeedback').value=(p.feedbackBase+Number($('watchId').value)).toString(16);previewWatchAddress()}
function watchEditorRaw(){
 const family=Motors.profiles[$('watchProfile').value].family,feedback=readWatchHex($('watchFeedback').value,'返回帧 ID');
 return {profile:$('watchProfile').value,name:$('watchName').value,id:family==='dji'?Motors.number($('watchId').value,'电机编号',1,8,true):readWatchHex($('watchId').value,'CAN ID'),feedbackId:feedback,master:feedback,pollHz:editingWatch?editingWatch.config.pollHz:100,mode:editingWatch&&editingWatch.config.mode||'mit',limits:{p:$('watchP').value,v:$('watchV').value,t:$('watchT').value}};
}
function previewWatchAddress(){try{const raw=watchEditorRaw(),p=Motors.profiles[raw.profile];Motors.number(raw.id,'电机 ID',1,p.family==='dji'?p.maxId:0x7fe,true);$('watchAddressPreview').textContent='监听反馈 RX '+watchHex(raw.feedbackId)}catch(e){$('watchAddressPreview').textContent=e.message}}
function saveWatchEditor(){try{
 const existing=editingWatch;
 const m=commitWatch(watchEditorRaw(),existing);closeWatchEditor();
 $('watchSelect').value=String(watchedMotors.indexOf(m));refreshWatchedMotors();publishDashboard();
 watchMessage(existing?'电机配置已更新':'已添加电机，返回首页查看状态和详情');
 }catch(e){$('watchEditorError').textContent=e.message}}
function removeWatchedMotor(m){
 stopMotorPolling(m,"已移除电机，查询停止");
 if(watchBusy()){watchMessage('请等待设备设置保存完成后移除');return}
 if(editingWatch===m)closeWatchEditor();if(activeWatch===m){activeWatch=null}
 removedWatch={config:m.config,index:watchedMotors.indexOf(m)};watchedMotors.splice(removedWatch.index,1);saveWatchedMotors();renderWatchedMotors();$('rosterUndo').hidden=false;watchMessage('已移除监控，不改变电机运行状态。可撤销最近一次移除。');
}
function undoWatchRemove(){if(!removedWatch)return;try{const m=commitWatch(removedWatch.config);watchedMotors.splice(watchedMotors.indexOf(m),1);watchedMotors.splice(removedWatch.index,0,m);removedWatch=null;$('rosterUndo').hidden=true;saveWatchedMotors();renderWatchedMotors();watchMessage('已恢复监控，等待新反馈')}catch(e){watchMessage(e.message)}}
function selectedWatch(){return watchedMotors[Number($('watchSelect').value)]||null}
function editSelectedWatch(){const m=selectedWatch();if(m)openWatchEditor(m)}
function removeSelectedWatch(){const m=selectedWatch();if(m)removeWatchedMotor(m)}
function renderWatchedMotors(){
 const select=$('watchSelect'),previous=select.value;select.replaceChildren();$('rosterCount').textContent=watchedMotors.length+' / 32';
 watchedMotors.forEach((m,i)=>{const c=m.config,p=Motors.profiles[c.profile];select.add(new Option((c.name||p.name)+' · ID '+watchHex(c.id)+' · RX '+watchHex(c.feedbackId),String(i)))});
 if(!watchedMotors.length)select.add(new Option('尚未添加电机',''));
 else if(previous!==''&&Number(previous)<watchedMotors.length)select.value=previous;
 refreshWatchedMotors();
}
function refreshWatchedMotors(){
 const disabled=watchBusy()||!watchedMotors.length;
 $('watchSelect').disabled=disabled;$('watchEditSelected').disabled=disabled;$('watchRemoveSelected').disabled=disabled;
 watchedDirty=false;
 if(window.requestDetailPaint)requestDetailPaint();
}
function watchedPacket(a,age=0){
 if(document.body.classList.contains('motor-detail')||!connected||a[1]!==1||(a[2]&3)||a[3]>8)return;
 const id=u32(a,4),data=Array.from(a.slice(8,8+a[3]));
 for(const m of watchedMotors){try{const c=m.config,p=Motors.profiles[c.profile];
  const f=p.family==='imu'?Motors.imuFeedback(c.feedbackId,id,data):p.family==='dji'?Motors.djiFeedback(c.profile,c.id,id,data,c.feedbackId):Motors.dmFeedback(c.id,c.master,id,data,c.limits);
  if(!f)continue;if(f.mode!==undefined){m.mode=f.mode;watchedDirty=true;continue}
  m.feedback=f;m.time=Date.now()-Math.max(0,age);watchedDirty=true;
 }catch(e){/* An invalid packet must not interrupt other motors or raw CAN monitoring. */}}
}
function resetWatchedFeedback(){homeQuiet=false;summaryAt=0;for(const m of watchedMotors){m.feedback=null;m.time=0;m.homeTemperature=null;m.temperatureAt=0;m.mode=null;m.polling=false;m.queryPending=false;m.queryTarget=false;m.queryMessage="查询已关闭"}refreshWatchedMotors()}
populateWatchProfiles('c620');
(function(){try{const saved=ConfigIO.readField('watchedMotors');if(Array.isArray(saved))for(const raw of saved.slice(0,32)){try{const c=validateWatch(raw);if(!watchedMotors.some(m=>watchConflict(c,m.config)))watchedMotors.push({config:c,feedback:null,time:0,mode:null})}catch(e){}}}catch(e){}renderWatchedMotors()})();
// Batch DOM work independently of bus packet rate; stale status updates even without packets.
setInterval(()=>{if(watchedDirty||!$('view-motor').hidden)refreshWatchedMotors()},250);

// Native Activity combines these independent channel snapshots into the home page.
function dashboardSnapshot(){const now=Date.now();return watchedMotors.map(m=>{
 const p=Motors.profiles[m.config.profile],temperatureSupported=p.family!=='imu'&&m.config.profile!=='c610'&&!p.rawAngle;
 const temperature=homeQuiet?m.homeTemperature:m.feedback&&m.feedback.values&&m.feedback.values.temperature;
 const temperatureAt=homeQuiet?m.temperatureAt:m.time;
 const sampleHz=window.detailRateFor?detailRateFor(m):100;
 if(!m.dashboardMetadata||m.dashboardMetadata.sampleHz!==sampleHz||m.dashboardMetadata.config!==m.config)m.dashboardMetadata={config:m.config,key:JSON.stringify(m.config),name:m.config.name||p.name,model:p.name,id:watchHex(m.config.id),feedbackId:watchHex(m.config.feedbackId),family:p.family,sampleHz,temperatureSupported};
 const {config,...metadata}=m.dashboardMetadata;
 return {...metadata,connectionStatus:homeQuiet?(summaryAt?m.summaryState||'waiting':'waiting'):(m.time?(now-m.time<2000?'online':'offline'):'waiting'),statusAge:homeQuiet?(summaryAt?now-summaryAt:3000):0,temperature:temperatureSupported&&Number.isFinite(temperature)?temperature:null,temperatureAge:temperatureAt?now-temperatureAt:3000};
 })}
window.motorTelemetryMode=function(data){homeQuiet=!!data.quiet;summaryAt=0;for(const m of watchedMotors){m.summaryState='waiting';m.homeTemperature=null;m.temperatureAt=0;if(!homeQuiet){m.feedback=null;m.time=0;m.mode=null}}refreshWatchedMotors();publishDashboard()};
window.motorOnlineSummary=function(data){if(!connected||!homeQuiet||data.count!==watchedMotors.length)return;summaryAt=Date.now()-Math.max(0,Number(data.age)||0);watchedMotors.forEach((m,i)=>{m.summaryState=(data.online>>>i)&1?'online':(data.seen>>>i)&1?'offline':'waiting'});scheduleDashboard()};
window.motorTemperatures=function(data){
 if(!connected||!homeQuiet||data.count!==watchedMotors.length||!Number.isInteger(data.start)||data.start<0||!Array.isArray(data.temperatures)||data.start+data.temperatures.length>watchedMotors.length)return;
 const now=Date.now()-Math.max(0,Number(data.age)||0);data.temperatures.forEach((temperature,i)=>{const m=watchedMotors[data.start+i];m.homeTemperature=Number.isInteger(temperature)&&temperature>=0&&temperature<=255?temperature:null;m.temperatureAt=now});scheduleDashboard();
};

window.dashboardDelete=function(key){
 const m=watchedMotors.find(m=>JSON.stringify(m.config)===key);
 if(!m)return '该电机配置已更新，请重试';
 if(watchBusy())return '请等待设备设置保存完成后删除';
 removeWatchedMotor(m);summaryAt=0;publishDashboard();
 return watchedMotors.includes(m)?'删除未完成，请重试':'已删除电机';
};
window.dashboardEdit=function(key){const m=watchedMotors.find(m=>JSON.stringify(m.config)===key);if(!m)return;closeMotorDetail(false);showTab('motor');openWatchEditor(m)};
window.dashboardOpen=function(key){const m=watchedMotors.find(m=>JSON.stringify(m.config)===key);if(m){openMotorDetail(m)}else{$('rosterPanel').scrollIntoView();watchMessage('该电机配置已更新，请从列表重新选择')}};
let dashboardTimer=0,dashboardPublishedAt=0;
function scheduleDashboard(){if(!dashboardTimer)dashboardTimer=setTimeout(publishDashboard,75)}
function publishDashboard(){clearTimeout(dashboardTimer);dashboardTimer=0;dashboardPublishedAt=Date.now();if(window.CanAndroid&&typeof CanAndroid.dashboard==='function')CanAndroid.dashboard(JSON.stringify(dashboardSnapshot()))}
setInterval(()=>{if(!dashboardTimer&&Date.now()-dashboardPublishedAt>=950)publishDashboard()},1000);publishDashboard();

// Activation is session-only: never exported, persisted or resumed after reconnect.
function stopMotorPolling(m,message){
 if(Motors.profiles[m.config.profile].family!=='dm')return;
 if(window.CanAndroid&&typeof CanAndroid.setPolling==='function')CanAndroid.setPolling(JSON.stringify(m.config),m.config.id,false,m.config.pollHz||100);
 m.polling=false;m.queryPending=false;m.queryTarget=false;m.queryMessage=message;watchedDirty=true;
}
window.setMotorPolling=function(key,enabled){
 const m=watchedMotors.find(m=>JSON.stringify(m.config)===key);if(!m||Motors.profiles[m.config.profile].family!=='dm')return;
 if(m.queryPending&&(enabled||m.queryTarget===false))return;
 if(enabled&&(!connected||watchBusy()||!window.devicePollingSupported)){m.queryMessage='请连接支持定时查询的新固件，并等待设置完成';m.polling=false;refreshWatchedMotors();publishDashboard();return}
 if(!window.CanAndroid||typeof CanAndroid.setPolling!=='function'){m.queryMessage='请使用新版安卓 App 开启查询';m.polling=false;refreshWatchedMotors();return}
 m.queryPending=true;m.queryTarget=!!enabled;m.queryMessage=enabled?'正在开启查询…':'正在关闭查询…';
 if(window.refreshDetail)refreshDetail();CanAndroid.setPolling(key,m.config.id,!!enabled,m.config.pollHz);
};
window.motorQueryState=function(data){const m=watchedMotors.find(m=>JSON.stringify(m.config)===data.key);if(!m)return;m.queryPending=connected&&!!data.pending;m.polling=connected&&!!data.enabled;if(!m.queryPending)m.queryTarget=m.polling;m.queryMessage=data.message;refreshWatchedMotors();publishDashboard()};

window.setMotorQueryHz=function(key,hz){
 const m=watchedMotors.find(m=>JSON.stringify(m.config)===key);if(!m||Motors.profiles[m.config.profile].family!=='dm')return;
 try{if(watchBusy())throw Error('请等待设备设置完成');hz=Motors.number(hz,'查询频率',1,1000,true);
  if(hz!==m.config.pollHz){stopMotorPolling(m,'');m.config.pollHz=hz;m.dashboardMetadata=null;saveWatchedMotors();m.queryMessage='已保存 '+hz+' Hz · 查询已关闭';}
 }catch(e){m.queryMessage=e.message}
 renderWatchedMotors();publishDashboard();if(window.detailRetarget)detailRetarget(m);
};
