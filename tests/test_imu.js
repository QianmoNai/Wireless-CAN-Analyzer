const assert=require('node:assert/strict');require('../android/app/src/main/assets/motors.js');const io=require('../android/app/src/main/assets/config_io.js');
const decode=d=>Motors.imuFeedback(0x11,0x11,d);
let f=decode([1,35,0,0,255,255,0,128]);assert.equal(f.values.accelX,-235.2);assert.equal(f.values.accelY,235.2);assert.equal(f.values.temperature,35);assert(Math.abs(f.values.accelZ)<.004);
f=decode([2,0,0,0,255,255,0,128]);assert.equal(f.values.gyroX,-34.88);assert.equal(f.values.gyroY,34.88);
f=decode([3,0,0,0,255,255,0,128]);assert.equal(f.values.pitch,-90);assert.equal(f.values.yaw,180);assert(Math.abs(f.values.roll)<.003);
for(const q of [[0,0,0,0],[16383,16383,16383,16383],[1,2,3,4],[8192,8191,25,16300]]){const [w,x,y,z]=q,d=[4,w>>6,((w&63)<<2)|(x>>12),(x>>4)&255,((x&15)<<4)|(y>>10),(y>>2)&255,((y&3)<<6)|(z>>8),z&255];f=decode(d);['qw','qx','qy','qz'].forEach((k,i)=>assert(Math.abs(f.values[k]-(q[i]*2/16383-1))<1e-12));}
assert.equal(decode([0xcc,1,0,0,0,0,0,0]),null);assert.equal(decode([1,0]),null);assert.equal(Motors.imuFeedback(0x12,0x11,[1,0,0,0,0,0,0,0]),null);
const config={format:'can-scope-config',version:1,channels:[{slot:1,name:'test',address:'',historyLimits:{rx:300,tx:300},motors:[{profile:'dm_imu_l1',name:'IMU',id:1,feedbackId:17}]}]};assert.equal(io.parse(JSON.stringify(config)).channels[0].motors[0].profile,'dm_imu_l1');config.channels[0].motors.push({profile:'c620',id:1,feedbackId:17});assert.throws(()=>io.backup(config));
console.log('PASS: IMU LE mapping, quaternion bit boundaries, invalid frames and configuration round trip/conflicts');
