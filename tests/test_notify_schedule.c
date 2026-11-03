#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "notify_schedule.h"

typedef struct {
    int64_t now;
    unsigned queued[3],taken[3],lanes[128],delivered,waits,rests,cost;
    bool ready,inject_ack,ack_on_wait,early_event;
} fixture_t;
static int64_t now_us(void *v){return ((fixture_t *)v)->now;}
static bool pending(void *v){return ((fixture_t *)v)->queued[0]!=0;}
static bool ready(void *v){return ((fixture_t *)v)->ready;}
static bool take(void *v,unsigned lane,bridge_notify_work_t *work){
    fixture_t *f=v;
    if(!f->queued[lane])return false;
    f->queued[lane]--;f->taken[lane]++;
    *work=(bridge_notify_work_t){.packet={.bytes={1,(uint8_t)(19+lane)}},.generation=7};
    return true;
}
static void deliver(void *v,const bridge_notify_work_t *work,unsigned lane){
    fixture_t *f=v;
    assert(work->generation==7&&work->packet.bytes[1]==19+lane);
    f->lanes[f->delivered++]=lane;f->now+=f->cost;
    if(f->inject_ack&&lane==BRIDGE_NOTIFY_RAW){f->queued[2]++;f->inject_ack=false;}
}
static void wait_event(void *v,unsigned ms){
    fixture_t *f=v;assert(ms==10);f->waits++;
    f->now+=f->early_event?50:10000;
    if(f->ack_on_wait){f->queued[2]++;f->ack_on_wait=false;}
}
static void rest(void *v){fixture_t *f=v;f->rests++;f->now+=10000;}
static const bridge_notify_schedule_ops_t ops={now_us,pending,ready,take,deliver,wait_event,rest};
int main(void){
    fixture_t f={.queued={2,1,1},.ready=true,.inject_ack=true,.cost=10};
    bridge_notify_schedule_t s={0};
    bridge_notify_schedule_step(&s,&ops,&f);
    unsigned expected[]={2,1,0,2,0};
    assert(f.delivered==5&&!memcmp(f.lanes,expected,sizeof(expected)));
    assert(f.waits==1&&s.busy_us==0);

    // A constrained pool never consumes raw, but an incoming ACK wakes the
    // scheduler and passes the waiting stream on the next priority check.
    f=(fixture_t){.queued={3,0,0},.ack_on_wait=true,.early_event=true};
    bridge_notify_schedule_step(&s,&ops,&f);
    assert(f.queued[0]==3&&f.taken[0]==0&&f.queued[2]==1);
    bridge_notify_schedule_step(&s,&ops,&f);
    assert(f.delivered==1&&f.lanes[0]==2&&f.taken[0]==0);
    f.ready=true;f.early_event=false;
    bridge_notify_schedule_step(&s,&ops,&f);
    assert(f.taken[0]==3&&f.delivered==4&&!f.queued[0]);

    // Healthy work is bounded by both packet count and elapsed time.
    f=(fixture_t){.queued={20,0,0},.ready=true,.cost=1};s=(bridge_notify_schedule_t){0};
    bridge_notify_schedule_step(&s,&ops,&f);assert(f.delivered==8&&f.queued[0]==12);
    f=(fixture_t){.queued={20,0,0},.ready=true,.cost=900};s=(bridge_notify_schedule_t){0};
    bridge_notify_schedule_step(&s,&ops,&f);assert(f.delivered==3&&f.rests==0);
    bridge_notify_schedule_step(&s,&ops,&f);assert(f.delivered==6&&f.rests==1&&s.busy_us==0);

    // Continuous producer/GAP hints cannot turn an interrupted wait into an
    // unbounded spin; a full blocked wait itself resets the busy budget.
    f=(fixture_t){.queued={1,0,0},.early_event=true};s=(bridge_notify_schedule_t){0};
    for(unsigned i=0;i<80;i++)bridge_notify_schedule_step(&s,&ops,&f);
    assert(f.rests==1&&f.taken[0]==0&&s.busy_us==0);
    f.early_event=false;s.busy_us=3999;
    bridge_notify_schedule_step(&s,&ops,&f);assert(s.busy_us==0&&f.rests==1);
    puts("PASS: ACK/control/raw priority, event wake, raw retained under reserve pressure, bounded batches/time, recovery without replay and producer-flood fairness.");
}
