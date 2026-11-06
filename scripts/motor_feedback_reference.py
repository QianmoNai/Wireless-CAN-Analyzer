"""Known feedback vectors, compared after physical STM32 CAN injection.

The Python reference never calls the product JavaScript decoder. These are
simulated feedback frames, not evidence from actual motors or an actual IMU.
"""
import math
import struct
import time


def vectors():
    result=[]
    for index,profile in enumerate(['c620','c610','gm6020','gm3510']):
        config=dict(profile=profile,id=1+index%2,feedbackId=0x600+index,name='Physical '+profile)
        cases=[]
        for angle,speed,effort,temperature in [(0,-32768,32767,0),(4096,-2,-1,85),(8191,32767,-32768,255)]:
            payload=struct.pack('>HhhBB',angle,speed,effort,temperature,0)
            values=dict(position=angle if profile=='gm3510' else angle*360/8192,
                        speed=None if profile=='gm3510' else speed,
                        effort=speed if profile=='gm3510' else effort,
                        temperature=None if profile in ('c610','gm3510') else temperature)
            cases.append((payload,values))
        result.append((config,cases))
    limits=[('dm4310_48',12.5,50,10),('dm4340',12.5,10,28),('dm4340_48',12.5,10,28),
            ('dm6006',12.5,45,20),('dm8006',12.5,45,40),('dm8009',12.5,45,54),
            ('dm10010l',12.5,25,200),('dm10010',12.5,20,200),('dmh3510',12.5,280,1),
            ('dmjh11',12.5,10,12),('dm6248p',12.566,20,120),('dm3507',12.566,50,5),
            ('dm4310',12.5,30,10),('dm_custom',9.75,17,3.25)]
    for index,(profile,p,v,t) in enumerate(limits):
        motor=index+1
        config=dict(profile=profile,id=motor,feedbackId=0x610+index,master=0x610+index,
                    mode='mit',pollHz=100,limits=dict(p=p,v=v,t=t),name='Physical '+profile)
        cases=[]
        for position,speed,effort,mos,temperature in [(0,0,0,0,255),(32768,1024,3071,44,55),(65535,4095,4095,255,0)]:
            payload=bytes([0x10|motor,position>>8,position&255,speed>>4,(speed&15)<<4|effort>>8,effort&255,mos,temperature])
            values=dict(position=-p+position*(2*p)/65535,speed=-v+speed*(2*v)/4095,
                        effort=-t+effort*(2*t)/4095,mosTemperature=mos,temperature=temperature)
            cases.append((payload,values))
        result.append((config,cases))
    config=dict(profile='dm_imu_l1',id=0x50,feedbackId=0x680,name='Physical IMU')
    cases=[]
    for group,keys,scales in [(1,['accelX','accelY','accelZ'],[235.2]*3),
                              (2,['gyroX','gyroY','gyroZ'],[34.88]*3),
                              (3,['pitch','yaw','roll'],[90,180,180])]:
        codes=[0,32768,65535]
        payload=bytes([group,73])+struct.pack('<HHH',*codes)
        values={key:-scale+code*(2*scale)/65535 for key,scale,code in zip(keys,scales,codes)}
        if group==1:values['temperature']=73
        cases.append((payload,values))
    # Distinct 14-bit quaternion values exercise every shared-byte boundary.
    w,x,y,z=0x0123,0x2345,0x3456,0x3fed
    payload=bytes([4,w>>6,(w&63)<<2|x>>12,(x>>4)&255,(x&15)<<4|y>>10,(y>>2)&255,(y&3)<<6|z>>8,z&255])
    cases.append((payload,{key:-1+code*2/16383 for key,code in zip(['qw','qx','qy','qz'],[w,x,y,z])}))
    result.append((config,cases))
    return result


def inject(fixture,bus,identifier,payload,flags=0):
    base=fixture.symbols['can_bench']+bus*52
    request=fixture.next_request(base+16,base+20)
    frame=struct.pack('<IBB8sBB',identifier,flags&1,len(payload),payload,int(bool(flags&2)),0)
    words=list(struct.unpack('<IIII',frame));fixture.write(base,words)
    if fixture.read(base,4)!=words:raise AssertionError('SWD feedback frame mismatch')
    fixture.write(base+16,[request]);deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        done,result=fixture.read(base+20,2)
        if done==request:
            if result:raise AssertionError('Fixture CAN feedback enqueue failed')
            return
        time.sleep(.005)
    raise TimeoutError('Fixture feedback submission missing')


def compare(actual,expected):
    for key,value in expected.items():
        observed=actual.get(key)
        if key not in actual:raise AssertionError(f'Missing feedback field {key}')
        if value is None:
            if observed is not None:raise AssertionError(f'{key}: expected unavailable, got {observed}')
        elif not isinstance(observed,(float,int)) or not math.isclose(observed,value,rel_tol=1e-10,abs_tol=1e-9):
            raise AssertionError(f'{key}: expected {value}, got {observed}')


def compare_display(config,expected,display):
    labels={item['key']:item['text'] for item in display}
    for key,value in expected.items():
        if key=='mosTemperature' or key not in labels:continue
        text=labels[key]
        if value is None:
            if text!='—':raise AssertionError(f'Unavailable {key} displayed as {text}')
            continue
        if config['profile'].startswith('dm') and config['profile']!='dm_imu_l1' and key=='temperature':
            wanted=f"{value} / {expected['mosTemperature']} °C"
            if text!=wanted:raise AssertionError(f'Temperature display {text} != {wanted}')
        else:
            numeric=text.replace('°','').split()[0]
            try:shown=float(numeric)
            except ValueError:raise AssertionError(f'Invalid parameter display {key}: {text}')
            # Display rounds position to 0.1 deg, DM to 0.001, IMU to
            # 0.0001. Verify the visible number within its own rounding unit.
            tolerance=.000050001
            if config['profile'] in ('c620','c610','gm6020') and key=='position':
                tolerance=.050000001
            elif config['profile'].startswith('dm') and config['profile']!='dm_imu_l1' and key in ('position','speed','effort'):
                tolerance=.000500001
            if not math.isclose(shown,value,rel_tol=0,abs_tol=tolerance):
                raise AssertionError(f'Displayed {key}: expected {value}, got {text}')


def exercise(page,fixture,bus,save,invalid_frames=False):
    cases=vectors();backup=page.evaluate('watchedMotors.map(m=>m.config)')
    catalog=page.evaluate('Object.fromEntries(Object.entries(Motors.profiles).map(([key,p])=>[key,p.limits||null]))')
    if set(catalog)!={config['profile']for config,_ in cases}:
        raise AssertionError('Feedback vectors do not cover the product profile catalog')
    for config,_ in cases:
        if config['profile'].startswith('dm')and config['profile']not in ('dm_custom','dm_imu_l1'):
            if catalog[config['profile']]!=config['limits']:
                raise AssertionError('Preset limits differ from reference for '+config['profile'])
    rows=[]
    try:
        page.evaluate("configs=>{closeMotorDetail();watchedMotors.splice(0);for(const c of configs)commitWatch(c);publishDashboard();showTab('live');window.feedbackMode=-1;window.feedbackRaw=[];const old=onNative;window.feedbackOriginalNative=old;onNative=(k,d)=>{old(k,d);if(k==='telemetryMode')feedbackMode=d.mode;if(k==='packets')for(const item of d.items||[]){const p=parsePacket(item.hex||item);if(p&&p[1]===1)feedbackRaw.push({id:u32(p,4),flags:p[2]&3,dlc:p[3]})}}}",[c for c,_ in cases])
        # Native may already have been in raw mode; request a real quiet/raw
        # transition so readiness is observed without fabricating any packets.
        page.evaluate("showTab('motor')");page.wait_for_function('feedbackMode===1',polling=100,timeout=10000)
        page.evaluate("showTab('live')");page.wait_for_function('feedbackMode===0',polling=100,timeout=10000)
        for index,(config,samples) in enumerate(cases):
            row=dict(profile=config['profile'],feedback_id=config['feedbackId'],raw=[],detail=[]);rows.append(row);save(rows)
            for payload,expected in samples:
                before=page.evaluate('(i)=>watchedMotors[i].time||0',index)
                inject(fixture,bus,config['feedbackId'],payload)
                page.wait_for_function('({i,before})=>watchedMotors[i].time>before',arg=dict(i=index,before=before),polling=50,timeout=5000)
                observed=page.evaluate('(i)=>watchedMotors[i].feedback.values',index);compare(observed,expected)
                row['raw'].append(dict(payload=payload.hex(),expected=expected,observed=observed));save(rows)
        if invalid_frames:
            baseline=page.evaluate('watchedMotors.map(m=>({time:m.time,feedback:m.feedback}))')
            for index,(config,samples) in enumerate(cases):
                payload=samples[1][0];identifier=config['feedbackId']
                invalid=[('extended',identifier,payload,1),('remote',identifier,payload,2),
                         ('short',identifier,payload[:6],0),('wrong-address',0x6f0,payload,0)]
                if config['profile']=='dm_imu_l1':
                    invalid.append(('unknown-group',identifier,bytes([5])+payload[1:],0))
                elif config['profile'].startswith('dm'):
                    invalid.append(('wrong-motor',identifier,bytes([payload[0]^1])+payload[1:],0))
                rows[index]['invalid_raw']=[]
                for kind,identifier,data,flags in invalid:
                    before=page.evaluate('feedbackRaw.length')
                    inject(fixture,bus,identifier,data,flags)
                    page.wait_for_function('({before,id,flags,dlc})=>feedbackRaw.slice(before).some(p=>p.id===id&&p.flags===flags&&p.dlc===dlc)',
                                           arg=dict(before=before,id=identifier,flags=flags,dlc=len(data)),polling=50,timeout=5000)
                    if page.evaluate('watchedMotors.map(m=>({time:m.time,feedback:m.feedback}))')!=baseline:
                        raise AssertionError(f'Invalid {kind} feedback changed a motor value or receipt time')
                    rows[index]['invalid_raw'].append(dict(kind=kind,id=identifier,flags=flags,dlc=len(data),
                                                          physically_received=True,all_motor_feedback_unchanged=True))
                    save(rows)
        for index,(config,samples) in enumerate(cases):
            page.evaluate('(i)=>dashboardOpen(JSON.stringify(watchedMotors[i].config))',index)
            page.wait_for_function('detailReady',polling=100,timeout=10000)
            for payload,expected in samples:
                before=page.evaluate('detailMotor.time||0')
                inject(fixture,bus,config['feedbackId'],payload)
                page.wait_for_function('(before)=>detailMotor.time>before',arg=before,polling=50,timeout=5000)
                observed=page.evaluate('detailMotor.feedback.values');compare(observed,expected)
                page.wait_for_timeout(100)
                display=page.evaluate('detailSeries.map(s=>({key:s.key,text:s.card.querySelector(".detail-number").textContent}))')
                if not display or all(d['text']=='—' for d in display):raise AssertionError('All detail parameter displays unavailable')
                compare_display(config,expected,display)
                rows[index]['detail'].append(dict(payload=payload.hex(),expected=expected,observed=observed,display=display));save(rows)
            page.evaluate("closeMotorDetail(true,'live')");page.wait_for_function('feedbackMode===0',polling=100,timeout=10000)
        return rows
    finally:
        page.evaluate("configs=>{closeMotorDetail();if(window.feedbackOriginalNative)onNative=feedbackOriginalNative;watchedMotors.splice(0);for(const c of configs)commitWatch(c);publishDashboard();showTab('motor')}",backup)
