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
 window.uartRefreshControls=controls;
 function stop(message){run++;if(pending){clearTimeout(pending.timer);const reject=pending.reject;pending=null;reject(Error(message));}sending=false;controls();}
 function stats(){ $('uartStats').textContent=reported?`${baud} baud · 8N1 · RX ${totalRx} 字节 · TX ${totalTx} 字节`:'等待设备状态';$('uartLoss').textContent=`设备丢弃 ${deviceDrops} 字节 · App 检测到缺失 ${lost} 个数据包 · 接收区保留最近 64 KiB`; }
 // Absolute byte blocks keep their DOM nodes when the 64 KiB window slides.
 // Only the trimmed first block, unfinished last block and new blocks change.
 const blockBytes=336,hexBytes=Array.from({length:256},(_,i)=>i.toString(16).padStart(2,'0').toUpperCase());
 const log=$('uartLog');let rendered=new Map(),renderFormat=null,endOffset=0;
 const textDecoder=new TextDecoder('utf-8',{ignoreBOM:true}),firstDecoder=new TextDecoder();
 function resetLog(){endOffset=0;renderFormat=null;}
 function byteAt(offset){return buffer[(head-count+offset-(endOffset-count)+buffer.length)%buffer.length];}
 // Move an interior boundary before a valid, incomplete UTF-8 prefix. Invalid
 // prefixes stay in this block, matching TextDecoder's replacement semantics.
 function textBoundary(end,start){
  for(let pos=end-1;pos>=Math.max(start,end-3);pos--){
   const lead=byteAt(pos),length=lead>=0xc2&&lead<=0xdf?2:lead>=0xe0&&lead<=0xef?3:lead>=0xf0&&lead<=0xf4?4:0;
   if(!length||pos+length<=end)continue;
   let valid=true;
   for(let i=pos+1;i<end;i++){
    const b=byteAt(i);
    if(b<0x80||b>0xbf||(i===pos+1&&((lead===0xe0&&b<0xa0)||(lead===0xed&&b>0x9f)||(lead===0xf0&&b<0x90)||(lead===0xf4&&b>0x8f)))){valid=false;break;}
   }
   if(valid)return pos;
  }
  return end;
 }
 function render(){
  if(!changed||paused||panel.hidden||document.hidden)return;
  changed=false;stats();
  const format=$('uartDisplay').value;
  if(renderFormat!==format){log.replaceChildren();rendered.clear();renderFormat=format;}
  if(!count){log.textContent='等待串口数据…';rendered.clear();return;}
  if(!rendered.size)log.replaceChildren();
  const start=endOffset-count,next=new Map();
  for(let id=Math.floor(start/blockBytes);id*blockBytes<endOffset;id++){
   const boundary=id*blockBytes,limit=Math.min((id+1)*blockBytes,endOffset);
   const from=boundary<=start?start:format==='text'?textBoundary(boundary,start):boundary;
   const to=format==='text'&&limit<endOffset?Math.max(from,textBoundary(limit,start)):limit;
   if(to===from)continue;
   const first=from===start;
   let block=rendered.get(id);
   if(!block){const node=document.createElement('span');node.style.cssText='display:block;contain:layout style';block={node};log.append(node);}
   if(block.from!==from||block.to!==to||block.first!==first){
    let text;
    if(format==='text'){
     const bytes=new Uint8Array(to-from);for(let i=from;i<to;i++)bytes[i-from]=byteAt(i);
     text=(first?firstDecoder:textDecoder).decode(bytes);
    }else{
     const parts=[];for(let i=from;i<to;i++)parts.push(hexBytes[byteAt(i)]);
     text=(first?'':' ')+parts.join(' ');
    }
    if(block.node.firstChild)block.node.firstChild.data=text;else block.node.append(document.createTextNode(text));
    block.from=from;block.to=to;block.first=first;
   }
   next.set(id,block);
  }
  for(const [id,block] of rendered)if(!next.has(id))block.node.remove();
  rendered=next;log.scrollTop=log.scrollHeight;
 }
 function mode(enabled){const previous=window.uartMode;window.uartMode=enabled;
  document.querySelector('[data-tab="live"]').hidden=enabled;document.querySelector('[data-tab="motor"]').hidden=enabled;
  if(previous!==enabled){stop('数据类型已改变，已停止后续发送');lastSequence=null;resetWatchedFeedback();if(window.uartWaveformReset)window.uartWaveformReset();if(enabled)showTab('uart');else if(!panel.hidden)showTab('motor');}
  $('uartModeHint').textContent=enabled?`UART 已启用 · TX=GPIO43，RX=GPIO44 · ${baud} baud，8N1。波特率在设备屏幕设置，须与外部单片机一致。`:'当前为 CAN。请在设备屏幕「设置 → 数据选择」选择 UART；两种数据互斥。';
  document.body.classList.toggle('uart-mode',enabled);
  document.querySelector('.hero .overline').textContent=enabled?'WIRELESS UART MONITOR':'WIRELESS CAN MONITOR';
  watchSettings();
 }
 function receiveBytes(bytes,first,last){
  if(!window.uartMode)return;
  if(lastSequence!==null){const gap=(first-lastSequence-1)>>>0;if(gap<0x80000000)lost+=gap;}
  lastSequence=last;
  endOffset+=bytes.length;
  if(bytes.length>=buffer.length){bytes=bytes.slice(-buffer.length);head=0;}
  const firstPart=Math.min(bytes.length,buffer.length-head);
  buffer.set(bytes.subarray(0,firstPart),head);buffer.set(bytes.subarray(firstPart),0);
  head=(head+bytes.length)%buffer.length;count=Math.min(buffer.length,count+bytes.length);changed=true;
  if(window.uartWaveformBytes)window.uartWaveformBytes(bytes);
 }
 // Desktop batches contain only contiguous runs; gaps split runs in Python.
 window.uartBatch=row=>receiveBytes(Uint8Array.from(row.uart.match(/../g)||[],x=>parseInt(x,16)),row.first,row.last);
 window.uartPacket=a=>{
  if(a[1]===21){reported=true;lastStatusAt=Date.now();baud=u32(a,4);totalRx=u32(a,8);totalTx=u32(a,12);deviceDrops=u32(a,16);portReady=a[3]===0;mode(a[2]===1);stats();controls();return true;}
  if(a[1]===19){if(!a[2]||a[2]>12||a[3])return true;receiveBytes(a.slice(4,4+a[2]),u32(a,16),u32(a,16));return true;}
  if(a[1]===20){if(pending&&u32(a,16)===pending.token){if(a[2]!==0){const reject=pending.reject;clearTimeout(pending.timer);pending=null;reject(Error('设备未完成串口输出（状态 '+a[2]+'）'));}else{pending.acked=true;finish();}}return true;}
  return false;
 };
 function finish(){if(pending&&pending.acked&&pending.written){const resolve=pending.resolve;clearTimeout(pending.timer);pending=null;resolve();}}
 window.uartEvent=(type,d)=>{
  if(type==='state'){reported=false;portReady=false;lastSequence=null;count=head=lost=0;resetLog();changed=true;stop('连接状态改变，已停止发送');if(window.uartWaveformReset)window.uartWaveformReset();mode(false);stats();controls();}
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
 $('uartClear').onclick=()=>{count=head=lost=0;resetLog();lastSequence=null;changed=true;if(window.uartWaveformReset)window.uartWaveformReset();stats();render();};
 $('uartPause').onclick=()=>{paused=!paused;$('uartPause').textContent=paused?'继续显示':'暂停显示';changed=true;render();};
 $('uartDisplay').onchange=()=>{changed=true;render();};
 $('uartFormat').onchange=()=>{$('uartInput').placeholder=$('uartFormat').value==='hex'?'HEX 示例：01 02 FF 00':'输入 UTF-8 文本';};
 setInterval(()=>{render();if(reported&&Date.now()-lastStatusAt>2500){reported=false;portReady=false;controls();$('uartModeHint').textContent='设备状态已过期，等待重新确认数据类型';}},250);
})();
