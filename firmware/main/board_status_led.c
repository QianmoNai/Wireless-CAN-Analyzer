#include "board_status_led.h"
#include "sdkconfig.h"

#if CONFIG_IDF_TARGET_ESP32S3 && CONFIG_CAN_STATUS_LED
#include "driver/gpio.h"
#include "esp_log.h"

enum { PULSE_MS = 50, MIN_ON_MS = 150 };
static const char *TAG = "status_led";
static bool ready, sampled, linked, output, pulsing, pulsed, activity_pending;
static uint32_t previous_rx, previous_tx, pulse_started, bright_started;

static void disable_led(void)
{
    ready = false;
    // Failure of this optional indicator must not stop CAN or either radio.
    gpio_set_level(GPIO_NUM_3, 0);
    ESP_LOGE(TAG, "GPIO3 indicator disabled after GPIO error");
}

void board_status_led_init(void)
{
    ready = sampled = linked = output = pulsing = pulsed = activity_pending = false;
    // Set the latch low before enabling output, matching the external 10k pull-down.
    if (gpio_set_level(GPIO_NUM_3, 0) != ESP_OK ||
        gpio_set_direction(GPIO_NUM_3, GPIO_MODE_OUTPUT) != ESP_OK) {
        disable_led();
        return;
    }
    ready = true;
}

void board_status_led_update(bool connected, uint32_t rx_count,
                             uint32_t tx_count, uint32_t now_ms)
{
    if (!ready) return;
    bool activity = sampled && (rx_count != previous_rx || tx_count != previous_tx);
    previous_rx = rx_count;
    previous_tx = tx_count;
    if (!sampled || connected != linked) {
        // Never replay traffic seen before a connection (or after disconnect).
        pulsing = pulsed = activity_pending = false;
        activity = false;
    }
    sampled = true;
    linked = connected;
    if (!connected) {
        pulsing = pulsed = activity_pending = false;
    } else {
        activity_pending |= activity;
        if (pulsing && now_ms - pulse_started >= PULSE_MS) {
            pulsing = false;
            bright_started = now_ms;
        }
        if (!pulsing && activity_pending && (!pulsed || now_ms - bright_started >= MIN_ON_MS)) {
            pulsing = pulsed = true;
            activity_pending = false;
            pulse_started = now_ms;
        }
    }
    bool level = connected && !pulsing;
    if (level != output) {
        if (gpio_set_level(GPIO_NUM_3, level) != ESP_OK) {
            disable_led();
            return;
        }
        output = level;
    }
}
#else
// GPIO3 is UART RX on the older ESP32 target; leave it entirely untouched.
void board_status_led_init(void) {}
void board_status_led_update(bool connected, uint32_t rx_count,
                             uint32_t tx_count, uint32_t now_ms)
{
    (void)connected; (void)rx_count; (void)tx_count; (void)now_ms;
}
#endif
