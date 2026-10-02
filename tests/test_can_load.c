#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include "../firmware/main/can_load.h"

static unsigned percentage(const bridge_packet_t *p)
{
    assert(p->bytes[0] == 1 && p->bytes[1] == 13);
    return p->bytes[2] | p->bytes[3] << 8;
}

static void *rx_producer(void *arg)
{
    for (unsigned i = 0; i < 10000; i++) can_load_rx(arg, false, false, 8);
    return NULL;
}

static void *tx_producer(void *arg)
{
    for (unsigned i = 0; i < 10000; i++) {
        can_load_tx_result(arg, 131, true);
        can_load_tx_result(arg, 131, false);
    }
    return NULL;
}

int main(void)
{
    can_load_t load = {0};
    bridge_packet_t p;
    assert(can_load_frame_bits(false, false, 0) == 47);
    assert(can_load_frame_bits(false, false, 8) == 111);
    assert(can_load_frame_bits(true, false, 8) == 131);
    assert(can_load_frame_bits(false, true, 8) == 47);
    assert(can_load_frame_bits(true, true, 8) == 67);
    assert(can_load_frame_bits(false, false, 15) == 111);

    can_load_reset(&load, 1000000, 0);
    for (unsigned i = 0; i < 1000; i++) can_load_rx(&load, false, false, 8);
    for (unsigned i = 0; i < 500; i++) can_load_tx_result(&load, 79, true);
    can_load_tx_result(&load, 131, false); // Failed/aborted TX contributes no bits.
    assert(!can_load_snapshot(&load, &p, 999999));
    assert(can_load_snapshot(&load, &p, 1000000));
    assert(percentage(&p) == 1505);
    assert(wire_u32(p.bytes + 4) == 111000 && wire_u32(p.bytes + 8) == 39500);
    assert(wire_u32(p.bytes + 12) == 1000000 && wire_u32(p.bytes + 16) == 1000000);
    assert(!can_load_snapshot(&load, &p, 1000000)); // Never repeats the window.
    assert(can_load_snapshot(&load, &p, 2000000) && percentage(&p) == 0);

    can_load_rx(&load, false, true, 8);
    can_load_rx(&load, true, true, 8);
    can_load_rx(&load, true, false, 8);
    can_load_tx_result(&load, 111, true);
    assert(can_load_snapshot(&load, &p, 3500000));
    assert(wire_u32(p.bytes + 4) == 245 && wire_u32(p.bytes + 8) == 111);
    assert(wire_u32(p.bytes + 12) == 1500000 && percentage(&p) == 2);

    // Rate change discards both old-rate counters and the partial old window.
    can_load_rx(&load, false, false, 8);
    can_load_tx_result(&load, 111, true);
    can_load_reset(&load, 125000, 4000000);
    for (unsigned i = 0; i < 100; i++) {
        can_load_rx(&load, false, false, 8);
        can_load_tx_result(&load, 79, true);
    }
    assert(!can_load_snapshot(&load, &p, 4500000));
    assert(can_load_snapshot(&load, &p, 5000000));
    assert(percentage(&p) == 1520 && wire_u32(p.bytes + 16) == 125000);
    assert(wire_u32(p.bytes + 4) == 11100 && wire_u32(p.bytes + 8) == 7900);

    // Cumulative 32-bit counters and ESP timestamps above the 32-bit boundary.
    atomic_store(&load.rx_bits, UINT32_MAX - 99);
    atomic_store(&load.tx_bits, UINT32_MAX - 49);
    int64_t t = (int64_t)UINT32_MAX + 10000;
    can_load_reset(&load, 1000000, t);
    can_load_rx(&load, false, false, 8);
    can_load_rx(&load, false, false, 8);
    can_load_tx_result(&load, 131, true);
    assert(can_load_snapshot(&load, &p, t + 1000000));
    assert(wire_u32(p.bytes + 4) == 222 && wire_u32(p.bytes + 8) == 131);
    assert(percentage(&p) == 3 && wire_u32(p.bytes + 12) == 1000000);

    // Clamp impossible overloads and avoid arithmetic overflow on long windows.
    can_load_reset(&load, 1000000, 0);
    atomic_fetch_add(&load.rx_bits, UINT32_MAX);
    atomic_fetch_add(&load.tx_bits, UINT32_MAX);
    assert(can_load_snapshot(&load, &p, 1000000) && percentage(&p) == 10000);
    can_load_reset(&load, 1000000, 0);
    atomic_fetch_add(&load.rx_bits, 1500000000u);
    atomic_fetch_add(&load.tx_bits, 1500000000u);
    assert(can_load_snapshot(&load, &p, 4000000000LL) && percentage(&p) == 7500);
    can_load_reset(&load, 1000000, 0);
    can_load_rx(&load, false, false, 8);
    assert(!can_load_snapshot(&load, &p, (int64_t)UINT32_MAX + 1));
    assert(can_load_snapshot(&load, &p, (int64_t)UINT32_MAX + 1000001));
    assert(percentage(&p) == 0 && wire_u32(p.bytes + 4) == 0);

    // Concurrent ISR-style producers retain every increment; failures stay out.
    pthread_t threads[6];
    can_load_reset(&load, 1000000, 0);
    for (unsigned i = 0; i < 6; i++)
        assert(!pthread_create(&threads[i], NULL, i < 4 ? rx_producer : tx_producer, &load));
    for (unsigned i = 0; i < 6; i++) assert(!pthread_join(threads[i], NULL));
    assert(can_load_snapshot(&load, &p, 10000000));
    assert(wire_u32(p.bytes + 4) == 4440000 && wire_u32(p.bytes + 8) == 2620000);
    assert(percentage(&p) == 7060);
    puts("PASS: CAN load frame estimates, success-only TX, real windows, rate reset, rollover, saturation and concurrent atomic writers.");
}
