#include <assert.h>
#include <stdio.h>
#include "../firmware/main/can_rates.h"

int main(void)
{
    can_rates_t s;
    bridge_packet_t p;
    can_rates_reset(&s,100,50,1000000,500000);
    assert(!can_rates_snapshot(&s,&p,500,200,1000000));
    assert(can_rates_snapshot(&s,&p,1100,550,1500000));
    assert(p.bytes[0]==1&&p.bytes[1]==14&&p.bytes[2]==0&&p.bytes[3]==0);
    assert(wire_u32(p.bytes+4)==1000&&wire_u32(p.bytes+8)==500);
    assert(wire_u32(p.bytes+12)==1000000&&wire_u32(p.bytes+16)==1000000);
    // A delayed sample includes the real longer interval, not a fixed second.
    assert(can_rates_snapshot(&s,&p,3600,1800,4000000));
    assert(wire_u32(p.bytes+4)==2500&&wire_u32(p.bytes+8)==1250&&wire_u32(p.bytes+12)==2500000);
    assert(!can_rates_snapshot(&s,&p,3600,1800,4000000));
    assert(can_rates_snapshot(&s,&p,3600,1800,5000000));
    assert(wire_u32(p.bytes+4)==0&&wire_u32(p.bytes+8)==0);
    // Both cumulative counters may wrap; time remains 64-bit on the device.
    can_rates_reset(&s,UINT32_MAX-4,UINT32_MAX-9,1000000,INT64_C(5000000000));
    assert(can_rates_snapshot(&s,&p,5,10,INT64_C(5001000000)));
    assert(wire_u32(p.bytes+4)==10&&wire_u32(p.bytes+8)==20);
    // A bitrate change discards the prior partial window.
    can_rates_reset(&s,5,10,500000,INT64_C(5001500000));
    assert(!can_rates_snapshot(&s,&p,15,20,INT64_C(5002000000)));
    assert(can_rates_snapshot(&s,&p,25,30,INT64_C(5002500000)));
    assert(wire_u32(p.bytes+4)==20&&wire_u32(p.bytes+16)==500000);
    // Unrepresentable or reversed clocks rebase without a bogus huge rate.
    assert(!can_rates_snapshot(&s,&p,25,30,0));
    assert(!can_rates_snapshot(&s,&p,35,40,(int64_t)UINT32_MAX+1));
    assert(can_rates_snapshot(&s,&p,45,50,(int64_t)UINT32_MAX+1000001));
    assert(wire_u32(p.bytes+4)==10&&wire_u32(p.bytes+12)==1000000);
    puts("PASS: device rate windows, real elapsed time, delayed/zero windows, counter wrap, bitrate reset and invalid-clock rebasing.");
}
