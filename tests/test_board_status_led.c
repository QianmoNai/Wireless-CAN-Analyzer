// Exercise the real driver with an output-state model, without a display,
// radio, FreeRTOS scheduler, or CAN hardware. Optical brightness is not tested.
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "board_status_led.h"

#if CONFIG_CAN_STATUS_LED && CONFIG_IDF_TARGET_ESP32S3

#include "driver/gpio.h"

enum { CALL_LEVEL, CALL_DIRECTION };
typedef struct { unsigned kind, value; } call_t;
static call_t calls[4096];
static unsigned call_count, output_level, errors;
static unsigned fail_level_count, fail_direction_count;
static int fail_level_value;

static void record(unsigned kind, unsigned value)
{
    assert(call_count < sizeof(calls) / sizeof(calls[0]));
    calls[call_count++] = (call_t){kind, value};
}

esp_err_t gpio_set_level(gpio_num_t pin, uint32_t level)
{
    assert(pin == GPIO_NUM_3 && level <= 1);
    record(CALL_LEVEL, level);
    if (fail_level_count && (fail_level_value < 0 || level == (unsigned)fail_level_value)) {
        fail_level_count--;
        return ESP_FAIL;
    }
    output_level = level;
    return ESP_OK;
}

esp_err_t gpio_set_direction(gpio_num_t pin, gpio_mode_t mode)
{
    assert(pin == GPIO_NUM_3 && mode == GPIO_MODE_OUTPUT);
    record(CALL_DIRECTION, mode);
    if (fail_direction_count) {
        fail_direction_count--;
        return ESP_FAIL;
    }
    return ESP_OK;
}

void status_led_test_log_error(const char *tag, const char *format, ...)
{
    assert(tag && format);
    errors++;
}

static void reset_stub(void)
{
    call_count = errors = fail_level_count = fail_direction_count = 0;
    output_level = 99;
    fail_level_value = -1;
}

static void fresh(void)
{
    reset_stub();
    board_status_led_init();
    // Set the inactive latch before enabling the pin's output driver.
    assert(call_count >= 2);
    assert(calls[0].kind == CALL_LEVEL && calls[0].value == 0);
    assert(calls[1].kind == CALL_DIRECTION && calls[1].value == GPIO_MODE_OUTPUT);
    assert(output_level == 0 && errors == 0);
}

static void update(bool connected, uint32_t rx, uint32_t tx, uint32_t now,
                   unsigned expected_level)
{
    board_status_led_update(connected, rx, tx, now);
    assert(output_level == expected_level);
}

static void test_idle_and_single_activity(void)
{
    fresh();
    update(false, 100, 200, 0, 0);
    update(false, 200, 400, 90, 0); // Unconnected CAN traffic stays dark.
    update(true, 500, 700, 100, 1); // Connection establishes a fresh baseline.
    update(true, 500, 700, 1000, 1);
    update(true, 501, 700, 1010, 0);
    update(true, 501, 700, 1059, 0);
    update(true, 501, 700, 1060, 1); // Exactly 50 ms, including the endpoint.
    update(true, 501, 700, 1210, 1); // No new traffic, no periodic blinking.
    update(true, 501, 701, 1250, 0); // A successful TX has the same indication.
    update(true, 501, 701, 1299, 0);
    update(true, 501, 701, 1300, 1);
    update(true, 501, 701, 1800, 1); // Unchanged successful counts stay steady.
    assert(errors == 0);
}

static void test_pending_activity_is_coalesced(void)
{
    fresh();
    update(true, 0, 0, 1000, 1);
    update(true, 1, 0, 1001, 0);
    update(true, 10, 5, 1020, 0); // More frames must not extend the dark pulse.
    update(true, 10, 5, 1051, 1);
    update(true, 20, 9, 1100, 1); // During cooldown, accumulate one indication.
    update(true, 20, 9, 1200, 1);
    update(true, 20, 9, 1201, 0);
    update(true, 20, 9, 1250, 0);
    update(true, 20, 9, 1251, 1);
    update(true, 20, 9, 1401, 1); // Pending frames were consumed once, not forever.
    update(true, 20, 9, 1800, 1);
    assert(errors == 0);
}

static void test_sustained_traffic_remains_visible(void)
{
    fresh();
    update(true, 0, 0, 1000, 1);
    unsigned dark = 0, bright = 0;
    for (uint32_t now = 1001; now <= 2000; now++) {
        unsigned expected = (now - 1001) % 200 < 50 ? 0 : 1;
        update(true, now - 1000, (now - 1000) / 2, now, expected);
        if (output_level) bright++;
        else dark++;
    }
    // 1 kHz traffic still leaves 150 ms of light between 50 ms dark pulses.
    assert(dark == 250 && bright == 750 && errors == 0);
}

static void test_delayed_update_preserves_the_bright_interval(void)
{
    fresh();
    update(true, 0, 0, 1000, 1);
    update(true, 1, 0, 1001, 0);
    update(true, 2, 0, 1020, 0);
    // A delayed owner cannot physically end the pulse until this update.
    update(true, 2, 0, 1200, 1);
    update(true, 2, 0, 1201, 1);
    update(true, 2, 0, 1349, 1);
    update(true, 2, 0, 1350, 0); // Keep the LED visibly bright for 150 real ms.
    update(true, 2, 0, 1400, 1);
    update(true, 2, 0, 1600, 1);
    assert(errors == 0);
}

static void test_disconnect_and_reconnect_discard_old_activity(void)
{
    fresh();
    update(true, 100, 200, 1000, 1);
    update(true, 101, 200, 1001, 0);
    update(true, 102, 201, 1020, 0);
    update(false, 102, 201, 1030, 0); // Disconnect wins over pulse/pending state.
    update(false, 900, 1000, 1500, 0);
    update(true, 5000, 9000, 2000, 1);
    update(true, 5000, 9000, 2200, 1);
    update(true, 5001, 9000, 2201, 0);
    update(true, 5001, 9000, 2251, 1);
    assert(errors == 0);
}

static void test_uint32_wraparound(void)
{
    fresh();
    const uint32_t connected_at = UINT32_MAX - 40;
    const uint32_t pulse_at = UINT32_MAX - 30;
    update(true, UINT32_MAX, UINT32_MAX, connected_at, 1);
    update(true, 0, UINT32_MAX, pulse_at, 0); // RX wraps; it is still new traffic.
    update(true, 0, 0, pulse_at + 10u, 0); // TX wraps independently.
    update(true, 0, 0, pulse_at + 49u, 0);
    update(true, 0, 0, pulse_at + 50u, 1);
    update(true, 0, 0, pulse_at + 199u, 1);
    update(true, 0, 0, pulse_at + 200u, 0);
    update(true, 0, 0, pulse_at + 250u, 1);
    update(true, 0, 0, pulse_at + 400u, 1);
    assert(errors == 0);
}

static void disabled_until_reinit(uint32_t now)
{
    unsigned previous_calls = call_count;
    board_status_led_update(true, 1000, 1000, now);
    board_status_led_update(false, 1001, 1001, now + 100);
    board_status_led_update(true, 2000, 2000, now + 200);
    assert(call_count == previous_calls && errors > 0);
    fail_level_count = fail_direction_count = 0;
    board_status_led_init();
    assert(output_level == 0);
    update(true, 2000, 2000, now + 300, 1);
    update(true, 2001, 2000, now + 301, 0);
    update(true, 2001, 2000, now + 351, 1);
}

static void test_driver_failures(void)
{
    reset_stub();
    fail_level_value = 0;
    fail_level_count = 1;
    board_status_led_init();
    assert(output_level == 0); // Failure cleanup retried the inactive level.
    disabled_until_reinit(1000);

    reset_stub();
    fail_direction_count = 1;
    board_status_led_init();
    assert(output_level == 0);
    disabled_until_reinit(2000);

    fresh();
    fail_level_value = 1;
    fail_level_count = 1;
    update(true, 1, 1, 3000, 0); // Failed connected-on write disables the output.
    disabled_until_reinit(3100);

    fresh();
    update(true, 1, 1, 4000, 1);
    fail_level_value = 0;
    fail_level_count = 1;
    update(true, 2, 1, 4001, 0); // Failed activity write retries low, then disables.
    disabled_until_reinit(4100);

    fresh();
    update(true, 1, 1, 5000, 1);
    update(true, 2, 1, 5001, 0);
    fail_level_value = 1;
    fail_level_count = 1;
    update(true, 2, 1, 5051, 0); // Failed recovery-to-on must not be cached as on.
    disabled_until_reinit(5100);

    fresh();
    update(true, 1, 1, 6000, 1);
    fail_level_value = 0;
    fail_level_count = 2; // Both the requested low and best-effort cleanup fail.
    unsigned previous_calls = call_count;
    board_status_led_update(false, 1, 1, 6001);
    assert(call_count >= previous_calls + 2);
    // Software cannot guarantee a physical low if both hardware writes fail.
    disabled_until_reinit(6100);
}

int main(void)
{
    test_idle_and_single_activity();
    test_pending_activity_is_coalesced();
    test_sustained_traffic_remains_visible();
    test_delayed_update_preserves_the_bright_interval();
    test_disconnect_and_reconnect_discard_old_activity();
    test_uint32_wraparound();
    test_driver_failures();
    puts("status LED: baseline, RX/TX, visible high-rate pulses, pending, disconnect, wraparound, GPIO recovery PASS");
    return 0;
}

#else

// This branch defines no GPIO functions. A disabled/legacy implementation that
// still touches a pin will therefore fail to link instead of silently passing.
int main(void)
{
    board_status_led_init();
    board_status_led_update(false, 0, 0, 0);
    board_status_led_update(true, 1, 1, 1);
    board_status_led_update(true, UINT32_MAX, UINT32_MAX, UINT32_MAX);
    board_status_led_update(false, 0, 0, 0);
    puts("status LED: disabled/legacy target performs no GPIO operations PASS");
    return 0;
}

#endif
