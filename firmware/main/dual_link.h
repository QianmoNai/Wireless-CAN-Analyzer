#pragma once
#include "dual_protocol.h"
#include "dual_view.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
// Wi-Fi manager owns radio start/stop; these synchronize with the dual worker.
bool dual_link_init(void);
void dual_link_request_mode(unsigned mode);
bool dual_link_start(unsigned role);
void dual_link_stop(void);
void dual_link_set_owner(TaskHandle_t owner);
// Physical UART output notification; the B test loopback queue does not use it.
void dual_link_set_uart_owner(TaskHandle_t owner);
void dual_link_button(int button);
void dual_link_view(dual_view_t *out);
// Physical USB management only. Never dispatch this from BLE or LAN commands.
// No session keys are exported. Confirm binds target MAC, fresh host token and SAS.
enum { DUAL_USB_COMMAND=0x1a, DUAL_USB_EVENT=23 };
int dual_link_usb_command(const uint8_t request[20], uint8_t reply[20]);
bool dual_link_connected(void);
void dual_link_can_rx(const uint8_t packet[20]);
bool dual_link_can_take(uint8_t packet[20]);
// Preserve the destination admission age and session for bounded CAN retries.
bool dual_link_can_take_timed(uint8_t packet[20],uint32_t *created,uint32_t *generation);
// Count a terminal physical CAN failure once, after any permitted retry.
void dual_link_can_tx_failed(void);
uint32_t dual_link_dropped(void);

// Data-type changes create a fresh session and discard queued old-type traffic.
void dual_link_set_uart(bool enabled);
uint32_t dual_link_generation(void);
bool dual_link_uart_rx(const uint8_t *data, size_t size);
bool dual_link_uart_take(uint8_t *data, size_t *size);
uint32_t dual_link_uart_dropped(void);
void dual_link_reset_data(void);
