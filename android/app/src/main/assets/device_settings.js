'use strict';
window.devicePollingSupported=false;window.bridgeBitrate=0;window.nameSaving=false;window.deviceSettingsBusy=false;
let capabilities=0,currentBleName='',settingsRequest=null,nameEdited=false,settingsToken=1;
function watchSettings(){window.deviceSettingsBusy=!!settingsRequest;window.nameSaving=!!settingsRequest&&settingsRequest.kind==='name';$('saveBleName').disabled=!connected||!(capabilities&4)||!!settingsRequest;$('saveBitrate').disabled=!!window.uartMode||!connected||!(capabilities&1)||!!settingsRequest;$('busBitrate').disabled=!!window.uartMode||!!settingsRequest}
function finishSettings(message){if(!settingsRequest)return;const kind=settingsRequest.kind;clearTimeout(settingsRequest.timer);settingsRequest=null;$(kind==='name'?'bleNameResult':'rateResult').textContent=message;watchSettings()}
function deviceWriteFailed(message){finishSettings(message)}
function nameInputChanged(){nameEdited=true;$('bleNameResult').textContent=new TextEncoder().encode($('bleNameInput').value.trim()).length+' / 16 字节'}
function startSettings(kind,request,a){if(!connected)throw Error('请先连接设备');if(settingsRequest)throw Error('请等待当前设置完成');settingsRequest={kind,...request,timer:setTimeout(()=>finishSettings('未收到设置结果；状态未知，请重连确认'),7000)};watchSettings();$(kind==='name'?'bleNameResult':'rateResult').textContent='正在保存到 ESP32…';invoke('send',hexOf(a))}
function saveBleName(){try{
 if(!(capabilities&4))throw Error('当前固件不支持永久保存名称，请更新固件');const name=$('bleNameInput').value.trim(),bytes=new TextEncoder().encode(name);
 if(!bytes.length||bytes.length>16||/[\u0000-\u001f\u007f-\u009f]/.test(name))throw Error('名称须为 1–16 个 UTF-8 字节，不能包含控制字符');
 const a=new Uint8Array(20);a[0]=1;a[1]=0x13;a[2]=bytes.length;a.set(bytes,4);startSettings('name',{name},a);
 }catch(e){$('bleNameResult').textContent=e.message}}
function saveBitrate(){try{if(window.uartMode)throw Error('请先在设备屏幕切回 CAN 数据');if(!(capabilities&1))throw Error('固件不支持设置波特率');const rate=Number($('busBitrate').value);if(![125000,250000,500000,1000000].includes(rate))throw Error('无效波特率');
 const a=new Uint8Array(20);a[0]=1;a[1]=0x12;put32(a,4,rate);const seq=settingsToken++;put32(a,16,seq);startSettings('rate',{seq,rate},a);
 }catch(e){$('rateResult').textContent=e.message}}
function devicePacket(a){
 if(a[1]===5){const reportedRate=u32(a,4);if(bridgeBitrate&&bridgeBitrate!==reportedRate){clearDeviceLoad();clearDeviceRates();}bridgeBitrate=reportedRate;capabilities=a[3];window.cleanTxCounters=!!(capabilities&32);renderFailureCount();window.devicePollingSupported=!!(capabilities&8);$('bitrateValue').textContent=bridgeBitrate/1000;$('settingsBitrate').textContent=bridgeBitrate/1000+' kbit/s';$('rateCurrent').textContent='当前速率：'+bridgeBitrate/1000+' kbit/s';
  if(!(capabilities&4))$('bleNameResult').textContent='当前 ESP32 固件需更新后才能永久保存蓝牙名称';
  if(settingsRequest&&settingsRequest.kind==='rate'&&u32(a,16)===settingsRequest.seq)finishSettings(a[2]===0?'波特率已应用':'修改失败，请检查设备状态');watchSettings();return;
 }
 if(a[1]!==7||a[3]<1||a[3]>16||a[2]>2)return;let name;try{name=new TextDecoder('utf-8',{fatal:true}).decode(a.slice(4,4+a[3]))}catch(e){return}
 currentBleName=name;$('bleNameCurrent').textContent='设备当前名称：'+name;if(!nameEdited&&!window.nameSaving)$('bleNameInput').value=name;
 if(settingsRequest&&settingsRequest.kind==='name'){
  if(a[2]===1&&name===settingsRequest.name){nameEdited=false;$('bleNameInput').value=name;finishSettings('已永久保存；断电重启保留，重新扫描时显示新名称')}
  else if(a[2]===2)finishSettings('设备保存失败，请重试或重连确认名称');
 }
}
function deviceConnection(){
 if(settingsRequest)finishSettings('连接变化，设置结果未知；重连后确认');
 window.devicePollingSupported=false;bridgeBitrate=0;capabilities=0;currentBleName='';nameEdited=false;$('bleNameInput').value='';$('bleNameCurrent').textContent='连接后读取设备名称';$('bitrateValue').textContent='—';$('settingsBitrate').textContent='等待报告';$('rateCurrent').textContent='等待设备报告';watchSettings();
}
watchSettings();
