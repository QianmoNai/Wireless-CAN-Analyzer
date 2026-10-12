/* Bounded histories. Insertion never shifts or filters the retained records. */
(function(root){
'use strict';
class RingBuffer{
 constructor(capacity=0){this.capacity=capacity;this.items=new Array(capacity);this.head=0;this.length=0;this.version=0;this.cachedVersion=-1;this.cached=[]}
 push(value){if(!this.capacity)return;this.items[this.head]=value;this.head=(this.head+1)%this.capacity;this.length=Math.min(this.length+1,this.capacity);this.version++}
 at(index){return index>=0&&index<this.length?this.items[(this.head-1-index+this.capacity)%this.capacity]:undefined}
 snapshot(){if(this.cachedVersion!==this.version){this.cached=Array.from({length:this.length},(_,i)=>this.at(i));this.cachedVersion=this.version}return this.cached}
 clear(){this.items=new Array(this.capacity);this.head=0;this.length=0;this.version++}
 resize(capacity){if(capacity===this.capacity)return;const keep=this.snapshot().slice(0,capacity);this.capacity=capacity;this.clear();for(let i=keep.length-1;i>=0;i--)this.push(keep[i])}
 replace(records){this.clear();for(let i=Math.min(records.length,this.capacity)-1;i>=0;i--)this.push(records[i])}
}
class FrameHistory{
 constructor(rx=300,tx=300){this.rx=new RingBuffer(rx);this.tx=new RingBuffer(tx);this.sequence=0;this.version=-1;this.cached=[]}
 push(frame){const entry={...frame,order:++this.sequence};(frame.kind===1?this.rx:this.tx).push(entry)}
 resize(rx,tx){this.rx.resize(rx);this.tx.resize(tx)}
 clear(){this.rx.clear();this.tx.clear()}
 replace(records){this.clear();for(let i=records.length-1;i>=0;i--)this.push(records[i])}
 snapshot(){
  const version=this.rx.version+this.tx.version;if(version===this.version)return this.cached;
  const out=[];let i=0,j=0;while(i<this.rx.length||j<this.tx.length){const r=this.rx.at(i),t=this.tx.at(j);if(!t||(r&&r.order>t.order)){out.push(r);i++}else{out.push(t);j++}}
  this.cached=out;this.version=version;return out;
 }
}
root.RingBuffer=RingBuffer;root.FrameHistory=FrameHistory;if(typeof module!=='undefined')module.exports={RingBuffer,FrameHistory};
})(typeof window==='undefined'?globalThis:window);
