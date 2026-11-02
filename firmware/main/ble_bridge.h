#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

// Wire protocol: fixed 20-byte little-endian packets, see PROTOCOL.md.
typedef struct { uint8_t bytes[20]; } bridge_packet_t;
void ble_bridge_init(QueueHandle_t commands, QueueHandle_t query_commands);
int ble_bridge_command(const uint8_t bytes[20]);
void ble_bridge_reset_session(void);
void ble_bridge_publish(const bridge_packet_t *packet);
uint32_t ble_bridge_dropped(void);
bool ble_bridge_connected(void);
bool ble_bridge_take_stop(void);
void ble_bridge_set_motor_busy(bool busy);
static inline uint32_t wire_u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static inline void wire_put_u32(uint8_t *p, uint32_t value) {
    for (unsigned i = 0; i < 4; i++) p[i] = value >> (i * 8);
}

// UART RX uses the bounded raw queue and reports admission failure in bytes.
bool ble_bridge_uart_publish(const bridge_packet_t *packet);
