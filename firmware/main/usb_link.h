#pragma once
#include "ble_bridge.h"
void usb_link_init(void);
void usb_link_reset(void);
bool usb_link_connected(void);
bool usb_link_send(const bridge_packet_t *packet);
