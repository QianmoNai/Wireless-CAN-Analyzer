#!/usr/bin/env python3
"""Opt-in USB CAN delivery-failure accounting, followed by physical recovery.

Withholds identity-scoped bulk IN reads on the STM32 simulator bench only.
No motor commands, firmware flashing, secret reads or replay of unknown requests.
Missing source frames after disconnection are NOT all transport-owned losses.
"""
import argparse,asyncio,importlib.util,json,secrets,struct,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'desktop')]
from can_scope.connection_store import connection_folder,load_connection
from can_scope.protocol import u32
from can_scope.usb_protocol import Decoder,encode,HELLO
from can_scope.usb_transport import prepare_usb,UsbTransport
from hardware_bench_lock import hardware_bench_lock
from usb_management import LocalUsb
import usb.core


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',choices=['stm32h723-dual'],required=True)
    p.add_argument('--elf',type=Path,required=True);p.add_argument('--port',required=True)
    p.add_argument('--channel',type=int,choices=range(1,5),default=1)
    p.add_argument('--stop-file',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.parent.mkdir(parents=True,exist_ok=True)
    hw=module('usb_delivery_fixture','test-dual-can-hardware.py')
    RawCdc=module('usb_delivery_raw','test-usb-management-isolation-hardware.py').RawCdc
    saved=load_connection(connection_folder()/f'connection-{a.channel}.json')
    r=dict(ok=False,scope='Physical USB IN backpressure on a quiet, verified STM32 CAN1 simulator. Requires counted transport-owned failure and separate CAN recovery. Does not equate all disconnected source frames with queue drops.')
    def save():a.output.write_text(json.dumps(r,indent=2)+'\n')
    def stopped():
        if a.stop_file and a.stop_file.exists():raise InterruptedError('User stop')
    async def restore_usb():
        deadline=time.monotonic()+5
        while not Path(a.port).exists():
            if time.monotonic()>deadline:raise TimeoutError('Identity-scoped CDC port did not return')
            await asyncio.sleep(.05)
        return await prepare_usb(a.port,data=0)
    with hardware_bench_lock():
        f=hw.Fixture(a.elf);r['fixture_image_sha256']=f.image_sha256
        if 'dual_load_bench'not in f.symbols:raise ValueError('Microsecond load fixture required')
        base=f.symbols['dual_load_bench'];raw=None;source_owned=False;identity_verified=False;quieted=False
        def source(op,rate=0,count=0,nonce=0):
            nonlocal source_owned
            request=f.next_request(base+16,base+20)
            config=[op,rate,count,nonce]if op else[0,*f.read(base+4,3)]
            f.write(base,config);f.write(base+16,[request]);source_owned=True
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                done,result=f.read(base+20,2)
                if done==request:
                    if result:raise RuntimeError('Fixture rejected request '+str(result))
                    return
                time.sleep(.005)
            raise TimeoutError('Source outcome unknown; no overwrite/replay')
        try:
            stopped();assert not any(f.health().values())and not f.read(base+28)[0]
            u=LocalUsb(a.port)
            try:identity=u.identity().hex();assert identity==saved['id']
            finally:u.close()
            r['identity']=identity;identity_verified=True;quieted=True;f.quiet(0);asyncio.run(restore_usb())
            raw=RawCdc(identity);decoder=Decoder();sid=secrets.randbelow(0xffffffff)+1;events=[]
            def drain_until(predicate,seconds):
                deadline=time.monotonic()+seconds
                while time.monotonic()<deadline:
                    stopped()
                    try:data=bytes(raw.device.read(129,4096,timeout=100))
                    except usb.core.USBTimeoutError:continue
                    events.extend(decoder.feed(data))
                    if predicate():return
                raise TimeoutError('Physical USB response missing')
            def query():
                token=secrets.randbelow(0xffffffff)+1;request=bytearray((1,0x1c))+bytearray(18);struct.pack_into('<I',request,16,token)
                assert raw.device.write(1,encode(sid,request),timeout=1000)==32
                def response():return next((p for s,p in events if s==sid and p[1]==27 and u32(p,16)==token),None)
                drain_until(lambda:response()is not None,3);reply=response();assert reply[2]==1 and not reply[3]
                return dict(zip(('bridge_delivery_dropped','can_rx_queue_dropped','dual_dropped'),struct.unpack_from('<III',reply,4)))
            r['before']=query()
            assert raw.device.write(1,encode(sid,HELLO),timeout=1000)==32
            drain_until(lambda:any(s==sid and p[:3]==bytes((1,0x7e,1))for s,p in events),3)
            nonce=secrets.randbelow(0xffffffff)+1;source(2,200,400,nonce)
            started=time.monotonic();deadline=started+5
            while True:
                stopped();v=f.read(base,40)
                if v[7]==2:break
                if time.monotonic()>deadline:raise TimeoutError('Pressure source did not finish')
                time.sleep(.05) # Deliberately no USB IN reads.
            r['in_reads_withheld_seconds']=time.monotonic()-started
            assert v[38]==1000000 and v[39]==1 and not v[6]
            r['source']=dict(tx_queued=v[10],tx_busy=v[11],tx_errors=v[12],skipped_slots=v[13],clock_hz=v[38],bus_mask=v[39])
            elapsed=(v[14]-v[8])&0xffffffff;r['actual_source_hz']=(v[10]-1)*1000000/elapsed if elapsed else 0
            r['source_fifo_free']=f.read(f.symbols['can_bench_tx_free'],2);source(0)
            # The query drains existing bytes before its freshly correlated reply.
            r['after']=query();r['delta']={k:(r['after'][k]-r['before'][k])&0xffffffff for k in r['before']}
            r['valid_pressure_frames']=len({u32(p,12)for s,p in events if s==sid and p[1]==1 and u32(p,4)==0x6a0 and u32(p,8)==nonce})
            r['usb_codec']=dict(crc_errors=decoder.crc_errors,discarded_bytes=decoder.discarded_bytes)
            r['counted_delivery_failure']=r['delta']['bridge_delivery_dropped']>0
            raw.close();raw=None;r['cdc_restored']=True;asyncio.run(restore_usb())
            async def recover():
                received=set();errors=[];fresh=secrets.randbelow(0xffffffff)+1
                def rx(p):
                    if p[1]==1 and u32(p,4)==0x6a0 and u32(p,8)==fresh:received.add(u32(p,12))
                link=UsbTransport(rx,errors.append)
                try:
                    await link.connect(a.port);source(2,100,100,fresh);deadline=time.monotonic()+4
                    while len(received)<100 and time.monotonic()<deadline:
                        stopped();await asyncio.sleep(.02)
                    source(0);assert received==set(range(100))and not errors
                    return dict(unique=len(received),expected=100,ok=True)
                finally:await link.close()
            r['recovery']=asyncio.run(recover());r['health_after']=f.health()
            r['ok']=bool(r['counted_delivery_failure']and r['source']['tx_queued']==400 and r['actual_source_hz']>=196 and not r['source']['tx_errors']and r['source_fifo_free'][0]==8 and r['recovery']['ok']and not any(r['health_after'].values()))
        except BaseException as error:r.update(ok=False,failure=type(error).__name__,failure_message=str(error)[:180])
        finally:
            if raw:
                try:raw.close();r['cdc_restored']=True
                except Exception as error:r.update(ok=False,cdc_restore_failure=type(error).__name__)
            if identity_verified:
                try:
                    if source_owned:source(0)
                    r['source_stopped']=True
                    asyncio.run(restore_usb());r['restored_usb_can']=True
                    u=LocalUsb(a.port)
                    try:assert u.identity().hex()==saved['id'];r['final_counters']=u.monitor_drops()
                    finally:u.close()
                    r['health_final']=f.health();assert not any(r['health_final'].values())
                    if quieted:f.quiet(1);r['periodic_restored']=True
                except Exception as error:r.update(ok=False,cleanup_failure=type(error).__name__)
            save()
    print(json.dumps({k:r[k]for k in ['ok','delta','counted_delivery_failure','valid_pressure_frames','recovery','failure','cleanup_failure']if k in r}),flush=True)
    return 0 if r['ok']else 1


if __name__=='__main__':raise SystemExit(main())
