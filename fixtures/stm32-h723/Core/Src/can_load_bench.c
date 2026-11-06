#include "can_load_bench.h"
#include <string.h>

static uint32_t get32(const uint8_t *p) {
  return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static void put32(uint8_t *p,uint32_t n) {
  for (unsigned i=0;i<4;i++) p[i]=(uint8_t)(n>>(8*i));
}
void CAN_LoadPoll(volatile CAN_LoadControl *c,CAN_LoadState *s,uint32_t now,
                  bool allowed,CAN_LoadSend send,void *context) {
  uint32_t request=c->request;
  if (request && request!=c->done) {
    if (c->command==0) {
      s->receiving=false;c->active=0;c->result=0;
    } else if (c->command!=1 || !c->rate_hz || c->rate_hz>1000 ||
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
      s->started_ms=now;s->receiving=true;
      s->next_due[0]=s->next_due[1]=now;
      c->started_ms=now;c->finished_ms=0;c->result=0;c->active=1;
    }
    c->done=request;
  }
  if (c->active!=1) return;
  if (!allowed) { c->active=0;c->result=5;return; }
  for (unsigned bus=0;bus<2;bus++) {
    if (c->bus[bus].tx_queued>=s->frames || (int32_t)(now-s->next_due[bus])<0) continue;
    CAN_Frame frame={.id=bus?CAN_LOAD_ID_B:CAN_LOAD_ID_A,.length=8};
    put32(frame.data,s->nonce);put32(frame.data+4,c->bus[bus].tx_queued);
    unsigned result=send(bus,&frame,context);
    if (result==1) { c->bus[bus].tx_busy++;continue; }
    if (result) { c->bus[bus].tx_errors++;c->active=0;c->result=4;return; }
    c->bus[bus].tx_queued++;
    if (c->bus[bus].tx_queued==s->frames) c->bus[bus].tx_done_ms=now;
    /* Skip missed time slots, never emit a catch-up burst or exceed the rate. */
    uint32_t next=s->slot[bus]+1;
    uint32_t future=(uint32_t)((uint64_t)(uint32_t)(now-s->started_ms)*s->rate_hz/1000)+1;
    if (future>next) { c->bus[bus].skipped_slots+=future-next;next=future; }
    s->slot[bus]=next;
    s->next_due[bus]=s->started_ms+(uint32_t)(((uint64_t)next*1000+s->rate_hz-1)/s->rate_hz);
  }
  if (c->bus[0].tx_queued==s->frames && c->bus[1].tx_queued==s->frames) {
    c->active=2;c->finished_ms=now; /* Keep receiving until explicit STOP. */
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
  if (!stats->rx_unique) stats->rx_first_ms=now;
  stats->rx_last_ms=now;stats->rx_unique++;
  if (seq<s->next_rx[bus]) stats->rx_out_of_order++;
  else {
    if (seq>s->next_rx[bus]) stats->rx_gap_events++;
    s->next_rx[bus]=seq+1;
  }
  stats->rx_last_sequence=seq;
  return true;
}
