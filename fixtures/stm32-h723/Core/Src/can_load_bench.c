#include "can_load_bench.h"
#include <string.h>

static uint32_t get32(const uint8_t *p) {
  return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static void put32(uint8_t *p,uint32_t n) {
  for (unsigned i=0;i<4;i++) p[i]=(uint8_t)(n>>(8*i));
}
static void poll(volatile CAN_LoadControl *c,CAN_LoadState *s,uint32_t now,
                  uint32_t clock_hz,bool allowed,CAN_LoadSend send,void *context) {
  uint32_t request=c->request;
  if (request && request!=c->done) {
    if (c->command==0) {
      s->receiving=false;c->active=0;c->result=0;
    } else if (c->command>4 || (c->command==1)!=(clock_hz==1000) ||
               !c->rate_hz || c->rate_hz>(clock_hz==1000?1000U:10000U) ||
               !c->frames || c->frames>CAN_LOAD_MAX_FRAMES || !c->nonce || !send) {
      c->result=1;
    } else if (s->receiving) {
      c->result=2; /* Explicit STOP required; never erase an unknown old outcome. */
    } else if (!allowed) {
      c->result=3;
    } else {
      memset(s,0,sizeof(*s));
      memset((void *)c->bus,0,sizeof(c->bus));
      s->rate_hz=c->rate_hz;s->frames=c->frames;s->nonce=c->nonce;
      s->clock_hz=clock_hz;s->bus_mask=c->command==2?1:c->command==3?2:3;
      c->clock_hz=s->clock_hz;c->bus_mask=s->bus_mask;
      s->started_at=now;s->receiving=true;
      s->next_due[0]=s->next_due[1]=now;
      c->started_at=now;c->finished_at=0;c->result=0;c->active=1;
    }
    c->done=request;
  }
  if (c->active!=1) return;
  if (!allowed) { c->active=0;c->result=5;return; }
  for (unsigned bus=0;bus<2;bus++) {
    if (!(s->bus_mask&(1U<<bus))) continue;
    if (c->bus[bus].tx_queued>=s->frames || (int32_t)(now-s->next_due[bus])<0) continue;
    CAN_Frame frame={.id=bus?CAN_LOAD_ID_B:CAN_LOAD_ID_A,.length=8};
    put32(frame.data,s->nonce);put32(frame.data+4,c->bus[bus].tx_queued);
    unsigned result=send(bus,&frame,context);
    if (result==1) { c->bus[bus].tx_busy++;continue; }
    if (result) { c->bus[bus].tx_errors++;c->active=0;c->result=4;return; }
    c->bus[bus].tx_queued++;
    if (c->bus[bus].tx_queued==s->frames) c->bus[bus].tx_done_at=now;
    /* Skip missed time slots, never emit a catch-up burst or exceed the rate. */
    uint32_t next=s->slot[bus]+1;
    uint32_t future=(uint32_t)((uint64_t)(uint32_t)(now-s->started_at)*s->rate_hz/s->clock_hz)+1;
    if (future>next) { c->bus[bus].skipped_slots+=future-next;next=future; }
    s->slot[bus]=next;
    s->next_due[bus]=s->started_at+(uint32_t)(((uint64_t)next*s->clock_hz+s->rate_hz-1)/s->rate_hz);
  }
  if ((!(s->bus_mask&1) || c->bus[0].tx_queued==s->frames) &&
      (!(s->bus_mask&2) || c->bus[1].tx_queued==s->frames)) {
    c->active=2;c->finished_at=now; /* Keep receiving until explicit STOP. */
  }
}
bool CAN_LoadReceive(volatile CAN_LoadControl *c,CAN_LoadState *s,unsigned bus,
                     const CAN_Frame *f,uint32_t now) {
  if (bus>1 || !s->receiving || (f->id!=CAN_LOAD_ID_A && f->id!=CAN_LOAD_ID_B)) return false;
  volatile CAN_LoadStats *stats=&c->bus[bus];
  if (f->id!=(bus?CAN_LOAD_ID_A:CAN_LOAD_ID_B) || f->extended || f->remote || f->length!=8) {
    stats->rx_bad++;return true;
  }
  if (get32(f->data)!=s->nonce) { stats->rx_stale++;return true; }
  uint32_t seq=get32(f->data+4);
  if (seq>=s->frames) { stats->rx_bad++;return true; }
  uint8_t bit=(uint8_t)(1U<<(seq&7U));
  if (s->seen[bus][seq>>3]&bit) { stats->rx_duplicates++;return true; }
  s->seen[bus][seq>>3]|=bit;
  if (!stats->rx_unique) stats->rx_first_at=now;
  stats->rx_last_at=now;stats->rx_unique++;
  if (seq<s->next_rx[bus]) stats->rx_out_of_order++;
  else {
    if (seq>s->next_rx[bus]) stats->rx_gap_events++;
    s->next_rx[bus]=seq+1;
  }
  stats->rx_last_sequence=seq;
  return true;
}
void CAN_LoadPoll(volatile CAN_LoadControl *c,CAN_LoadState *s,uint32_t now,
                  bool allowed,CAN_LoadSend send,void *context) {
  poll(c,s,now,1000,allowed,send,context);
}
void CAN_LoadPollUs(volatile CAN_LoadControl *c,CAN_LoadState *s,uint32_t now,
                    bool allowed,CAN_LoadSend send,void *context) {
  poll(c,s,now,1000000,allowed,send,context);
}
uint32_t CAN_LoadClock(uint32_t cycles,uint32_t cycles_per_us,
                       uint32_t *previous,uint32_t *remainder,uint32_t *micros) {
  if (!cycles_per_us) return *micros;
  uint64_t elapsed=(uint64_t)(uint32_t)(cycles-*previous)+*remainder;
  *previous=cycles;*micros+=(uint32_t)(elapsed/cycles_per_us);
  *remainder=(uint32_t)(elapsed%cycles_per_us);
  return *micros;
}
