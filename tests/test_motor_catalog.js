const assert=require('node:assert/strict');require('../android/app/src/main/assets/motors.js');const io=require('../android/app/src/main/assets/config_io.js');
for(const [id,p] of Object.entries(Motors.profiles)){
 if(p.family!=='dm'||!p.limits)continue;
 const c={slot:1,name:'test',address:'',historyLimits:{rx:1,tx:1},motors:[{profile:id,id:1,feedbackId:0x11,master:0x11,mode:'mit',limits:p.limits,pollHz:100}]};assert.equal(io.channel(c).motors[0].profile,id);
 const f=Motors.dmFeedback(1,17,17,[1,255,255,255,255,255,30,31],p.limits);assert.equal(f.values.position,p.limits.p);assert.equal(f.values.speed,p.limits.v);assert.equal(f.values.effort,p.limits.t);
}
const f=Motors.djiFeedback('gm3510',1,0x205,[0x10,0,0xff,0xfe,0x11,0x22,0x33,0x44]);assert.equal(f.values.position,4096);assert.equal(f.values.effort,-2);assert.equal(f.values.speed,null);assert.equal(f.values.temperature,null);
assert.equal(Motors.djiFeedback('gm3510',1,0x201,[0,0,0,0,0,0,0,0]),null);
const c={slot:1,name:'test',address:'',historyLimits:{rx:1,tx:1},motors:[{profile:'gm3510',id:4,feedbackId:0x208}]};assert.throws(()=>io.channel(c));c.motors[0].id=1;c.motors.push({profile:'gm6020',id:1,feedbackId:0x301});assert.throws(()=>io.channel(c));
console.log('PASS: motor catalog limits, config round trips, GM3510 distinct feedback, ID range and conflicts');
