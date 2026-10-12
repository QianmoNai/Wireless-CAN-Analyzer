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
void dual_link_button(int button);
void dual_link_view(dual_view_t *out);
bool dual_link_connected(void);
void dual_link_can_rx(const uint8_t packet[20]);
bool dual_link_can_take(uint8_t packet[20]);
uint32_t dual_link_dropped(void);
