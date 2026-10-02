#include "feedback_poll.h"
#include <string.h>
static void status(uint32_t id, uint32_t hz, uint32_t actual, uint8_t reason, uint32_t token, uint8_t detail) {
    bridge_packet_t b = {.bytes={1,8,reason,detail}};
    wire_put_u32(b.bytes+4,id); wire_put_u32(b.bytes+8,hz);
    wire_put_u32(b.bytes+12,actual); wire_put_u32(b.bytes+16,token);
    ble_bridge_publish(&b);
}
bool poll_active(const feedback_poll_t *p) { for(int i=0;i<POLL_MAX;i++)if(p->entries[i].hz)return true;return false; }
void poll_clear(feedback_poll_t *p,uint8_t reason) {
    for(int i=0;i<POLL_MAX;i++)if(p->entries[i].hz)status(p->entries[i].id,0,0,reason,0,0);
    memset(p,0,sizeof(*p));
}
void poll_command(feedback_poll_t *p,const bridge_packet_t *cmd,uint32_t bitrate,int64_t now) {
    poll_command_available(p,cmd,bitrate,now,true);
}
void poll_cancel_command(const bridge_packet_t *cmd) {
    // Explicit stop/name change invalidates queued enable requests, but each
    // accepted request still gets its original-token stopped acknowledgment.
    status(cmd->bytes[1]==0x15?0:wire_u32(cmd->bytes+4),0,0,1,wire_u32(cmd->bytes+16),0);
}
void poll_command_available(feedback_poll_t *p,const bridge_packet_t *cmd,uint32_t bitrate,int64_t now,bool can_available) {
    uint32_t id=wire_u32(cmd->bytes+4), hz=wire_u32(cmd->bytes+8), token=wire_u32(cmd->bytes+16);
    if(cmd->bytes[1]==0x15){poll_clear(p,1);status(0,0,0,1,token,0);return;}
    if(!id||id>0x7fe||hz>1000){status(id,0,0,3,token,0);return;}
    poll_entry_t *entry=NULL,*empty=NULL;uint32_t total=0;
    for(int i=0;i<POLL_MAX;i++){poll_entry_t *e=&p->entries[i];if(e->id==id)entry=e;else total+=e->hz;if(!e->hz&&!empty)empty=e;}
    if(!hz){if(entry)memset(entry,0,sizeof(*entry));status(id,0,0,1,token,0);return;}
    if(!can_available){status(id,0,0,4,token,POLL_ERR_BUS_OFF);return;}
    if(total+hz>bitrate/400||(!entry&&!empty)){status(id,entry?entry->hz:0,0,2,token,0);return;}
    if(!entry)entry=empty;
    if(entry->id!=id||entry->hz!=hz)*entry=(poll_entry_t){.id=id,.hz=hz,.due=now};
    entry->lease=now+3000000; // refreshed by foreground app every second
    status(id,hz,entry->actual,0,token,entry->detail);
}
void poll_expire(feedback_poll_t *p,int64_t now) {
    for(int i=0;i<POLL_MAX;i++){poll_entry_t *e=&p->entries[i];if(e->hz&&now>=e->lease){status(e->id,0,0,5,0,0);memset(e,0,sizeof(*e));}}
}
uint32_t poll_next(feedback_poll_t *p,int64_t now) {
    poll_expire(p,now);
    poll_entry_t *best=NULL;
    for(int i=0;i<POLL_MAX;i++){poll_entry_t *e=&p->entries[i];if(e->hz&&e->due<=now&&(!best||e->due<best->due))best=e;}
    if(!best)return 0;
    // Keep phase for on-time slots, skip missed slots instead of catch-up bursts.
    int64_t period=(1000000+best->hz-1)/best->hz;
    best->due+=period;if(best->due<=now)best->due=now+period;
    return best->id;
}
void poll_bus_off(feedback_poll_t *p) {
    for(int i=0;i<POLL_MAX;i++)if(p->entries[i].hz)status(p->entries[i].id,0,0,4,0,POLL_ERR_BUS_OFF);
    memset(p,0,sizeof(*p));
}
void poll_result(feedback_poll_t *p,uint32_t id,bool ok,uint8_t detail,int64_t now) {
    for(int i=0;i<POLL_MAX;i++){poll_entry_t *e=&p->entries[i];if(e->id!=id||!e->hz)continue;
        // A stopped, expired or removed entry must never be restarted by a late completion.
        if(ok){e->sent++;e->failures=e->arbitration=e->detail=0;return;}
        if(!detail)detail=POLL_ERR_UNKNOWN;
        bool arbitration=detail==POLL_ERR_ARB;
        if(arbitration)e->arbitration++;else e->failures++;
        if(e->failures>=POLL_ERROR_LIMIT||e->arbitration>=POLL_ARBITRATION_LIMIT){
            status(id,0,0,4,0,detail);memset(e,0,sizeof(*e));return;
        }
        // Retry at a future slot, never exceed the requested rate. Vary backoff to
        // avoid repeatedly colliding with a periodic higher-priority CAN sender.
        unsigned streak=arbitration?e->arbitration:e->failures;
        int64_t backoff=arbitration?2000*(streak>8?8:streak):5000*streak;
        backoff+=(id%7)*125+(streak%4)*250;
        if(e->due<now+backoff)e->due=now+backoff;
        bool changed=e->detail!=detail;e->detail=detail;
        if(changed)status(id,e->hz,e->actual,0,0,detail);
        return;
    }
}
void poll_report(feedback_poll_t *p,int64_t elapsed) {
    if(elapsed<=0)return;
    for(int i=0;i<POLL_MAX;i++){poll_entry_t *e=&p->entries[i];if(!e->hz)continue;
        e->actual=(uint32_t)((uint64_t)e->sent*1000000/elapsed);e->sent=0;status(e->id,e->hz,e->actual,0,0,e->detail);}
}
