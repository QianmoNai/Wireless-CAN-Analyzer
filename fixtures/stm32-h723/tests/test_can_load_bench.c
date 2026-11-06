#include "can_load_bench.h"
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

_Static_assert(sizeof(CAN_LoadControl)==152,"SWD control layout");
_Static_assert(offsetof(CAN_LoadControl,request)==16,"SWD trigger offset");
_Static_assert(offsetof(CAN_LoadControl,bus)==40,"SWD counter offset");
static CAN_LoadState state;
static CAN_LoadControl control;
static CAN_Frame frames[2][16];
static unsigned calls[2],busy[2],fail[2];
static unsigned send_frame(unsigned bus,const CAN_Frame *frame,void *context) {
  assert(context==&control);calls[bus]++;
  if (busy[bus]) { busy[bus]--;return 1; }
  if (fail[bus]) return 2;
  unsigned at=control.bus[bus].tx_queued;
  if (at<16) frames[bus][at]=*frame;
  return 0;
}
static void poll(uint32_t now,bool allowed) {
  CAN_LoadPoll(&control,&state,now,allowed,send_frame,&control);
}
static void fresh(uint32_t rate,uint32_t count,uint32_t nonce) {
  memset(&control,0,sizeof(control));memset(&state,0,sizeof(state));
  memset(calls,0,sizeof(calls));memset(busy,0,sizeof(busy));memset(fail,0,sizeof(fail));
  control.command=1;control.rate_hz=rate;control.frames=count;control.nonce=nonce;control.request=1;
}
int main(void) {
  fresh(1000,3,0x12345678);poll(100,true);
  assert(control.done==1 && !control.result && control.active==1);
  assert(control.bus[0].tx_queued==1 && control.bus[1].tx_queued==1);
  assert(frames[0][0].id==CAN_LOAD_ID_A && frames[1][0].id==CAN_LOAD_ID_B);
  const uint8_t expected[8]={0x78,0x56,0x34,0x12,0,0,0,0};
  assert(!memcmp(frames[0][0].data,expected,8));
  poll(100,true);assert(calls[0]==1 && calls[1]==1);
  poll(101,true);busy[0]=1;poll(102,true);
  assert(control.bus[0].tx_queued==2 && control.bus[0].tx_busy==1 && control.bus[1].tx_queued==3);
  poll(102,true);assert(control.active==2 && control.finished_ms==102);
  assert(control.bus[0].tx_done_ms==102 && control.bus[1].tx_done_ms==102);
  // Physical sink validation is independent of source enqueue success.
  for (unsigned bus=0;bus<2;bus++) {
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][0],103));
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][2],105));
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][1],106));
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][0],107));
    assert(control.bus[bus].rx_unique==3 && control.bus[bus].rx_duplicates==1);
    assert(control.bus[bus].rx_gap_events==1 && control.bus[bus].rx_out_of_order==1);
    assert(control.bus[bus].rx_first_ms==103 && control.bus[bus].rx_last_ms==106);
  }
  CAN_Frame bad=frames[0][0];bad.data[0]^=1;
  assert(CAN_LoadReceive(&control,&state,1,&bad,108) && control.bus[1].rx_stale==1);
  bad=frames[0][0];bad.extended=1;CAN_LoadReceive(&control,&state,1,&bad,108);
  bad=frames[0][0];bad.remote=1;CAN_LoadReceive(&control,&state,1,&bad,108);
  bad=frames[0][0];bad.length=7;CAN_LoadReceive(&control,&state,1,&bad,108);
  bad=frames[0][0];bad.data[4]=3;CAN_LoadReceive(&control,&state,1,&bad,108);
  CAN_LoadReceive(&control,&state,0,&frames[0][0],108);
  assert(control.bus[1].rx_bad==4 && control.bus[0].rx_bad==1);
  bad.id=0x201;assert(!CAN_LoadReceive(&control,&state,0,&bad,108));
  assert(!CAN_LoadReceive(&control,&state,2,&frames[0][0],108));
  control.request=2;poll(109,true);assert(control.result==2 && control.bus[0].rx_unique==3);
  control.command=0;control.request=3;poll(110,false);
  assert(!control.active && !control.result && control.done==3 && !state.receiving);
  assert(!CAN_LoadReceive(&control,&state,1,&frames[0][0],111));
  // Requested slots are an upper bound: delayed servicing never catches up.
  fresh(1000,3,1);poll(0,true);poll(10,true);poll(10,true);
  assert(control.bus[0].tx_queued==2 && control.bus[0].skipped_slots==9);
  poll(11,true);assert(control.active==2);
  fresh(333,3,1);poll(0,true);poll(3,true);assert(control.bus[0].tx_queued==1);
  poll(4,true);poll(6,true);assert(control.bus[0].tx_queued==2);poll(7,true);assert(control.active==2);
  fresh(1000,3,1);poll(UINT32_MAX-1,true);poll(UINT32_MAX,true);poll(0,true);assert(control.active==2);
  fresh(1000,3,1);fail[0]=1;poll(0,true);assert(control.result==4 && !control.active && control.bus[0].tx_errors==1 && !calls[1]);
  poll(1,true);assert(calls[0]==1); // Never replay an errored enqueue.
  fresh(1000,3,1);poll(0,false);assert(control.done==1 && control.result==3 && !state.receiving && !calls[0]);
  fresh(1000,3,1);poll(0,true);poll(1,false);assert(control.result==5 && !control.active && control.bus[0].tx_queued==1);
  for (unsigned kind=0;kind<5;kind++) {
    fresh(1000,3,1);
    if (kind==0) control.rate_hz=0;
    if (kind==1) control.rate_hz=1001;
    if (kind==2) control.frames=0;
    if (kind==3) control.frames=CAN_LOAD_MAX_FRAMES+1;
    if (kind==4) control.nonce=0;
    poll(0,true);assert(control.result==1 && control.done==1 && !control.active && !calls[0]);
  }
  fresh(1000,CAN_LOAD_MAX_FRAMES,1);poll(0,true);
  bad=frames[0][0];uint32_t last=CAN_LOAD_MAX_FRAMES-1;
  for (unsigned i=0;i<4;i++) bad.data[4+i]=(uint8_t)(last>>(i*8));
  assert(CAN_LoadReceive(&control,&state,1,&bad,1) && control.bus[1].rx_unique==1);
  puts("CAN load bench: exact bytes, independent sinks, nonce/sequence bitmap, wrap, busy/error, no catch-up, stop and admission PASS");
}
