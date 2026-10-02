#include "can_load.h"
#include <limits.h>

_Static_assert(ATOMIC_INT_LOCK_FREE == 2 && sizeof(unsigned int) == sizeof(uint32_t),
               "CAN load ISR counters must use lock-free 32-bit atomics");

uint32_t can_load_frame_bits(bool extended, bool remote, unsigned dlc)
{
    if (dlc > 8) dlc = 8; // Classic CAN DLC 9..15 still carries at most 8 bytes.
    return (extended ? 67u : 47u) + (remote ? 0u : dlc * 8u);
}

void can_load_rx(can_load_t *load, bool extended, bool remote, unsigned dlc)
{
    atomic_fetch_add_explicit(&load->rx_bits, can_load_frame_bits(extended, remote, dlc),
                              memory_order_relaxed);
}

void can_load_tx_result(can_load_t *load, uint32_t bits, bool successful)
{
    if (successful)
        atomic_fetch_add_explicit(&load->tx_bits, bits, memory_order_relaxed);
}

void can_load_reset(can_load_t *load, uint32_t bitrate, int64_t now_us)
{
    load->previous_rx = atomic_load_explicit(&load->rx_bits, memory_order_relaxed);
    load->previous_tx = atomic_load_explicit(&load->tx_bits, memory_order_relaxed);
    load->bitrate = bitrate;
    load->started_us = now_us;
}

bool can_load_snapshot(can_load_t *load, bridge_packet_t *packet, int64_t now_us)
{
    if (now_us <= load->started_us || !load->bitrate || load->bitrate > 1000000) return false;
    uint64_t elapsed = (uint64_t)(now_us - load->started_us);
    if (elapsed < 1000000) return false;
    // A multi-hour stalled window cannot fit the wire duration. Start a fresh
    // window instead of wrapping its denominator into a misleading percentage.
    if (elapsed > UINT32_MAX) {
        can_load_reset(load, load->bitrate, now_us);
        return false;
    }
    uint32_t rx = atomic_load_explicit(&load->rx_bits, memory_order_relaxed);
    uint32_t tx = atomic_load_explicit(&load->tx_bits, memory_order_relaxed);
    uint32_t rx_bits = rx - load->previous_rx;
    uint32_t tx_bits = tx - load->previous_tx;
    load->previous_rx = rx;
    load->previous_tx = tx;
    load->started_us = now_us;

    uint64_t busy = ((uint64_t)rx_bits + tx_bits) * UINT64_C(1000000);
    uint64_t capacity = elapsed * load->bitrate;
    uint16_t percent = 10000;
    if (busy < capacity) {
        percent = 0;
        // Exact floor to 0.01%, without overflowing bits * 10^10. Because
        // Classic CAN is <= 1 Mbit/s, capacity * 10 also fits in uint64_t.
        for (unsigned digit = 0; digit < 4; digit++) {
            busy *= 10;
            percent = percent * 10 + busy / capacity;
            busy %= capacity;
        }
    }
    *packet = (bridge_packet_t){.bytes = {1, 13, percent & 0xff, percent >> 8}};
    wire_put_u32(packet->bytes + 4, rx_bits);
    wire_put_u32(packet->bytes + 8, tx_bits);
    wire_put_u32(packet->bytes + 12, (uint32_t)elapsed);
    wire_put_u32(packet->bytes + 16, load->bitrate);
    return true;
}
