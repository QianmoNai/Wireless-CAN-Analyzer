#!/usr/bin/env python3
"""Opt-in physical CAN load qualification; source and delivered frames are checked independently."""
import argparse,asyncio,importlib.util,json,pathlib,secrets,struct,sys,time
root=pathlib.Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'scripts'),str(root/'desktop')]
from can_scope.connection_store import connection_folder,load_connection
from can_scope.transports import BleTransport,LanTransport,discover
from can_scope.usb_transport import UsbTransport,prepare_usb
from can_scope.protocol import packet,u32
from usb_management import LocalUsb


def main():
    p=argparse.ArgumentParser(description='Physical CAN load through a production desktop transport; requires the microsecond STM32 fixture, saved trust and exclusive bench access. Does not flash firmware.');p.add_argument('--port',required=True);p.add_argument('--channel',type=int,choices=range(1,5),default=1);p.add_argument('--bus',type=int,choices=[1,2],default=1);p.add_argument('--stop-file',type=pathlib.Path);p.add_argument('--probe-control',action='store_true',help='Measure acknowledged stop-all on an already stopped query owner during load; no motion commands');p.add_argument('--mode',choices=['usb','ble','wifi'],required=True);p.add_argument('--elf',type=pathlib.Path,required=True);p.add_argument('--rates',type=int,nargs='+',default=[500,1000,2000,4000,6000,7500]);p.add_argument('--seconds',type=int,default=8);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args()
    if not 1<=a.seconds<=30 or not all(1<=n<=10000 and n*a.seconds<=300000 for n in a.rates):p.error('Seconds 1..30, rate 1..10000, at most300000 frames per phase')
    if not a.elf.is_file():p.error('Fixture ELF not found')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    def module(name,f):
     s=importlib.util.spec_from_file_location(name,root/'scripts'/f);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
    hw=module('native_load_fixture','test-dual-can-hardware.py');bench=module('native_load_mode','test-can-hardware.py')
    saved=load_connection(connection_folder()/f'connection-{a.channel}.json');port=a.port;stop=a.stop_file
    from hardware_bench_lock import hardware_bench_lock
    names='tx_queued tx_busy tx_errors skipped_slots tx_done_at rx_unique rx_duplicates rx_out_of_order rx_gap_events rx_bad rx_stale rx_last_sequence rx_first_at rx_last_at'.split()
    r=dict(ok=False,mode=a.mode,scope='Production desktop transport with physical STM32 generated CAN; no GUI/Android/FPS proof. Source requested rate differs from actual rate. Bus percentage excludes bit stuffing, errors and failed attempts.',phases=[],errors=[])
    def save():a.output.write_text(json.dumps(r,indent=2)+'\n')
    def monitor():
     u=LocalUsb(port)
     try:assert u.identity().hex()==saved['id'];return u.monitor_drops()
     finally:u.close()
    f=None;base=None

    def snap():
     before=time.monotonic();w=f.read(base,40)
     return dict(command=w[0],rate=w[1],frames=w[2],nonce=w[3],request=w[4],done=w[5],result=w[6],active=w[7],started_at=w[8],finished_at=w[9],bus=[dict(zip(names,w[10+i*14:24+i*14]))for i in range(2)],clock_hz=w[38],bus_mask=w[39],host_before_s=before,host_after_s=time.monotonic())
    def command(op,rate=0,count=0,nonce=0):
     req=f.next_request(base+16,base+20);config=[op,rate,count,nonce]if op else[0,*f.read(base+4,3)];f.write(base,config);assert f.read(base,4)==config;f.write(base+16,[req]);deadline=time.monotonic()+2
     while time.monotonic()<deadline:
      v=f.read(base+20,2)
      if v[0]==req:
       if v[1]:raise RuntimeError('Source request rejected '+str(v[1]))
       return
      time.sleep(.002)
     raise TimeoutError('Source outcome unknown; refusing overwrite')
    async def run():
     stats=[];loads=[];current=None;query_ack=asyncio.Event();control_pending={}
     def received(data):
      nonlocal current
      if data[1]==4:stats.append(dict(host_s=time.monotonic(),rx=u32(data,4),tx=u32(data,8),failures=u32(data,12),drops=u32(data,16),state=data[2]));return
      if data[1]==13:
       loads.append(dict(host_s=time.monotonic(),hex=data.hex()));return
      if data[1]==8:
       token=u32(data,16)
       if token==0x62656e63 and data[2]==1:query_ack.set()
       future=control_pending.get(token)
       if future is not None and not future.done():future.set_result(data[2])
       return
      if not current or data[1]!=1:return
      if u32(data,4)!=(0x6a0 if a.bus==1 else 0x6b0):return
      d=current
      if data[2]&3 or data[3]!=8:d['bad']+=1;return
      nonce,seq=struct.unpack_from('<II',data,8)
      if nonce!=d['nonce']:d['stale']+=1;return
      if seq>=d['frames']:d['bad']+=1;return
      if d['bitmap'][seq>>3]&(1<<(seq&7)):d['duplicates']+=1;return
      d['bitmap'][seq>>3]|=1<<(seq&7);d['unique']+=1
      if seq<d['next']:d['out_of_order']+=1
      else:
       if seq>d['next']:d['gap_events']+=1
       d['next']=seq+1
      now=time.monotonic()
      if d['first_s']is None:d['first_s']=now
      d['last_s']=now
     link={'usb':UsbTransport,'ble':BleTransport,'wifi':LanTransport}[a.mode](received,lambda _:r['errors'].append('disconnect'))
     try:
      await bench.select(port,a.mode)
      if a.mode=='usb':await link.connect(port)
      elif a.mode=='ble':await link.connect(saved['address'])
      else:
       await asyncio.sleep(10);found=[];await discover(saved['id'],found.append);assert len(found)==1;saved['ip']=found[0]['address'].removeprefix('wifi:');await bench.connect_wifi(link,saved)
      await link.write(packet(0x15,token=0x62656e63));await asyncio.wait_for(query_ack.wait(),3)
      await asyncio.sleep(2);assert stats and not stats[-1]['state']
      for rate in a.rates:
       if stop and stop.exists():raise InterruptedError('User stop')
       count=rate*a.seconds;nonce=secrets.randbelow(0xffffffff)+1
       current=dict(nonce=nonce,frames=count,bitmap=bytearray((count+7)//8),unique=0,duplicates=0,out_of_order=0,gap_events=0,bad=0,stale=0,next=0,first_s=None,last_s=None)
       phase=dict(rate_requested_hz=rate,frames_requested=count,nonce=nonce,before=stats[-1],ticks=[],control_probe_ms=[]);r['phases'].append(phase);save()
       command(a.bus+1,rate,count,nonce)
       configured=snap()
       if configured['clock_hz']!=1000000 or configured['bus_mask']!=1<<(a.bus-1):raise RuntimeError('Source clock or selected bus mismatch')
       deadline=time.monotonic()+a.seconds*1.6+4;next_probe=time.monotonic()+1
       try:
        while True:
         if a.probe_control and time.monotonic()>=next_probe:
          token=secrets.randbelow(0xffffffff)+1
          while token==0x62656e63 or token in control_pending:token=secrets.randbelow(0xffffffff)+1
          future=asyncio.get_running_loop().create_future();control_pending[token]=future;began=time.monotonic()
          try:
           await link.write(packet(0x15,token=token));result=await asyncio.wait_for(future,2)
           if result!=1:raise RuntimeError('Stopped query owner did not acknowledge stop-all')
           phase['control_probe_ms'].append((time.monotonic()-began)*1000)
          finally:control_pending.pop(token,None)
          next_probe=time.monotonic()+2
         v=snap();phase['ticks'].append(dict(source=v,received=current['unique']));save()
         if v['result']:raise RuntimeError('Source failure '+str(v['result']))
         if stop and stop.exists():raise InterruptedError('User stop')
         if r['errors']:raise ConnectionError('Transport disconnected')
         if v['active']==2:break
         if time.monotonic()>deadline:raise TimeoutError('Source did not finish at requested rate')
         await asyncio.sleep(.25)
        # Keep the source stopped and transport receiving while queues settle.
        phase['source']=v;await asyncio.sleep(2)
       finally:
        command(0);phase['frozen']=snap()
       phase['received']={k:v for k,v in current.items()if k!='bitmap'};phase['after']=stats[-1];phase['status_delta']={k:(phase['after'][k]-phase['before'][k])&0xffffffff for k in ['rx','tx','failures','drops']};phase['health']=f.health()
       src=phase['source'];b=src['bus'][a.bus-1];elapsed=(b['tx_done_at']-src['started_at'])&0xffffffff;actual=(b['tx_queued']-1)*1000000/elapsed if elapsed else None
       phase['actual_source_hz']=actual;phase['estimated_source_bus_percent_without_stuffing']=actual*111/10000 if actual else None
       phase['source_rate_qualified']=bool(actual and actual>=rate*.98 and b['tx_queued']==count and not b['tx_errors'])
       phase['source_fifo_free']=f.read(f.symbols['can_bench_tx_free'],2);phase['source_fifo_drained']=phase['source_fifo_free'][a.bus-1]==8
       phase['all_frames_received']=current['unique']==count and not any(current[k]for k in ['duplicates','out_of_order','bad','stale'])
       phase['no_new_reported_drops']=phase['status_delta']['drops']==0
       phase['ok']=phase['source_fifo_drained']and phase['source_rate_qualified']and phase['all_frames_received']and phase['no_new_reported_drops']and not phase['status_delta']['failures']and not any(phase['health'].values())
       phase['load_packets']=loads[-12:];save();print(json.dumps({k:phase[k]for k in ['rate_requested_hz','actual_source_hz','estimated_source_bus_percent_without_stuffing','all_frames_received','status_delta','ok']}),flush=True)
       # An observed wireless loss is a result, not an excuse to omit higher loads.
       if not phase['source_rate_qualified']:break
      r['ok']=len(r['phases'])==len(a.rates)and all(p['ok']for p in r['phases'])
     except BaseException as e:r.update(failure=type(e).__name__,failure_message=str(e)[:180])
     finally:
      try:command(0);r['source_stopped']=True
      except Exception as e:r['source_stop_failure']=type(e).__name__
      try:await link.close()
      except Exception as e:r['link_close_failure']=type(e).__name__
      try:
       await prepare_usb(port,data=0)
       r['restored_usb_can']=True
       r['after']=monitor();r['delta']={k:(r['after'][k]-r['before'][k])&0xffffffff for k in r['before']}
      except Exception as e:r['restore_failure']=type(e).__name__
      r['health_after']=f.health()
      r['ok']=bool(r['ok']and r.get('source_stopped')and r.get('restored_usb_can')and 'delta'in r and not any(r['delta'].values())and not any(r['health_after'].values()))
      save()
    with hardware_bench_lock():
     f=hw.Fixture(a.elf);base=f.symbols['dual_load_bench'];r['fixture_image_sha256']=f.image_sha256
     assert not any(f.health().values())and not snap()['active']
     before=f.read(f.symbols['can_bench_clock_us'])[0];time.sleep(.1);after=f.read(f.symbols['can_bench_clock_us'])[0];assert 50000<((after-before)&0xffffffff)<250000,'Microsecond clock not advancing correctly'
     r['before']=monitor();f.quiet(0)
     try:asyncio.run(run())
     finally:
      if r.get('restored_usb_can')and r.get('source_stopped'):f.quiet(1);r['periodic_restored']=True
      save()
    print(json.dumps(dict(ok=r['ok'],mode=a.mode,phases=len(r['phases']),failure=r.get('failure'))),flush=True)
    return 0 if r['ok']else 1


if __name__=="__main__":
    raise SystemExit(main())
