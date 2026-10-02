#include "notify_resource.h"

bool bridge_notify_with_reserve(const bridge_packet_t *packet,
                                const bridge_notify_resource_ops_t *ops,
                                void *context)
{
    void *buffer = ops->allocate(context, packet);
    if (!buffer) return false;
    if (ops->pool_free(context, buffer) <
        BRIDGE_NOTIFY_ATT_RESERVE + BRIDGE_NOTIFY_HEADER_BLOCKS) {
        ops->release(context, buffer);
        return false;
    }
    return ops->submit(context, buffer);
}
