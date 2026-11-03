#include "ack_delivery.h"

bool bridge_packet_is_ack(const bridge_packet_t *packet)
{
    uint8_t type = packet->bytes[1];
    if (type == 5 || type == 8 || type == 10 || type == 20 || type == 22)
        return wire_u32(packet->bytes + 16) != 0;
    return type == 7 && packet->bytes[2] != 0;
}

bool bridge_packet_is_raw(const bridge_packet_t *packet)
{
    uint8_t type=packet->bytes[1];
    // Query completions are ordinary telemetry. Explicit send tokens retain
    // their previous control priority; management ACKs use a separate queue.
    if(type==2||type==3)return wire_u32(packet->bytes+16)==0;
    return type==1||type==11||type==13||type==19;
}

bool bridge_deliver_ack(const bridge_packet_t *packet,
                        bool (*connected)(void *),
                        bool (*send)(void *, const bridge_packet_t *),
                        void (*delay)(void *, unsigned), void *context)
{
    // Eight attempts/70ms of backoff bounds the work per ACK. An entire
    // 16-entry queue remains bounded below the App's two-second ACK deadline.
    for (unsigned attempt = 0; attempt < 8; attempt++) {
        if (!connected(context)) return false;
        if (send(context, packet)) return true;
        if (attempt < 7) delay(context, 10);
    }
    return false;
}
