#include "ble_connection_policy.h"

void bridge_ble_policy_start(bridge_ble_policy_t *s,uint32_t generation,int64_t now_ms)
{
    *s=(bridge_ble_policy_t){.generation=generation,.due_ms=now_ms+1000};
}
bool bridge_ble_policy_next(bridge_ble_policy_t *s,uint32_t generation,int64_t now_ms,
                            unsigned profile,bool ready,uint16_t interval,
                            uint16_t latency,uint16_t timeout,bridge_ble_params_t *p)
{
    if(generation!=s->generation||!ready){s->finished=true;return false;}
    if(s->finished||s->attempts>=2||now_ms<s->due_ms)return false;
    if(profile!=1&&profile!=2&&profile!=3){s->finished=true;return false;}
    uint16_t low=profile==1?12:6,high=profile==1?24:profile==3?6:12; // 1.25 ms units.
    if(interval>=low&&interval<=high&&latency==0){s->finished=true;return false;}
    // Latency zero: timeout(10ms) > 2 * max interval(1.25ms).
    // Preserve the current legal timeout, avoiding another A/B variable.
    if(timeout<10||timeout>3200||timeout*4u<=high)timeout=200;
    *p=(bridge_ble_params_t){low,high,0,timeout};
    s->attempts++;s->due_ms=now_ms+3000;
    return true;
}
void bridge_ble_policy_result(bridge_ble_policy_t *s,int rc)
{
    // Accepted is not negotiated success; never fight a later central update.
    if(rc==0||s->attempts>=2)s->finished=true;
}
