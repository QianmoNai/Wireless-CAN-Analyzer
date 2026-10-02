#pragma once
#include "ble_bridge.h"

// In IDF NimBLE, notify_custom allocates an ATT header in addition to our
// payload mbuf. Keep capacity for incoming ATT requests and their responses.
#define BRIDGE_NOTIFY_ATT_RESERVE 6u
#define BRIDGE_NOTIFY_HEADER_BLOCKS 1u

typedef struct {
    void *(*allocate)(void *context, const bridge_packet_t *packet);
    // Read the free count of the allocated mbuf's own pool, not all msys pools.
    unsigned (*pool_free)(void *context, void *buffer);
    void (*release)(void *context, void *buffer);
    // Consumes buffer on both success and failure, like notify_custom.
    bool (*submit)(void *context, void *buffer);
} bridge_notify_resource_ops_t;

bool bridge_notify_with_reserve(const bridge_packet_t *packet,
                                const bridge_notify_resource_ops_t *ops,
                                void *context);
