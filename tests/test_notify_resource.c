#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../firmware/main/notify_resource.h"
#include "../firmware/main/ack_delivery.h"

typedef struct { unsigned free, total; } pool_t;
typedef struct { pool_t *pool; bool owned; } buffer_t;
typedef struct {
    pool_t small, large;
    buffer_t payload;
    bool online, fail_submit;
    unsigned allocations, reads, releases, submissions, pending_blocks;
    unsigned sends, delays, drain_after;
    bridge_packet_t expected;
} resource_t;

static resource_t fixture(unsigned small_free)
{
    return (resource_t){.small = {small_free, 12}, .large = {24, 24},
                        .online = true, .expected = {.bytes = {1, 8}}};
}
static void *allocate(void *context, const bridge_packet_t *packet)
{
    resource_t *r = context;
    assert(!memcmp(packet, &r->expected, sizeof(*packet)));
    // NimBLE selects the first fitting pool, without a free-count fallback.
    assert(!r->payload.owned);
    r->allocations++;
    if (!r->small.free) return NULL;
    r->small.free--;
    r->payload = (buffer_t){.pool = &r->small, .owned = true};
    return &r->payload;
}
static unsigned pool_free(void *context, void *buffer)
{
    resource_t *r = context;
    buffer_t *b = buffer;
    assert(b->owned && b->pool == &r->small);
    r->reads++;
    return b->pool->free;
}
static void release(void *context, void *buffer)
{
    resource_t *r = context;
    buffer_t *b = buffer;
    assert(b->owned);
    b->owned = false;
    b->pool->free++;
    r->releases++;
}
static bool submit(void *context, void *buffer)
{
    resource_t *r = context;
    buffer_t *b = buffer;
    assert(b->owned && b->pool->free);
    b->owned = false; // notify_custom consumes payload on either outcome.
    b->pool->free--; // Its separately allocated ATT header.
    r->submissions++;
    if (r->fail_submit) {
        b->pool->free += 2;
        return false;
    }
    r->pending_blocks += 2;
    return true;
}
static const bridge_notify_resource_ops_t ops = {
    .allocate = allocate, .pool_free = pool_free, .release = release,
    .submit = submit,
};
static bool send(void *context, const bridge_packet_t *packet)
{
    resource_t *r = context;
    r->sends++;
    return bridge_notify_with_reserve(packet, &ops, r);
}
static bool connected(void *context) { return ((resource_t *)context)->online; }
static void drain(resource_t *r)
{
    r->small.free += r->pending_blocks;
    r->pending_blocks = 0;
    assert(r->small.free <= r->small.total);
}
static void delay(void *context, unsigned milliseconds)
{
    resource_t *r = context;
    assert(milliseconds == 10);
    if (++r->delays == r->drain_after) drain(r);
}

int main(void)
{
    // A large-pool surplus cannot hide an empty or nearly empty selected pool.
    resource_t r = fixture(0);
    assert(!send(&r, &r.expected));
    assert(r.allocations == 1 && r.reads == 0 && r.releases == 0 && r.submissions == 0);
    assert(r.small.free == 0 && r.large.free == 24);
    r = fixture(BRIDGE_NOTIFY_ATT_RESERVE + BRIDGE_NOTIFY_HEADER_BLOCKS);
    assert(!send(&r, &r.expected));
    assert(r.small.free == 7 && r.large.free == 24 && r.releases == 1 && r.submissions == 0);

    // The boundary includes the second mbuf allocated inside notify_custom.
    r = fixture(BRIDGE_NOTIFY_ATT_RESERVE + BRIDGE_NOTIFY_HEADER_BLOCKS + 1);
    assert(send(&r, &r.expected));
    assert(r.small.free == BRIDGE_NOTIFY_ATT_RESERVE && r.pending_blocks == 2);
    assert(r.releases == 0 && r.submissions == 1 && !r.payload.owned);
    drain(&r);
    assert(r.small.free == 8);

    // A 12-block pool bounds the backlog to three complete notifications,
    // leaving six blocks available even when the controller stops draining.
    r = fixture(12);
    for (unsigned i = 0; i < 3; i++) assert(send(&r, &r.expected));
    for (unsigned i = 0; i < 50; i++) assert(!send(&r, &r.expected));
    assert(r.small.free == 6 && r.pending_blocks == 6 && r.large.free == 24);
    r.small.free -= 2; // A GATT write request and its ATT response can allocate.
    assert(!send(&r, &r.expected));
    assert(r.small.free == 4);
    r.small.free += 2;
    drain(&r);
    assert(r.small.free == 12 && send(&r, &r.expected));

    // Raw, periodic control, and explicit ACKs all use the same reserve.
    for (unsigned type = 1; type <= 13; type++) {
        r = fixture(7);
        r.expected.bytes[1] = type;
        wire_put_u32(r.expected.bytes + 16, 123);
        assert(!send(&r, &r.expected));
        assert(r.submissions == 0 && r.releases == 1 && r.small.free == 7);
    }
    // Failed submission still consumes its mbuf; no double free or leak.
    r = fixture(12);
    r.fail_submit = true;
    assert(!send(&r, &r.expected));
    assert(r.submissions == 1 && r.releases == 0 && r.small.free == 12 && !r.payload.owned);

    // An ACK waits for real capacity, then succeeds without using ATT reserve.
    r = fixture(12);
    wire_put_u32(r.expected.bytes + 16, 1001);
    assert(bridge_packet_is_ack(&r.expected));
    for (unsigned i = 0; i < 3; i++) assert(send(&r, &r.expected));
    r.sends = 0;
    r.drain_after = 2;
    assert(bridge_deliver_ack(&r.expected, connected, send, delay, &r));
    assert(r.sends == 3 && r.delays == 2 && r.small.free == 10 && r.pending_blocks == 2);
    // A permanently constrained pool obeys the existing eight-attempt bound.
    r = fixture(7);
    assert(!bridge_deliver_ack(&r.expected, connected, send, delay, &r));
    assert(r.sends == 8 && r.delays == 7 && r.submissions == 0 && r.small.free == 7);
    assert(r.releases == 8 && !r.payload.owned);
    puts("PASS: selected-pool reserve, ATT header overhead, bounded backlog, GATT capacity, mbuf ownership and bounded ACK recovery.");
}
