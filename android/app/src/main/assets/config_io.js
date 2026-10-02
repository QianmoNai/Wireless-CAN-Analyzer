/* Versioned portable settings. No CAN commands, motion values or runtime feedback. */
(function(root){
'use strict';
const KEY='canScopeChannelV1';
function fail(s){throw Error(s)}
function num(v,min,max,label){if(typeof v!=='number'||!Number.isFinite(v)||v<min||v>max)fail(label+'范围错误');return v}
function integer(v,min,max,label){num(v,min,max,label);if(!Number.isInteger(v))fail(label+'必须为整数');return v}
function string(v,max,label){if(typeof v!=='string'||v.length>max||/[\u0000-\u001f\u007f]/.test(v))fail(label+'格式错误');return v}
function obj(v,label){if(!v||typeof v!=='object'||Array.isArray(v))fail(label+'格式错误');return v}
function motor(raw){
 obj(raw,'电机');const p=Motors.profiles[raw.profile];if(!p||!Object.prototype.hasOwnProperty.call(Motors.profiles,raw.profile))fail('未知电机型号');
 const m={profile:raw.profile,name:string(raw.name===undefined?'':raw.name,32,'电机名称'),id:integer(raw.id,1,p.family==='dji'?p.maxId:0x7fe,'电机 ID'),feedbackId:integer(raw.feedbackId,0,0x7ff,'返回帧 ID')};
 if(p.family==='dm'){
  m.pollHz=integer(raw.pollHz===undefined?100:raw.pollHz,1,1000,'查询频率');
  m.master=integer(raw.master,0,0x7ff,'Master ID');if(m.master!==m.feedbackId)fail('Master ID 与返回帧 ID 不一致');
  if(!Object.prototype.hasOwnProperty.call(Motors.modes,raw.mode))fail('未知达妙模式');m.mode=raw.mode;Motors.dmId(m.id,m.mode);
  obj(raw.limits,'量程');m.limits={};for(const k of ['p','v','t'])m.limits[k]=num(raw.limits[k],0.000001,1e6,'量程 '+k);
 }
 return m;
}
function channel(raw){
 obj(raw,'通道');const c={slot:integer(raw.slot,1,4,'通道编号'),name:string(raw.name,64,'设备名称'),address:string(raw.address,17,'设备地址')};
 if(c.address&&!/^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(c.address))fail('设备地址格式错误');c.address=c.address.toUpperCase();
 if(raw.lastSuccessfulDevice!==undefined){
  const saved=obj(raw.lastSuccessfulDevice,'上次成功连接的设备');
  const address=string(saved.address,17,'上次成功设备地址');
  if(!/^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(address))fail('上次成功设备地址格式错误');
  c.lastSuccessfulDevice={address:address.toUpperCase(),name:string(saved.name,64,'上次成功设备名称')};
 }
 obj(raw.historyLimits,'保存上限');c.historyLimits={rx:integer(raw.historyLimits.rx,0,10000,'RX 保存上限'),tx:integer(raw.historyLimits.tx,0,10000,'TX 保存上限')};
 if(!Array.isArray(raw.motors)||raw.motors.length>32)fail('每通道最多 32 个电机');c.motors=[];
 for(const item of raw.motors){const m=motor(item),family=Motors.profiles[m.profile].family;
  for(const a of c.motors){const af=Motors.profiles[a.profile].family;
   if(a.feedbackId===m.feedbackId&&(af!=='dm'||family!=='dm'||(a.id&15)===(m.id&15)))fail('通道 '+c.slot+' 的电机返回地址冲突');
   if(af===family&&a.id===m.id&&(Motors.profiles[a.profile].feedbackBase===Motors.profiles[m.profile].feedbackBase))fail('通道 '+c.slot+' 的电机 ID 重复');
  }c.motors.push(m);
 }
 if(raw.detailRates!==undefined){obj(raw.detailRates,'反馈上报频率');c.detailRates={};for(const m of c.motors){const key=m.profile+':'+m.id+':'+m.feedbackId;if(Object.prototype.hasOwnProperty.call(raw.detailRates,key))c.detailRates[key]=integer(raw.detailRates[key],1,1000,'反馈上报频率');}}
 return c;
}
function backup(raw){obj(raw,'配置文件');if(raw.format!=='can-scope-config')fail('这不是 CAN Scope 配置文件');if(raw.version!==1)fail('不支持此配置版本，请更新 App');
 if(!Array.isArray(raw.channels)||raw.channels.length<1||raw.channels.length>4)fail('配置须包含 1–4 个通道');
 const channels=raw.channels.map(channel);if(new Set(channels.map(c=>c.slot)).size!==channels.length)fail('通道编号重复');channels.sort((a,b)=>a.slot-b.slot);
 return {format:'can-scope-config',version:1,channels};
}
function parse(text){if(typeof text!=='string'||text.length>1048576)fail('配置文件超过 1 MB');return backup(JSON.parse(text.replace(/^\uFEFF/,'')))}
function readRecord(){const text=localStorage.getItem(KEY);return text?obj(JSON.parse(text),'本地配置'):null}
function readField(key){const saved=readRecord();return saved?saved[key]:JSON.parse(localStorage.getItem(key)||'null')}
function writeField(key,value){const saved=readRecord()||{watchedMotors:readField('watchedMotors')||[],historyLimits:readField('historyLimits')||{rx:300,tx:300}};saved[key]=value;localStorage.setItem(KEY,JSON.stringify(saved))}
function install(raw,revision){try{const c=channel(raw);if(typeof revision!=='string'||!revision)fail('缺少导入标识');let old=null;try{old=readRecord()}catch(e){}if(old&&old.revision===revision)return {ok:true,reload:false};
 // One atomic localStorage replacement: no half-written roster/limits pair.
 localStorage.setItem(KEY,JSON.stringify({revision,watchedMotors:c.motors,historyLimits:c.historyLimits,detailRates:c.detailRates||{}}));return {ok:true,reload:true};
 }catch(e){return {ok:false,error:e.message}}}
function exportChannel(slot,name,address,lastSuccessfulDevice){if(typeof watchBusy==='function'&&watchBusy())fail('请等待通道 '+slot+' 的操作完成再导出');return channel({slot,name,address,lastSuccessfulDevice,historyLimits:{rx:rxSaveLimit,tx:txSaveLimit},motors:watchedMotors.map(m=>m.config),detailRates:readField('detailRates')||{}})}
const api={backup,parse,channel,readField,writeField,install,exportChannel};root.ConfigIO=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
