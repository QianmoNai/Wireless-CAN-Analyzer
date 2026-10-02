#include "can_rates.h"
#include <stdint.h>

void can_rates_reset(can_rates_t *s, uint32_t rx, uint32_t tx, uint32_t bitrate, int64_t now)
{
    *s=(can_rates_t){.rx=rx,.tx=tx,.bitrate=bitrate,.started_us=now};
}

bool can_rates_snapshot(can_rates_t *s, bridge_packet_t *p, uint32_t rx, uint32_t tx, int64_t now)
{
    int64_t elapsed=now-s->started_us;
    if(elapsed<0||elapsed>UINT32_MAX){can_rates_reset(s,rx,tx,s->bitrate,now);return false;}
    if(elapsed<1000000)return false;
    *p=(bridge_packet_t){.bytes={1,14}};
    wire_put_u32(p->bytes+4,rx-s->rx);
    wire_put_u32(p->bytes+8,tx-s->tx);
    wire_put_u32(p->bytes+12,(uint32_t)elapsed);
    wire_put_u32(p->bytes+16,s->bitrate);
    can_rates_reset(s,rx,tx,s->bitrate,now);
    return true;
}
