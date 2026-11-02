#pragma once
#include <stdbool.h>
#include <stdint.h>

// Active-high GPIO3 LED on the ESP32-S3 board; independent of the LCD task.
void board_status_led_init(void);
// Call from the CAN owner task. Counters must describe actual CAN RX/TX success,
// not commands queued for transmission or packets delivered to an App.
void board_status_led_update(bool connected, uint32_t rx_count,
                             uint32_t tx_count, uint32_t now_ms);
