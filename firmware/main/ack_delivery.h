#pragma once
#include "ble_bridge.h"

bool bridge_packet_is_ack(const bridge_packet_t *packet);
bool bridge_packet_is_raw(const bridge_packet_t *packet);
bool bridge_deliver_ack(const bridge_packet_t *packet,
                        bool (*connected)(void *),
                        bool (*send)(void *, const bridge_packet_t *),
                        void (*delay)(void *, unsigned), void *context);
