#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

// TMB09A03 powered through Q2 on GPIO13; LEDC channel/timer 1 is independent
// of the backlight's channel/timer 0. No sound is emitted during initialization.
esp_err_t board_buzzer_init(void);
esp_err_t board_buzzer_set(bool enabled, uint8_t volume);
void board_buzzer_stop(void);
