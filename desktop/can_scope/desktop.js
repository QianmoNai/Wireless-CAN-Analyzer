// Desktop-only additions. Shared Android assets remain unchanged.
(() => {
 const style=document.createElement('style');
 style.textContent=`.app{max-width:1440px;padding:24px 32px}.desktop-bar{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 20px}.desktop-send{margin-top:20px}.desktop-send .form-grid{grid-template-columns:repeat(4,minmax(100px,1fr))}.desktop-result{max-height:140px;overflow:auto;font:12px monospace;white-space:pre-wrap}.desktop-title{font-size:12px;color:#91a0b7}@media(min-width:1100px){.hero{padding:24px}#detailPlots{display:grid;grid-template-columns:1fr 1fr;gap:16px}}`;
 document.head.append(style);
 $('connectionMode').add(new Option('USB直连','usb'));
 const usbHint=document.createElement('p');usbHint.className='hint';usbHint.textContent='USB直连：屏幕切换到 USB直连，用数据线连接原生 USB 口，搜索并选择串口。外接单片机仍使用 TX/RX 引脚。';$('connectionMode').parentElement.append(usbHint);
 document.querySelector('.eyebrow').textContent='DESKTOP · LINUX / WINDOWS';
 const bar=document.createElement('div');bar.className='desktop-bar';
 bar.innerHTML='<button class="secondary" id="desktopExport">导出通道配置</button><button class="secondary" id="desktopImport">导入通道配置</button><button class="secondary" id="desktopCsv">导出当前帧记录 CSV</button><span class="desktop-title">CAN Scope Desktop 1.2.0 · 各通道配置独立保存</span>';
 document.querySelector('.app').prepend(bar);
 $('desktopExport').onclick=()=>{try{invoke('exportConfig',JSON.stringify({format:'can-scope-config',version:1,channels:[ConfigIO.exportChannel(1,$('deviceName').textContent,'')]}))}catch(e){toast(e.message)}};
 $('desktopImport').onclick=()=>invoke('importConfig');
 $('desktopCsv').onclick=()=>invoke('exportCsv',JSON.stringify(frames));
 window.desktopImport=text=>{try{if(connected)throw Error('请先断开当前通道，再导入配置');const backup=ConfigIO.parse(text);let c=backup.channels[0];if(backup.channels.length>1){const slot=Number(prompt('配置含多个通道，请输入要导入当前标签页的通道编号：',String(c.slot)));c=backup.channels.find(x=>x.slot===slot);if(!c)throw Error('已取消或编号无效')}const result=ConfigIO.install(c,String(Date.now()));if(!result.ok)throw Error(result.error);location.reload()}catch(e){toast(e.message)}};
 const cards=document.createElement('section');cards.className='panel';cards.innerHTML='<h3>设备状态与曲线</h3><p class="hint">点击设备卡片查看实时曲线和反馈；在下方添加或编辑设备。</p><div id="desktopMotors" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px"></div>';$('view-motor').prepend(cards);
 const cardMap=new Map();
 const oldDashboard=window.publishDashboard;
 window.publishDashboard=function(){oldDashboard();const rows=dashboardSnapshot(),keys=new Set(rows.map(m=>m.key));for(const [key,button] of cardMap)if(!keys.has(key)){button.remove();cardMap.delete(key)}for(const m of rows){let button=cardMap.get(m.key);if(!button){button=document.createElement('button');button.className='secondary';button.style.cssText='text-align:left;line-height:1.8;min-height:100px';button.onclick=()=>dashboardOpen(m.key);cardMap.set(m.key,button);$('desktopMotors').append(button)}const temperature=Number.isFinite(m.temperature)&&m.temperatureAge<2000?m.temperature+' °C':'—';button.textContent=m.name+' · '+m.id+'\n'+(connected?(m.connectionStatus==='online'?'反馈在线':m.connectionStatus==='offline'?'反馈超时':'等待反馈'):'未连接')+' · 温度 '+temperature;button.style.whiteSpace='pre-line'} };
 publishDashboard();
 const panel=document.createElement('section');panel.className='panel desktop-send';
 panel.innerHTML='<h3>手动发送 CAN 帧</h3><p class="hint">单次发送 · 标准帧 / 扩展帧 · 数据帧 / 远程帧</p><div class="form-grid"><div class="field"><label>CAN ID（十六进制）</label><input id="sendId" value="123" maxlength="8"></div><div class="field"><label>帧格式</label><select id="sendFormat"><option value="std">标准帧</option><option value="ext">扩展帧</option></select></div><div class="field"><label>帧类型</label><select id="sendKind"><option value="data">数据帧</option><option value="rtr">远程帧</option></select></div><div class="field"><label>DLC</label><input id="sendDlc" type="number" min="0" max="8" value="8"></div></div><div class="field"><label>数据（空格分隔的十六进制字节）</label><input id="sendData" value="00 00 00 00 00 00 00 00" maxlength="23"></div><button class="primary" id="sendOnce">发送一次</button><p class="hint">提交成功后继续等待 CAN 结果；超时显示结果未知，不自动重复发送。</p><div class="desktop-result" id="sendResult" role="status"></div>';
 $('view-live').append(panel);
 $('sendKind').onchange=()=>{$('sendData').disabled=$('sendKind').value==='rtr'};
 $('sendOnce').onclick=()=>{try{if(!connected)throw Error('请先连接设备');if(window.uartMode)throw Error('请先在设备屏幕切回 CAN');const id=$('sendId').value.trim().replace(/^0x/i,''),raw=$('sendData').value.trim(),remote=$('sendKind').value==='rtr',dlc=Number($('sendDlc').value);if(!/^[0-9a-f]{1,8}$/i.test(id))throw Error('请输入十六进制 CAN ID');if(!remote&&raw&&!/^[0-9a-f]{2}(\s+[0-9a-f]{2})*$/i.test(raw))throw Error('数据须为两位十六进制字节，以空格分隔');const data=remote?[]:raw?raw.split(/\s+/).map(x=>parseInt(x,16)):[];if(!Number.isInteger(dlc)||dlc<0||dlc>8||(!remote&&data.length!==dlc))throw Error('DLC 与数据长度不一致');invoke('manual',{identifier:parseInt(id,16),data,extended:$('sendFormat').value==='ext',remote,dlc})}catch(e){toast(e.message)}};
 const original=window.onNative;
 window.onNative=(type,d)=>{if(type==='manualResult'){const line=document.createElement('div');line.textContent=new Date().toLocaleTimeString()+' #'+d.token+' '+d.status;$('sendResult').prepend(line);while($('sendResult').children.length>40)$('sendResult').lastChild.remove()}else original(type,d);$('desktopCsv').disabled=!!window.uartMode;$('desktopCsv').title=window.uartMode?'CSV 导出适用于 CAN 帧记录':''};
 // App lifecycle copy differs on desktop: minimizing does not disconnect.
 document.querySelectorAll('.protocol').forEach(p=>{p.textContent='CAN Scope Desktop 1.2.0 · 本地运行 · 关闭窗口断开连接；最小化继续监控。'});
 document.querySelectorAll('p').forEach(p=>{if(p.textContent.includes('在手机输入设备屏幕'))p.textContent=p.textContent.replace('在手机输入设备屏幕','在系统蓝牙配对窗口输入设备屏幕')});
})();
