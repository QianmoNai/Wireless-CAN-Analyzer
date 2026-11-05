const assert=require('node:assert/strict');
const {RingBuffer,FrameHistory}=require('../android/app/src/main/assets/history.js');
const ring=new RingBuffer(3);for(let i=0;i<10;i++)ring.push(i);
assert.deepEqual(ring.snapshot(),[9,8,7]);assert.equal(ring.at(3),undefined);
const frozen=ring.snapshot();ring.push(10);assert.deepEqual(frozen,[9,8,7]);
ring.resize(2);assert.deepEqual(ring.snapshot(),[10,9]);ring.resize(5);ring.push(11);assert.deepEqual(ring.snapshot(),[11,10,9]);
ring.resize(0);ring.push(42);assert.equal(ring.length,0);
const history=new FrameHistory(2,3);history.replace([{kind:1,id:10},{kind:2,id:11},{kind:1,id:12},{kind:3,id:13},{kind:1,id:14},{kind:2,id:15},{kind:2,id:16}]);
assert.deepEqual(history.snapshot().map(f=>f.id),[10,11,12,13,15]);
const snapshot=history.snapshot();history.push({kind:1,id:99});assert.deepEqual(history.snapshot().map(f=>f.id),[99,10,11,13,15]);assert.deepEqual(snapshot.map(f=>f.id),[10,11,12,13,15]);
history.resize(0,1);assert.deepEqual(history.snapshot().map(f=>f.id),[11]);history.clear();assert.equal(history.snapshot().length,0);
assert.deepEqual(snapshot.map(f=>f.id),[10,11,12,13,15]);assert.deepEqual(frozen,[9,8,7]);
// Long-running wrapping retains the newest data and RX/TX limits independently.
history.resize(10000,300);for(let i=0;i<50000;i++)history.push({kind:i%2?1:3,id:i});
const records=history.snapshot();assert.equal(records.filter(f=>f.kind===1).length,10000);assert.equal(records.filter(f=>f.kind===3).length,300);
assert.equal(records[0].id,49999);assert(records.every((f,i)=>i===0||f.order<records[i-1].order));
console.log('PASS: ring wrapping, resize/zero limits, immutable paused snapshots, interleaved RX/TX/failure order and long-running retention.');
