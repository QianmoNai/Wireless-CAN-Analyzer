'use strict';
let transportMode='ble',wifiBusy=false;
function refreshNetworkControls(){
 $('connectionMode').value=transportMode;$('transportBadge').textContent=transportMode==='usb'?'USB直连':transportMode==='wifi'?'Wi-Fi':'BLE';
 $('lanManual').hidden=transportMode!=='wifi';
 for(const id of ['wifiRefresh','wifiScan','wifiSave','wifiClear'])$(id).disabled=wifiBusy||!connected||transportMode!=='ble';
 $('connectionMode').disabled=wifiBusy;$('wifiPassword').disabled=wifiBusy;$('wifiSsid').disabled=wifiBusy;$('wifiNetworks').disabled=wifiBusy;
}
function selectConnectionMode(value){invoke('connectionMode',value)}
function wifiRequest(op){
 if(wifiBusy)return;
 const ssid=$('wifiSsid').value,password=$('wifiPassword').value;
 if(op===5){const count=new TextEncoder().encode(ssid).length;if(!count||count>32){$('wifiResult').textContent='Wi-Fi 名称须为 1–32 个 UTF-8 字节';return;}if(password&&!(/^[\x20-\x7e]{8,63}$/.test(password)||/^[0-9a-fA-F]{64}$/.test(password))){$('wifiResult').textContent='密码须为 8–63 个英文字符；开放网络留空';return;}}
 if(op===6&&!confirm('清除设备保存的 Wi-Fi 名称和密码？之后可通过蓝牙重新配置。'))return;
 invoke('wifiConfigure',op,ssid,password);if(op===5)$('wifiPassword').value='';
}
function wifiSelect(){const value=$('wifiNetworks').value;if(value)$('wifiSsid').value=value;}
function connectManualLan(){invoke('connectLan',$('lanIp').value.trim());}
function decodeSsid(hex){try{return new TextDecoder('utf-8',{fatal:true}).decode(Uint8Array.from(hex.match(/.{2}/g)||[],x=>parseInt(x,16)))}catch(e){return '';}}
window.networkEvent=function(type,d){
 if(type==='networkMode'){if(typeof d.busy==='boolean')wifiBusy=d.busy;transportMode=d.mode==='usb'&&$('connectionMode').querySelector('option[value=usb]')?'usb':d.mode==='wifi'?'wifi':'ble';if(d.ip)$('lanIp').value=d.ip;$('wifiKnown').textContent=d.paired?'已保存此设备的安全连接信息':'首次使用 Wi-Fi 请先通过蓝牙配网';refreshNetworkControls();}
 else if(type==='wifiResult'){wifiBusy=!!d.busy;$('wifiResult').textContent=d.message||'';refreshNetworkControls();}
 else if(type==='wifiStatus'){const statuses=['未配置 / 待机','连接中','Wi-Fi 已连接','已断开','连接失败'];$('wifiState').textContent=(statuses[d.state]||'未知状态')+' · 设备模式：'+(d.mode?'Wi-Fi':'蓝牙')+(d.ip?' · IP '+d.ip:'');const ssid=decodeSsid(d.ssid||'');if(ssid)$('wifiSsid').value=ssid;}
 else if(type==='wifiScan'){const select=$('wifiNetworks');select.replaceChildren(new Option('手动输入 / 隐藏网络',''));for(const row of d.rows||[]){const ssid=decodeSsid(row[0]);if(ssid)select.add(new Option(ssid+' · '+row[1]+' dBm'+(row[2]===0?' · 开放':''),ssid));}}
};
const previousDeviceConnection=window.deviceConnection;
window.deviceConnection=function(){if(previousDeviceConnection)previousDeviceConnection();if(!connected)wifiBusy=false;refreshNetworkControls();invoke('networkState');};
refreshNetworkControls();invoke('networkState');
