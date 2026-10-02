'use strict';
const $=id=>document.getElementById(id);
let channels=[],receivedAt=0,channelSignature='',reconnectBusy=false,homeConfigBusy=false;
const cards=new Map();
function callNative(method,...args){if(window.HomeAndroid&&typeof HomeAndroid[method]==='function')HomeAndroid[method](...args)}
$('addChannel').onclick=()=>callNative('addChannel');$('filter').onchange=()=>{endGesture(false);renderHome()};
function updateConnectButton(){const button=$('connectAll');button.disabled=reconnectBusy||homeConfigBusy;button.textContent=reconnectBusy?'连接中…':'连接';button.setAttribute('aria-busy',String(reconnectBusy))}
$('connectAll').onclick=()=>{if(reconnectBusy||homeConfigBusy)return;if(!window.HomeAndroid||typeof HomeAndroid.connectAll!=='function')return;window.reconnectStatus({busy:true,message:'正在连接各通道上次成功连接的设备…'});callNative('connectAll')};
window.reconnectStatus=function(data){reconnectBusy=!!(data&&data.busy);const message=data&&typeof data.message==='string'?data.message:'';$('reconnectNotice').textContent=message;$('reconnectNotice').hidden=!message;updateConnectButton()};
window.updateHome=function(data){channels=Array.isArray(data)?data:[];receivedAt=Date.now();renderHome()};
const ORDER_KEY='canScopeHomeOrderV1';
let drag=null,gesture=null,sortOrder=[],customOrder=false;
try{const saved=JSON.parse(localStorage.getItem(ORDER_KEY)||'[]');if(Array.isArray(saved))sortOrder=[...new Set(saved.filter(x=>typeof x==='string'))].slice(0,512);customOrder=sortOrder.length>0;}catch(e){}
function motorOrderKey(slot,m){try{const c=JSON.parse(m.key);return JSON.stringify([slot,c.profile,c.id,c.feedbackId])}catch(e){return slot+'|'+m.key}}
let noticeTimer;
function saveOrder(){clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{$('sortNotice').hidden=true},2200);customOrder=true;try{localStorage.setItem(ORDER_KEY,JSON.stringify(sortOrder));$('sortNotice').hidden=false;$('sortNotice').textContent='排列已保存'}catch(e){$('sortNotice').textContent='排列保存失败，请重试'}}
const reduceMotion=()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function animateReflow(change){
 const nodes=[...$('cards').children],before=new Map(nodes.map(c=>[c,c.getBoundingClientRect()]));
 for(const c of nodes){if(c.layoutMotion)c.layoutMotion.cancel();}
 change();
 if(reduceMotion())return;
 for(const c of nodes){if(!c.isConnected||c===drag?.card)continue;const old=before.get(c),now=c.getBoundingClientRect(),dx=old.left-now.left,dy=old.top-now.top;
  if(Math.abs(dx)+Math.abs(dy)<1)continue;
  c.layoutMotion=c.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:'translate(0,0)'}],{duration:240,easing:'cubic-bezier(.2,.8,.2,1)'});
 }
}
function liftCard(g){
 clearTimeout(noticeTimer);
 const card=g.card,rect=card.getBoundingClientRect(),ghost=card.cloneNode(true);
 ghost.className='drag-preview';ghost.removeAttribute('role');ghost.removeAttribute('tabindex');ghost.setAttribute('aria-hidden','true');
 ghost.style.width=rect.width+'px';ghost.style.height=rect.height+'px';document.body.append(ghost);
 drag={card,ghost,x:g.lastX,y:g.lastY,offsetX:g.lastX-rect.left,offsetY:g.lastY-rect.top,raf:0,dirty:true,lastFrame:performance.now()};
 card.classList.remove('pressing');card.classList.add('dragging');
 const tick=now=>{const d=drag;if(!d)return;const dt=Math.min(32,now-d.lastFrame);d.lastFrame=now;
  d.ghost.style.transform=`translate3d(${d.x-d.offsetX}px,${d.y-d.offsetY}px,0) scale(1.035)`;
  const edge=90,velocity=d.y<edge?-Math.min(1,(edge-d.y)/edge):d.y>innerHeight-edge?Math.min(1,(d.y-innerHeight+edge)/edge):0;
  if(velocity){const before=scrollY;window.scrollBy(0,velocity*dt*.65);if(scrollY!==before)d.dirty=true;}
  if(d.dirty){d.dirty=false;placeDrag();}d.raf=requestAnimationFrame(tick);
 };tick(performance.now());
}
function finishDrag(commit){
 if(!drag)return;const d=drag;if(commit&&d.ghost)placeDrag();drag=null;cancelAnimationFrame(d.raf);
 if(!commit)$('sortNotice').hidden=true;
 if(commit){const visible=[...$('cards').children].map(c=>c.dataset.orderKey);const keys=new Set(visible);let i=0;sortOrder=sortOrder.map(k=>keys.has(k)?visible[i++]:k);saveOrder()}
 renderHome();
 if(d.ghost){const rect=d.card.getBoundingClientRect();const start=d.ghost.style.transform;
  const cleanup=()=>{d.ghost.remove();if(drag?.card!==d.card)d.card.classList.remove('dragging')};
  if(reduceMotion()||document.hidden)cleanup();else{const motion=d.ghost.animate([{transform:start,opacity:1},{transform:`translate3d(${rect.left}px,${rect.top}px,0) scale(1)`,opacity:1}],{duration:210,easing:'cubic-bezier(.2,.8,.2,1)',fill:'forwards'});motion.onfinish=cleanup;motion.oncancel=cleanup;}
 }else d.card.classList.remove('dragging');
}
function placeDrag(){
 if(!drag)return;const target=document.elementFromPoint(drag.x,drag.y)?.closest('.motor');if(!target||target===drag.card||target.parentElement!==$('cards'))return;
 const list=[...$('cards').children];animateReflow(()=>$('cards').insertBefore(drag.card,list.indexOf(drag.card)<list.indexOf(target)?target.nextSibling:target));
}
function beginGesture(card,slot,key,x,y){
 endGesture(false);card.blockClick=false;if(card.layoutMotion)card.layoutMotion.cancel();card.classList.add('pressing');
 gesture={card,slot,key,x,y,lastX:x,lastY:y,mode:'pending',timer:0};
 gesture.timer=setTimeout(()=>{
  const g=gesture;if(!g||g.card!==card||!card.isConnected)return;
  g.mode='drag';card.blockClick=true;liftCard(g);
  $('sortNotice').hidden=false;$('sortNotice').textContent='拖动调整顺序，松手保存';
 },550);
}
function moveGesture(x,y){
 const g=gesture;if(!g)return false;g.lastX=x;g.lastY=y;const dx=x-g.x,dy=y-g.y;
 if(g.mode==='pending'&&Math.hypot(dx,dy)>10){clearTimeout(g.timer);g.card.classList.remove('pressing');g.card.blockClick=true;g.mode=Math.abs(dx)>Math.abs(dy)*1.3?'swipe':'scroll'}
 if(g.mode==='drag'){drag.x=x;drag.y=y;drag.dirty=true;return true}
 if(g.mode==='swipe'){g.card.classList.add('swiping');g.card.style.transform='translate3d('+(Math.sign(dx)*Math.min(105,Math.abs(dx)*.8))+'px,0,0)';g.card.dataset.swipeLabel=Math.abs(dx)>=55?(dx<0?'松手删除':'松手配置'):(dx<0?'← 左滑删除':'右滑配置 →');g.card.classList.toggle('swipe-delete',dx<0);g.card.classList.toggle('swipe-edit',dx>=0);return true}
 return false;
}
function endGesture(commit){
 const g=gesture;gesture=null;if(!g){finishDrag(false);return}clearTimeout(g.timer);
 const from=g.card.style.transform;g.card.style.transform='';g.card.classList.remove('pressing','swiping','swipe-delete','swipe-edit');
 if(from&&!reduceMotion())g.card.animate([{transform:from},{transform:'translate3d(0,0,0)'}],{duration:220,easing:'cubic-bezier(.16,1,.3,1)'});
 if(g.mode==='drag'){finishDrag(commit);return}
 if(commit&&g.mode==='swipe'&&Math.abs(g.lastX-g.x)>=55&&Math.abs(g.lastX-g.x)>Math.abs(g.lastY-g.y)*1.3){callNative(g.lastX<g.x?'deleteMotor':'editMotor',g.slot,g.key)}
 renderHome();
}
window.addEventListener('blur',()=>endGesture(false));
window.addEventListener('resize',()=>{endGesture(false);document.querySelectorAll('.drag-preview').forEach(g=>{g.getAnimations().forEach(a=>a.cancel());g.remove()});document.querySelectorAll('.dragging').forEach(c=>c.classList.remove('dragging'));for(const c of cards.values())if(c.layoutMotion)c.layoutMotion.cancel()});
document.addEventListener('visibilitychange',()=>{if(document.hidden)endGesture(false)});
document.addEventListener('pointermove',e=>{if(e.pointerType!=='touch')moveGesture(e.clientX,e.clientY)});
document.addEventListener('pointerup',e=>{if(e.pointerType!=='touch')endGesture(true)});
document.addEventListener('pointercancel',e=>{if(e.pointerType!=='touch')endGesture(false)});
function bindMotorCard(card,slot,key){
 card.addEventListener('pointerdown',e=>{if(e.pointerType!=='touch'&&e.isPrimary&&e.button===0)beginGesture(card,slot,key,e.clientX,e.clientY)});
 card.addEventListener('touchstart',e=>{if(e.touches.length!==1){endGesture(false);return}const t=e.touches[0];beginGesture(card,slot,key,t.clientX,t.clientY)},{passive:true});
 card.addEventListener('touchmove',e=>{if(e.touches.length!==1){endGesture(false);return}const t=e.touches[0];if(moveGesture(t.clientX,t.clientY)&&e.cancelable)e.preventDefault()},{passive:false});
 card.addEventListener('touchend',e=>{const block=gesture&&['drag','swipe'].includes(gesture.mode);if(block&&e.cancelable)e.preventDefault();endGesture(true)},{passive:false});
 card.addEventListener('touchcancel',()=>endGesture(false));
 card.addEventListener('contextmenu',e=>e.preventDefault());
 card.onclick=e=>{if(card.blockClick){e.preventDefault();card.blockClick=false;return}callNative('openMotor',slot,key)};
 card.onkeydown=e=>{
  if(e.target!==card)return;
  if(e.key==='Escape'){endGesture(false);return}
  if(e.key==='Delete'){e.preventDefault();callNative('deleteMotor',slot,key)}
  else if(e.key==='F2'){e.preventDefault();callNative('editMotor',slot,key)}
  else if(e.altKey&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const list=[...$('cards').children],i=list.indexOf(card),j=i+(['ArrowLeft','ArrowUp'].includes(e.key)?-1:1);if(j<0||j>=list.length)return;drag={card};$('cards').insertBefore(card,j>i?list[j].nextSibling:list[j]);finishDrag(true);card.focus()}
  else if(e.key==='Enter'||e.key===' '){e.preventDefault();callNative('openMotor',slot,key)}
 };
 card.cancelPress=()=>{if(gesture&&gesture.card===card)endGesture(false)};
}
function renderHome(){
 if(drag||gesture)return;
 const known=channels.flatMap(c=>(c.motors||[]).map(m=>motorOrderKey(c.slot,m)));if(!customOrder)sortOrder=known;else for(const key of known)if(!sortOrder.includes(key))sortOrder.push(key);
 const signature=JSON.stringify(channels.map(c=>[c.slot,c.name,c.label,c.connected,c.connecting,c.connectionMessage]));
 if(channelSignature!==signature){
  channelSignature=signature;const selection=$('filter').value;$('filter').replaceChildren(new Option('所有通道','all'));$('channels').replaceChildren();
  for(const c of channels){$('filter').add(new Option('通道 '+c.slot,String(c.slot)));const b=document.createElement('button');b.className='channel'+(c.connected?' online':'');b.textContent='通道 '+c.slot+' · '+c.name;b.title=c.connectionMessage||c.label||'';const state=document.createElement('span');state.textContent=c.label;b.append(state);b.onclick=()=>callNative('openChannel',c.slot);$('channels').append(b)}
  $('filter').value=Array.from($('filter').options).some(o=>o.value===selection)?selection:'all';
 }
 const all=channels.reduce((n,c)=>n+(c.motors||[]).length,0);$('summary').textContent=channels.filter(c=>c.connected).length+' / '+channels.length+' 设备已连接 · '+all+' 台监控设备';$('addChannel').disabled=channels.length>=4;
 const wanted=new Set(),ordered=[];let count=0;
 for(const c of channels){if($('filter').value!=='all'&&Number($('filter').value)!==c.slot)continue;
  for(const m of c.motors||[]){const id=c.slot+'|'+m.key;wanted.add(id);count++;let card=cards.get(id);
   if(!card){card=document.createElement('article');card.className='motor';card.tabIndex=0;card.setAttribute('role','button');card.innerHTML='<div class="motor-heading"><span class="source"></span><span class="motor-addresses"><span class="motor-id"></span><span class="feedback-id"></span></span></div><h2></h2><p class="model"></p><span class="status"></span>';card.dataset.orderKey=motorOrderKey(c.slot,m);bindMotorCard(card,c.slot,m.key);cards.set(id,card);$('cards').append(card)}
   ordered.push(card);
   card.querySelector('.source').textContent='通道 '+c.slot+' · '+c.name;card.querySelector('h2').textContent=m.name;card.querySelector('.model').textContent=m.model;const badge=card.querySelector('.motor-id');badge.textContent='ID '+m.id;badge.setAttribute('aria-label','电机编号 '+m.id);badge.title='电机 ID';const feedback=card.querySelector('.feedback-id');feedback.textContent='RX '+(m.feedbackId??'—');feedback.title='反馈 CAN ID';feedback.setAttribute('aria-label','反馈 CAN ID '+(m.feedbackId??'未配置'));card.querySelector('.source').title='通道 '+c.slot+' · '+c.name;
   const snapshotAge=(c.age||0)+Date.now()-receivedAt,statusAge=(m.statusAge||0)+snapshotAge;
   const state=!c.connected?'disconnected':snapshotAge>=4000||statusAge>=3000?'stale':m.connectionStatus||'waiting';
   const status=card.querySelector('.status');status.textContent={disconnected:'● 蓝牙未连接',stale:'● 状态更新超时',online:'● 电机在线',offline:'● 电机反馈超时',waiting:'● 等待电机反馈'}[state]||'● 等待电机反馈';status.className='status'+(state==='online'?' live':state==='offline'||state==='stale'?' stale':'');
   if(m.family==='imu'){if(c.connected&&c.imuSupported===false){status.textContent='● 请更新 ESP32 固件至 1.17';status.className='status stale'}else status.textContent=status.textContent.replaceAll('电机','IMU');badge.title='IMU CAN_ID'}
   card.setAttribute('aria-label','通道 '+c.slot+' '+m.name+'，电机 ID '+m.id+'，反馈 CAN ID '+(m.feedbackId??'未配置')+'，'+status.textContent+'，点击查看详情，左滑删除，右滑配置，长按拖动排序');

  }
 }
 for(const [id,card] of cards)if(!wanted.has(id)){card.cancelPress();card.remove();cards.delete(id)}
 ordered.sort((a,b)=>sortOrder.indexOf(a.dataset.orderKey)-sortOrder.indexOf(b.dataset.orderKey));
 let cursor=$('cards').firstChild;for(const card of ordered){if(card!==cursor)$('cards').insertBefore(card,cursor);cursor=card.nextSibling}
 $('motorCount').textContent=count;$('empty').hidden=count!==0;
}
setInterval(renderHome,1000);

$('exportConfig').onclick=()=>callNative('exportConfig');$('importConfig').onclick=()=>callNative('importConfig');
window.configStatus=function(message,busy){$('configStatus').textContent=message;$('exportConfig').disabled=busy;$('importConfig').disabled=busy;homeConfigBusy=!!busy;updateConnectButton()};
window.validateImport=function(text){try{return {ok:true,data:ConfigIO.parse(text)}}catch(e){return {ok:false,error:e.message}}};
