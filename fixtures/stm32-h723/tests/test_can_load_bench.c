#include "can_load_bench.h"
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

_Static_assert(sizeof(CAN_LoadControl)==160,"SWD control layout");
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
  poll(102,true);assert(control.active==2 && control.finished_at==102);
  assert(control.bus[0].tx_done_at==102 && control.bus[1].tx_done_at==102);
  // Physical sink validation is independent of source enqueue success.
  for (unsigned bus=0;bus<2;bus++) {
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][0],103));
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][2],105));
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][1],106));
    assert(CAN_LoadReceive(&control,&state,bus,&frames[1-bus][0],107));
    assert(control.bus[bus].rx_unique==3 && control.bus[bus].rx_duplicates==1);
    assert(control.bus[bus].rx_gap_events==1 && control.bus[bus].rx_out_of_order==1);
    assert(control.bus[bus].rx_first_at==103 && control.bus[bus].rx_last_at==106);
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
  // Microsecond source exceeds 1 kHz and can isolate either physical bus.
  fresh(4000,3,9);control.command=2;
  CAN_LoadPollUs(&control,&state,100,true,send_frame,&control);
  assert(control.clock_hz==1000000 && control.bus_mask==1 && calls[0]==1 && !calls[1]);
  CAN_LoadPollUs(&control,&state,349,true,send_frame,&control);assert(calls[0]==1);
  CAN_LoadPollUs(&control,&state,350,true,send_frame,&control);
  CAN_LoadPollUs(&control,&state,600,true,send_frame,&control);
  assert(control.active==2 && control.bus[0].tx_done_at==600 && !calls[1]);
  fresh(10000,3,9);control.command=3;
  CAN_LoadPollUs(&control,&state,UINT32_MAX-99,true,send_frame,&control);
  CAN_LoadPollUs(&control,&state,0,true,send_frame,&control);
  CAN_LoadPollUs(&control,&state,100,true,send_frame,&control);
  assert(control.active==2 && !calls[0] && calls[1]==3 && control.bus_mask==2);
  fresh(10000,3,9);control.command=4;
  CAN_LoadPollUs(&control,&state,0,true,send_frame,&control);
  CAN_LoadPollUs(&control,&state,1000,true,send_frame,&control);
  CAN_LoadPollUs(&control,&state,1000,true,send_frame,&control);
  assert(calls[0]==2 && calls[1]==2 && control.bus[0].skipped_slots==9);
  fresh(10001,3,9);control.command=2;
  CAN_LoadPollUs(&control,&state,0,true,send_frame,&control);assert(control.result==1 && !calls[0]);
  // Idle state retains the preceding run's clock. A newly published request
  // chooses its own clock inside admission, including opposite-clock starts.
  for (unsigned op=1;op<=4;op++) {
    fresh(op==1?1000:7500,3,9);control.command=op;
    state.clock_hz=op==1?1000000:1000;
    CAN_LoadPollClocks(&control,&state,12,345000,true,send_frame,&control);
    assert(!control.result && control.done==control.request);
    assert(control.clock_hz==(op==1?1000U:1000000U));
    assert(control.started_at==(op==1?12U:345000U));
    assert(control.bus_mask==(op==2?1U:op==3?2U:3U));
    // Config changes without publishing a request cannot retime an active run.
    control.command=op==1?2:1;
    CAN_LoadPollClocks(&control,&state,13,345134,true,send_frame,&control);
    assert(control.clock_hz==(op==1?1000U:1000000U));
    control.command=0;control.request++;
    CAN_LoadPollClocks(&control,&state,14,345268,true,send_frame,&control);
    assert(!control.active && !control.result && control.done==control.request);
  }
  // DWT conversion carries fractional cycles and handles the 32-bit wrap.
  uint32_t previous=UINT32_MAX-199,remainder=0,micros=UINT32_MAX;
  assert(CAN_LoadClock(0,400,&previous,&remainder,&micros)==UINT32_MAX && remainder==200);
  assert(CAN_LoadClock(200,400,&previous,&remainder,&micros)==0 && !remainder);
  assert(CAN_LoadClock(399,400,&previous,&remainder,&micros)==0 && remainder==199);
  assert(CAN_LoadClock(600,400,&previous,&remainder,&micros)==1 && !remainder);
  assert(CAN_LoadClock(800,0,&previous,&remainder,&micros)==1 && previous==600);
  puts("CAN load bench: exact bytes, independent sinks, nonce/sequence bitmap, wrap, busy/error, no catch-up, stop and admission PASS");
}
