#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../firmware/main/ack_delivery.h"

typedef struct {
    bool online;
    unsigned sends, delays, failures, disconnect_after_delays;
    bridge_packet_t expected;
} delivery_t;
static bool connected(void *context) { return ((delivery_t *)context)->online; }
static bool send(void *context, const bridge_packet_t *packet)
{
    delivery_t *d = context;
    assert(!memcmp(packet, &d->expected, sizeof(*packet)));
    return ++d->sends > d->failures;
}
static void delay(void *context, unsigned milliseconds)
{
    delivery_t *d = context;
    assert(milliseconds == 10);
    if (++d->delays == d->disconnect_after_delays) d->online = false;
}
int main(void)
{
    bridge_packet_t packet = {.bytes = {1, 8}};
    for (unsigned type = 1; type <= 14; type++) {
        packet.bytes[1] = type;
        packet.bytes[2] = 0;
        wire_put_u32(packet.bytes + 16, 0);
        assert(!bridge_packet_is_ack(&packet));
        assert(bridge_packet_is_raw(&packet)==(type==1||type==2||type==3||type==11||type==13));
        wire_put_u32(packet.bytes + 16, 1000000);
        assert(bridge_packet_is_ack(&packet) == (type == 5 || type == 8 || type == 10));
        assert(bridge_packet_is_raw(&packet)==(type==1||type==11||type==13));
        assert(!(bridge_packet_is_ack(&packet)&&bridge_packet_is_raw(&packet)));
    }
    packet.bytes[1] = 7;
    packet.bytes[2] = 1;
    assert(bridge_packet_is_ack(&packet));
    packet.bytes[2] = 2;
    assert(bridge_packet_is_ack(&packet));
    packet.bytes[2] = 0; // Periodic name's last bytes must not look like a token.
    assert(!bridge_packet_is_ack(&packet));
    assert(!bridge_packet_is_raw(&packet));
    packet.bytes[1]=3;packet.bytes[2]=0x80;wire_put_u32(packet.bytes+16,0);
    assert(bridge_packet_is_raw(&packet)&&!bridge_packet_is_ack(&packet)); // Arbitration diagnostics remain low-priority raw.

    delivery_t d = {.online = true, .expected = packet};
    assert(bridge_deliver_ack(&packet, connected, send, delay, &d));
    assert(d.sends == 1 && d.delays == 0);
    d = (delivery_t){.online = true, .failures = 3, .expected = packet};
    assert(bridge_deliver_ack(&packet, connected, send, delay, &d));
    assert(d.sends == 4 && d.delays == 3);
    d = (delivery_t){.online = true, .failures = 100, .expected = packet};
    assert(!bridge_deliver_ack(&packet, connected, send, delay, &d));
    assert(d.sends == 8 && d.delays == 7);
    d = (delivery_t){.online = true, .failures = 100, .disconnect_after_delays = 2, .expected = packet};
    assert(!bridge_deliver_ack(&packet, connected, send, delay, &d));
    assert(d.sends == 2 && d.delays == 2);
    d = (delivery_t){.expected = packet};
    assert(!bridge_deliver_ack(&packet, connected, send, delay, &d));
    assert(d.sends == 0 && d.delays == 0);
    puts("PASS: explicit ACK/raw classification, tokenless TX telemetry, token-bearing send priority, timestamp/load exclusion, bounded retries, successful recovery and disconnect cancellation.");
}
