/* Motor feedback decoders; mode metadata retained for old configuration files. Protocol references and model limits: MOTOR_PROTOCOLS.md. */
(function(root){
'use strict';
const profiles={
 c620:{name:'大疆 M3508 + C620',family:'dji',unit:'电流给定 · raw',limit:16384,maxId:8,groups:[0x200,0x1ff],feedbackBase:0x200},
 c610:{name:'大疆 M2006 + C610',family:'dji',unit:'电流给定 · raw',limit:10000,maxId:8,groups:[0x200,0x1ff],feedbackBase:0x200},
 gm6020:{name:'大疆 GM6020',family:'dji',unit:'电压给定 · raw',limit:30000,maxId:7,groups:[0x1ff,0x2ff],feedbackBase:0x204},
 gm3510:{name:'大疆 GM3510',family:'dji',maxId:3,feedbackBase:0x204,rawAngle:true},
 dm4310_48:{name:'达妙 DM4310 · 48V',family:'dm',limits:{p:12.5,v:50,t:10}},
 dm4340:{name:'达妙 DM4340',family:'dm',limits:{p:12.5,v:10,t:28}},
 dm4340_48:{name:'达妙 DM4340 · 48V',family:'dm',limits:{p:12.5,v:10,t:28}},
 dm6006:{name:'达妙 DM6006',family:'dm',limits:{p:12.5,v:45,t:20}},
 dm8006:{name:'达妙 DM8006',family:'dm',limits:{p:12.5,v:45,t:40}},
 dm8009:{name:'达妙 DM8009',family:'dm',limits:{p:12.5,v:45,t:54}},
 dm10010l:{name:'达妙 DM10010L',family:'dm',limits:{p:12.5,v:25,t:200}},
 dm10010:{name:'达妙 DM10010',family:'dm',limits:{p:12.5,v:20,t:200}},
 dmh3510:{name:'达妙 DM-H3510',family:'dm',limits:{p:12.5,v:280,t:1}},
 dmjh11:{name:'达妙 DM-JH11',family:'dm',limits:{p:12.5,v:10,t:12}},
 dm6248p:{name:'达妙 DM6248P',family:'dm',limits:{p:12.566,v:20,t:120}},
 dm3507:{name:'达妙 DM3507',family:'dm',limits:{p:12.566,v:50,t:5}},
 dm4310:{name:'达妙 DM4310 · 经典 CAN',family:'dm',limits:{p:12.5,v:30,t:10}},
 dm_custom:{name:'达妙其他型号 · 自定义量程',family:'dm',limits:null},
 dm_imu_l1:{name:'达妙 DM-IMU-L1',family:'imu'}
};
const modes={mit:{name:'MIT 混合控制',offset:0,code:1},posvel:{name:'位置速度',offset:0x100,code:2},velocity:{name:'速度',offset:0x200,code:3},forcepos:{name:'力位混合',offset:0x300,code:4}};
function number(value,name,min,max,integer=false){if(value===null||value===undefined||String(value).trim()==='')throw Error(name+'不能为空');const n=Number(value);if(!Number.isFinite(n)||n<min||n>max||(integer&&!Number.isInteger(n)))throw Error(name+'范围为 '+min+' ～ '+max+(integer?'（整数）':''));return n}
function hexId(text,max=0x7ff){if(!/^(?:0x)?[0-9a-fA-F]{1,3}$/.test(String(text).trim()))throw Error('CAN ID 应为十六进制数');return number(parseInt(String(text).replace(/^0x/i,''),16),'CAN ID',1,max,true)}
function dmId(id,mode){if(!modes[mode])throw Error('未知达妙模式');number(id,'电机 CAN ID',1,0x7fe,true);const target=id+modes[mode].offset;if(target>0x7fe)throw Error('电机 ID 加模式偏移必须小于 0x7FF（保留参数地址）');return target}
function limits(l){return{p:number(l.p,'P_MAX',0.000001,1e6),v:number(l.v,'V_MAX',0.000001,1e6),t:number(l.t,'T_MAX',0.000001,1e6)}}
function djiFeedback(profile,motorId,id,data,feedbackId){const p=profiles[profile];if(!p||p.family!=='dji'||id!==(feedbackId===undefined?p.feedbackBase+motorId:feedbackId)||data.length<7)return null;const signed=(a,b)=>{const n=(a<<8)|b;return n>=32768?n-65536:n};if(p.rawAngle){const angle=(data[0]<<8)|data[1],effort=signed(data[2],data[3]);return{values:{position:angle,speed:null,effort,temperature:null},angle:angle+' raw',speed:'—',effort:effort+' raw',temperature:'—',state:'收到反馈'}}return{values:{position:((data[0]<<8)|data[1])*360/8192,speed:signed(data[2],data[3]),effort:signed(data[4],data[5]),temperature:profile==='c610'?null:data[6]},angle:(((data[0]<<8)|data[1])*360/8192).toFixed(1)+'°',speed:signed(data[2],data[3])+' rpm',effort:signed(data[4],data[5])+' raw',temperature:profile==='c610'?'—':data[6]+' °C',state:'收到反馈'}}
function dmFeedback(motorId,masterId,id,data,l){if(id!==masterId||data.length<8)return null;if((data[0]|data[1]<<8)===motorId&&[0x33,0x55].includes(data[2])&&data[3]===0x0a)return{mode:(data[4]|data[5]<<8|data[6]<<16|data[7]<<24)>>>0};if((data[0]&15)!==(motorId&15))return null;const range=limits(l),map=(x,max,bits)=>x*2*max/((2**bits)-1)-max;const states={0:'失能',1:'使能',8:'过压',9:'欠压',10:'过流',11:'MOS 过温',12:'线圈过温',13:'通信丢失',14:'过载'};return{values:{position:map(data[1]<<8|data[2],range.p,16),speed:map(data[3]<<4|data[4]>>4,range.v,12),effort:map((data[4]&15)<<8|data[5],range.t,12),mosTemperature:data[6],temperature:data[7]},angle:map(data[1]<<8|data[2],range.p,16).toFixed(3)+' rad',speed:map(data[3]<<4|data[4]>>4,range.v,12).toFixed(3)+' rad/s',effort:map((data[4]&15)<<8|data[5],range.t,12).toFixed(3)+' N·m',temperature:data[6]+' / '+data[7]+' °C',state:states[data[0]>>4]||'未知状态'}}
// DM-IMU-L1 V1.2 data-frame table. Quaternion W includes bit 2 of byte 2.
function imuFeedback(feedbackId,id,data){
 if(id!==feedbackId||data.length!==8||!data.every(v=>Number.isInteger(v)&&v>=0&&v<=255)||data[0]<1||data[0]>4)return null;
 const map=(v,max,bits=16)=>v*2*max/(2**bits-1)-max;
 const u16=o=>data[o]|data[o+1]<<8,values={};
 if(data[0]===1){values.accelX=map(u16(2),235.2);values.accelY=map(u16(4),235.2);values.accelZ=map(u16(6),235.2);values.temperature=data[1]}
 if(data[0]===2){values.gyroX=map(u16(2),34.88);values.gyroY=map(u16(4),34.88);values.gyroZ=map(u16(6),34.88)}
 if(data[0]===3){values.pitch=map(u16(2),90);values.yaw=map(u16(4),180);values.roll=map(u16(6),180)}
 if(data[0]===4){values.qw=map(data[1]<<6|data[2]>>2,1,14);values.qx=map((data[2]&3)<<12|data[3]<<4|data[4]>>4,1,14);values.qy=map((data[4]&15)<<10|data[5]<<2|data[6]>>6,1,14);values.qz=map((data[6]&63)<<8|data[7],1,14)}
 return {values,state:'收到 IMU 反馈',group:data[0]};
}
function imuSeries(){return [
 ['横滚 Roll','roll','°'],['俯仰 Pitch','pitch','°'],['航向 Yaw','yaw','°'],
 ['加速度 X','accelX','m/s²'],['加速度 Y','accelY','m/s²'],['加速度 Z','accelZ','m/s²'],
 ['角速度 X','gyroX','rad/s'],['角速度 Y','gyroY','rad/s'],['角速度 Z','gyroZ','rad/s'],
 ['四元数 W','qw',''],['四元数 X','qx',''],['四元数 Y','qy',''],['四元数 Z','qz',''],['温度','temperature','°C']
 ].map(([label,key,unit])=>({label,key,unit,imu:true}))}
const api={imuFeedback,imuSeries,profiles,modes,number,hexId,djiFeedback,dmFeedback,dmId};root.Motors=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
