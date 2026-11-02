'use strict';
// UART uses a separate packet family: arbitrary bytes never reach CAN decoders.
window.uartMode=false;
(()=>{
 const style=document.createElement('style');style.textContent='.uart-log{white-space:pre-wrap;overflow-wrap:anywhere;max-height:42vh;min-height:160px;overflow:auto;background:#101a29;padding:14px;border-radius:12px;font:13px/1.7 monospace}.uart-input{width:100%;min-height:100px;resize:vertical;background:#101a29;color:#e7edf6;border:1px solid #304056;border-radius:10px;padding:12px;box-sizing:border-box}.uart-status{font-variant-numeric:tabular-nums}.tabs{overflow-x:auto}.tabs .tab{white-space:nowrap}#view-uart select{background:#0b1320;color:var(--ink);border:1px solid #304059;border-radius:10px;padding:9px;max-width:100%}#view-uart .form-grid{margin-bottom:14px}#view-uart .form-grid label{display:flex;flex-direction:column;gap:8px;color:var(--muted);font-size:12px}#view-uart .section-row{flex-wrap:wrap;margin:18px 0 10px;gap:8px}#view-uart .section-row h3{flex:1;white-space:nowrap}#view-uart .section-row button{padding:9px 12px}body.uart-mode .hero .speed{display:none}';document.head.append(style);
 const tab=document.createElement('button');tab.className='tab';tab.dataset.tab='uart';tab.textContent='UART 串口';tab.onclick=()=>showTab('uart');document.querySelector('.tabs').append(tab);
 const panel=document.createElement('section');panel.className='view';panel.id='view-uart';panel.hidden=true;
 panel.innerHTML=`<div class="panel"><h3>无线串口</h3><p id="uartModeHint" class="hint">在设备屏幕「设置 → 数据选择」选择 UART。串口为 3.3V TTL，TX/RX 交叉连接并共地。</p><p class="uart-status" id="uartStats">等待设备状态</p><p class="hint" id="uartLoss">接收区保留最近 64 KiB；无线吞吐不足时可能丢失数据。</p></div><div class="section-row"><h3>接收数据</h3><select id="uartDisplay" aria-label="接收显示格式"><option value="hex">HEX</option><option value="text">文本 UTF-8</option></select><button class="secondary" id="uartPause">暂停显示</button><button class="secondary" id="uartClear">清空</button></div><pre id="uartLog" class="uart-log" aria-label="串口接收数据">等待串口数据…</pre><div class="panel"><h3>发送到串口</h3><div class="form-grid"><label>格式<select id="uartFormat"><option value="hex">HEX</option><option value="text">文本 UTF-8</option></select></label><label>末尾追加<select id="uartEnding"><option value="">无</option><option value="lf">LF</option><option value="crlf">CRLF</option></select></label></div><textarea id="uartInput" class="uart-input" maxlength="12288" placeholder="HEX 示例：01 02 FF 00" aria-label="串口发送内容"></textarea><button class="primary wide" id="uartSend" disabled>发送</button><button class="secondary" id="uartCancel" hidden>停止后续发送</button><p class="hint" id="uartResult" role="status">每次最多 4096 字节；发送结果表示设备串口输出完成。</p></div>`;
 document.querySelector('#view-live').before(panel);
 const buffer=new Uint8Array(65536);let count=0,head=0,changed=false,paused=false,lastSequence=null,lost=0;
 let reported=false,portReady=false,baud=0,totalRx=0,totalTx=0,deviceDrops=0,token=0x71000000,pending=null,sending=false,run=0,lastStatusAt=0;
 function controls(){ $('uartSend').disabled=!connected||!reported||!window.uartMode||!portReady||sending;$('uartCancel').hidden=!sending; }
 function stop(message){run++;if(pending){clearTimeout(pending.timer);const reject=pending.reject;pending=null;reject(Error(message));}sending=false;controls();}
 function stats(){ $('uartStats').textContent=reported?`${baud} baud · 8N1 · RX ${totalRx} 字节 · TX ${totalTx} 字节`:'等待设备状态';$('uartLoss').textContent=`设备丢弃 ${deviceDrops} 字节 · App 检测到缺失 ${lost} 个数据包 · 接收区保留最近 64 KiB`; }
 // Keep small layout blocks: a single constantly replaced 192 KiB HEX
 // paragraph is expensive for WebEngine's layout and compositor.
 const hexBytes=Array.from({length:256},(_,i)=>i.toString(16).padStart(2,'0').toUpperCase());
 const log=$('uartLog');let rendered=[];
 function render(){
  if(!changed||paused||panel.hidden||document.hidden)return;
  changed=false;stats();
  const bytes=new Uint8Array(count),start=(head-count+buffer.length)%buffer.length;
  const first=Math.min(count,buffer.length-start);
  bytes.set(buffer.subarray(start,start+first));bytes.set(buffer.subarray(0,count-first),first);
  let text='等待串口数据…';
  if(count)text=$('uartDisplay').value==='text'?new TextDecoder().decode(bytes):Array.from(bytes,b=>hexBytes[b]).join(' ');
  const parts=[];
  for(let offset=0;offset<text.length;){
   let end=Math.min(offset+1023,text.length);
   // Do not split a UTF-16 surrogate pair between layout blocks.
   if(end<text.length&&text.charCodeAt(end-1)>=0xd800&&text.charCodeAt(end-1)<=0xdbff)end--;
   parts.push(text.slice(offset,end));offset=end;
  }
  if(!log.firstElementChild){log.replaceChildren();rendered=[];}
  parts.forEach((part,i)=>{
   let block=log.children[i];
   if(!block){block=document.createElement('span');block.style.cssText='display:block;contain:layout style';log.append(block);}
   if(rendered[i]!==part){if(block.firstChild)block.firstChild.data=part;else block.append(document.createTextNode(part));}
  });
  while(log.children.length>parts.length)log.lastChild.remove();
  rendered=parts;log.scrollTop=log.scrollHeight;
 }
 function mode(enabled){const previous=window.uartMode;window.uartMode=enabled;
  document.querySelector('[data-tab="live"]').hidden=enabled;document.querySelector('[data-tab="motor"]').hidden=enabled;
  if(previous!==enabled){stop('数据类型已改变，已停止后续发送');lastSequence=null;resetWatchedFeedback();if(enabled)showTab('uart');else if(!panel.hidden)showTab('motor');}
  $('uartModeHint').textContent=enabled?`UART 已启用 · TX=GPIO43，RX=GPIO44 · ${baud} baud，8N1。波特率在设备屏幕设置，须与外部单片机一致。`:'当前为 CAN。请在设备屏幕「设置 → 数据选择」选择 UART；两种数据互斥。';
  document.body.classList.toggle('uart-mode',enabled);
  document.querySelector('.hero .overline').textContent=enabled?'WIRELESS UART MONITOR':'WIRELESS CAN MONITOR';
  watchSettings();
 }
 window.uartPacket=a=>{
  if(a[1]===21){reported=true;lastStatusAt=Date.now();baud=u32(a,4);totalRx=u32(a,8);totalTx=u32(a,12);deviceDrops=u32(a,16);portReady=a[3]===0;mode(a[2]===1);stats();controls();return true;}
  if(a[1]===19){if(!window.uartMode||!a[2]||a[2]>12||a[3])return true;const seq=u32(a,16);if(lastSequence!==null){const gap=(seq-lastSequence-1)>>>0;if(gap<0x80000000)lost+=gap;}lastSequence=seq;for(const byte of a.slice(4,4+a[2])){buffer[head]=byte;head=(head+1)%buffer.length;count=Math.min(buffer.length,count+1);}changed=true;return true;}
  if(a[1]===20){if(pending&&u32(a,16)===pending.token){if(a[2]!==0){const reject=pending.reject;clearTimeout(pending.timer);pending=null;reject(Error('设备未完成串口输出（状态 '+a[2]+'）'));}else{pending.acked=true;finish();}}return true;}
  return false;
 };
 function finish(){if(pending&&pending.acked&&pending.written){const resolve=pending.resolve;clearTimeout(pending.timer);pending=null;resolve();}}
 window.uartEvent=(type,d)=>{
  if(type==='state'){reported=false;portReady=false;lastSequence=null;count=head=lost=0;changed=true;stop('连接状态改变，已停止发送');mode(false);stats();controls();}
  if(type==='writeDone'&&pending){if(d.success){pending.written=true;finish();}else stop('通信写入失败，已停止后续发送');}
  if(type==='error'&&pending)stop(d.message||'通信错误');
 };
 function chunk(bytes){return new Promise((resolve,reject)=>{const p=new Uint8Array(20);p[0]=1;p[1]=0x18;p[2]=bytes.length;p.set(bytes,4);token=(token+1)>>>0||1;put32(p,16,token);pending={token,resolve,reject,acked:false,written:false,timer:setTimeout(()=>{if(pending&&pending.token===token){pending=null;reject(Error('确认超时，当前分段结果未知；未自动重发'));}},3000)};invoke('send',hexOf(p));});}
 $('uartSend').onclick=async()=>{
  if(sending||!connected||!reported||!window.uartMode||!portReady)return;
  let bytes;try{const value=$('uartInput').value;if($('uartFormat').value==='hex'){const hex=value.replace(/\s/g,'');if(!hex||hex.length%2||!/^[0-9a-f]+$/i.test(hex))throw Error('请输入完整的十六进制字节，例如 01 02 FF');bytes=Uint8Array.from(hex.match(/../g),v=>parseInt(v,16));}else bytes=new TextEncoder().encode(value);
   const ending=$('uartEnding').value;const tail=ending==='lf'?[10]:ending==='crlf'?[13,10]:[];bytes=Uint8Array.from([...bytes,...tail]);if(!bytes.length||bytes.length>4096)throw Error('发送长度须为 1–4096 字节');
  }catch(e){$('uartResult').textContent=e.message;return;}
  sending=true;const epoch=++run;controls();let confirmed=0;$('uartResult').textContent='正在发送…';
  try{for(let offset=0;offset<bytes.length;offset+=12){if(epoch!==run||!connected||!window.uartMode)throw Error('发送已停止');const part=bytes.slice(offset,offset+12);await chunk(part);confirmed+=part.length;$('uartResult').textContent=`已确认输出 ${confirmed} / ${bytes.length} 字节`;}}
  catch(e){$('uartResult').textContent=`已确认 ${confirmed} 字节；${e.message}。外部单片机是否处理成功需由其协议确认。`;}
  finally{if(epoch===run){sending=false;controls();}}
 };
 $('uartCancel').onclick=()=>stop('已停止后续发送，已提交的字节可能仍在输出');
 $('uartClear').onclick=()=>{count=head=lost=0;lastSequence=null;changed=true;stats();render();};
 $('uartPause').onclick=()=>{paused=!paused;$('uartPause').textContent=paused?'继续显示':'暂停显示';changed=true;render();};
 $('uartDisplay').onchange=()=>{changed=true;render();};
 $('uartFormat').onchange=()=>{$('uartInput').placeholder=$('uartFormat').value==='hex'?'HEX 示例：01 02 FF 00':'输入 UTF-8 文本';};
 setInterval(()=>{render();if(reported&&Date.now()-lastStatusAt>2500){reported=false;portReady=false;controls();$('uartModeHint').textContent='设备状态已过期，等待重新确认数据类型';}},250);
})();
