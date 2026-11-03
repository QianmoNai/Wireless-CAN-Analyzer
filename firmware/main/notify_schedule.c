#include "notify_schedule.h"

void bridge_notify_schedule_step(bridge_notify_schedule_t *state,
                                const bridge_notify_schedule_ops_t *ops,void *context)
{
    enum { BATCH_PACKETS=8, BATCH_US=2000, BUSY_US=4000, REST_US=2000 };
    int64_t began=ops->now_us(context);
    for(unsigned n=0;n<BATCH_PACKETS;n++){
        bridge_notify_work_t work;
        unsigned lane=BRIDGE_NOTIFY_ACK;
        bool have=ops->take(context,lane,&work);
        if(!have){lane=BRIDGE_NOTIFY_CONTROL;have=ops->take(context,lane,&work);}
        if(!have){
            lane=BRIDGE_NOTIFY_RAW;
            if(ops->pending_raw(context)&&ops->raw_ready(context))
                have=ops->take(context,lane,&work);
        }
        if(!have){
            int64_t waiting=ops->now_us(context);
            // Producers and GAP hints can interrupt this safety checkpoint.
            // No public controller-credit completion hook is assumed.
            ops->wait_event(context,10);
            if(ops->now_us(context)-waiting>=REST_US){state->busy_us=0;return;}
            break;
        }
        ops->deliver(context,&work,lane);
        if(ops->now_us(context)-began>=BATCH_US)break;
    }
    state->busy_us+=ops->now_us(context)-began;
    if(state->busy_us>=BUSY_US){
        // A real blocked tick serves host/idle tasks under a producer flood.
        ops->rest(context);
        state->busy_us=0;
    }
}
