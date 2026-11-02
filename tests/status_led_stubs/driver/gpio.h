#pragma once

#include <stdint.h>
#include "esp_err.h"

typedef enum { GPIO_NUM_3 = 3 } gpio_num_t;
typedef enum { GPIO_MODE_OUTPUT = 2 } gpio_mode_t;

esp_err_t gpio_set_level(gpio_num_t pin, uint32_t level);
esp_err_t gpio_set_direction(gpio_num_t pin, gpio_mode_t mode);
