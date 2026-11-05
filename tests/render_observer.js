// Opt-in bench instrumentation. Timings cover callbacks, not display frames.
(()=>{
 'use strict';
 if(window.canRenderObserver)throw new Error('render observer already installed');
 let phase=null,previous=null,raf=0;
 const originals=new Map();
 function record(kind,ms){
  if(!phase||!Number.isFinite(ms)||ms<0)return;
  const s=phase.metrics[kind]||(phase.metrics[kind]={count:0,totalMs:0,maxMs:0,bins:new Map()});
  s.count++;s.totalMs+=ms;s.maxMs=Math.max(s.maxMs,ms);
  // Bounded histogram: 1/16 ms bins, with a final >=1000 ms bin.
  const bin=Math.min(16000,Math.floor(ms*16));s.bins.set(bin,(s.bins.get(bin)||0)+1);
 }
 for(const name of ['drawDetail','renderFrames','renderDetailStream','onNative']){
  const original=window[name];if(typeof original!=='function')continue;
  const wrapped=function(...args){const start=performance.now();try{return original.apply(this,args)}finally{record(name,performance.now()-start)}};
  originals.set(name,{original,wrapped});window[name]=wrapped;
 }
 function frame(now){
  if(phase&&!document.hidden){if(previous!==null)record('rafInterval',now-previous);previous=now}else previous=null;
  raf=requestAnimationFrame(frame);
 }
 const longTask=typeof PerformanceObserver==='function'?new PerformanceObserver(list=>{
  for(const entry of list.getEntries())if(phase&&entry.startTime>=phase.started)record('longTask',entry.duration);
 }):null;
 if(longTask&&PerformanceObserver.supportedEntryTypes.includes('longtask'))longTask.observe({type:'longtask'});
 raf=requestAnimationFrame(frame);
 window.canRenderObserver={
  reset(){previous=null;phase={started:performance.now(),metrics:{}}},
  snapshot(){
   if(!phase)return null;
   const metrics={};
   for(const [kind,s]of Object.entries(phase.metrics)){
    let cumulative=0,p95UpperMs=null;
    for(const [bin,count]of [...s.bins].sort((a,b)=>a[0]-b[0])){
     cumulative+=count;if(cumulative>=Math.ceil(s.count*.95)){p95UpperMs=bin===16000?null:(bin+1)/16;break}
    }
    metrics[kind]={count:s.count,totalMs:s.totalMs,meanMs:s.totalMs/s.count,maxMs:s.maxMs,p95UpperMs};
   }
   return {elapsedMs:performance.now()-phase.started,hidden:document.hidden,metrics};
  },
  stop(){
   cancelAnimationFrame(raf);if(longTask)longTask.disconnect();phase=null;
   for(const [name,{original,wrapped}]of originals)if(window[name]===wrapped)window[name]=original;
   delete window.canRenderObserver;
  }
 };
})();
