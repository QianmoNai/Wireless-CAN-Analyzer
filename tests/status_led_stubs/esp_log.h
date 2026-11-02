#pragma once

void status_led_test_log_error(const char *tag, const char *format, ...);

#define ESP_LOGE(...) status_led_test_log_error(__VA_ARGS__)
