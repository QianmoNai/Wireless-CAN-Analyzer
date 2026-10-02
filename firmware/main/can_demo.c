#include <inttypes.h>
#include <stdio.h>
#include <stdatomic.h>
#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_twai.h"
#include "esp_twai_onchip.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "ble_bridge.h"
#include "feedback_poll.h"
#include "can_load.h"
#include "can_rates.h"
#include "board_ui.h"
static void publish_local_and_ble(const bridge_packet_t *packet)
{
    board_ui_observe(packet);
    ble_bridge_publish(packet);
}
static TaskHandle_t owner_task;
static void poll_wake(void *arg) { xTaskNotifyGive(owner_task); }

// S3 Mini board: exposed GPIO4/5, outside USB, boot straps and the RGB LED.
#if CONFIG_IDF_TARGET_ESP32S3
#define CAN_TX_GPIO GPIO_NUM_4
#define CAN_RX_GPIO GPIO_NUM_5
#else
#define CAN_TX_GPIO GPIO_NUM_21
#define CAN_RX_GPIO GPIO_NUM_22
#endif
#define CAN_BITRATE 1000000
#define CAN_TX_ID 0x123
static const char *TAG = "can_demo";
static QueueHandle_t rx_queue, tx_done_queue, command_queue, query_command_queue;
static atomic_uint rx_count, rx_dropped;
static can_load_t bus_load;
static _Atomic uint32_t inflight_frame_bits;
static uint32_t tx_count, tx_failed, tx_arbitration;
// Only pure arbitration loss is excluded. Mixed/unknown errors remain failures.
static void count_tx(bool success, uint8_t errors)
{
    if(success)tx_count++;
    else if(errors==POLL_ERR_ARB)tx_arbitration++;
    else tx_failed++;
}
static atomic_uint active_tx_errors;
static atomic_bool tx_inflight;
typedef struct { bool success; uint8_t errors; } tx_result_t;
static bool on_error(twai_node_handle_t node, const twai_error_event_data_t *event, void *ctx)
{
    if(atomic_load(&tx_inflight)) {
        uint8_t flags=(event->err_flags.arb_lost?POLL_ERR_ARB:0)
            |(event->err_flags.ack_err?POLL_ERR_ACK:0)|(event->err_flags.bit_err?POLL_ERR_BIT:0)
            |(event->err_flags.form_err?POLL_ERR_FORM:0)|(event->err_flags.stuff_err?POLL_ERR_STUFF:0);
        atomic_fetch_or(&active_tx_errors,flags);
    }
    return false;
}
typedef struct { uint32_t timestamp; twai_frame_header_t header; uint8_t data[8]; } rx_message_t;

static bool on_rx(twai_node_handle_t node, const twai_rx_done_event_data_t *event, void *ctx)
{
    rx_message_t message = {0};
    twai_frame_t frame = {.buffer = message.data, .buffer_len = sizeof(message.data)};
    BaseType_t wake = pdFALSE;
    if (twai_node_receive_from_isr(node, &frame) == ESP_OK) {
        atomic_fetch_add(&rx_count, 1);
        can_load_rx(&bus_load, frame.header.ide, frame.header.rtr, frame.header.dlc);
        message.header = frame.header;message.timestamp=(uint32_t)(esp_timer_get_time()/1000);
        if (xQueueSendFromISR(rx_queue, &message, &wake) != pdTRUE)
            atomic_fetch_add(&rx_dropped, 1);
    }
    return wake == pdTRUE;
}
static bool on_tx(twai_node_handle_t node, const twai_tx_done_event_data_t *event, void *ctx)
{
    BaseType_t wake = pdFALSE;
    // All TX origins (including firmware-side queries) share this completion.
    // Consume the in-flight estimate exactly once, even if completion is late.
    can_load_tx_result(&bus_load, atomic_exchange(&inflight_frame_bits, 0), event->is_tx_success);
    atomic_store(&tx_inflight,false);
    tx_result_t result={.success=event->is_tx_success,.errors=atomic_exchange(&active_tx_errors,0)};
    xQueueSendFromISR(tx_done_queue, &result, &wake);
    if(owner_task) vTaskNotifyGiveFromISR(owner_task, &wake);
    return wake == pdTRUE;
}
static void rx_task(void *arg)
{
    rx_message_t message;
    int64_t last_log = -100000;
    for (;;) {
        if (xQueueReceive(rx_queue, &message, portMAX_DELAY) != pdTRUE) continue;
        bridge_packet_t packet = {.bytes = {1, 1}};
        packet.bytes[2] = (message.header.ide ? 1 : 0) | (message.header.rtr ? 2 : 0);
        packet.bytes[3] = message.header.dlc;
        wire_put_u32(packet.bytes + 4, message.header.id);
        unsigned len = message.header.rtr ? 0 : message.header.dlc;
        if (len > 8) len = 8;
        memcpy(packet.bytes + 8, message.data, len);
        wire_put_u32(packet.bytes + 16, message.timestamp);
        publish_local_and_ble(&packet);
        // Motor feedback can arrive at 1 kHz; do not let UART logging stall reception.
        int64_t now = esp_timer_get_time();
        if (now - last_log < 100000) continue;
        last_log = now;
        char hex[25] = {0};
        for (unsigned i = 0; i < len; i++) snprintf(hex + i * 3, sizeof(hex) - i * 3, "%02X ", message.data[i]);
        ESP_LOGI(TAG, "RX %s %s ID=0x%" PRIX32 " DLC=%u DATA=[%s]",
                 message.header.ide ? "EXT" : "STD", message.header.rtr ? "RTR" : "DATA",
                 message.header.id, (unsigned)message.header.dlc, hex);
    }
}
static void report_tx(bridge_packet_t *packet, bool success, uint8_t errors, bool log_frame)
{
    packet->bytes[1] = success ? 2 : 3;
    count_tx(success,errors);
    packet->bytes[2]&=3; // Preserve EXT/RTR; never retain stale outcome flags.
    if(!success){
        if(errors==POLL_ERR_ARB)packet->bytes[2]|=0x80; // contention only; hidden by monitor UI
        else packet->bytes[2]|=(errors&(POLL_ERR_ACK|POLL_ERR_BIT|POLL_ERR_FORM|POLL_ERR_STUFF|POLL_ERR_DRIVER))<<1;
        // Unknown/unavailable errors leave reason bits clear. No bus-off
        // reason is inferred: this completion's ISR does not provide one.
    }
    publish_local_and_ble(packet);
    // Firmware queries have no request token and can run at 1 kHz. Publish
    // their real result without adding a per-frame UART logging bottleneck.
    if(log_frame)
        ESP_LOGI(TAG, "TX %s ID=0x%" PRIX32 " request=%" PRIu32,
                 success ? "OK" : "FAILED", wire_u32(packet->bytes + 4), wire_u32(packet->bytes + 16));
}
static void publish_config(uint32_t bitrate, uint8_t result, uint32_t token)
{
    bridge_packet_t features={.bytes={1,15,1,0}}; // protocol extensions: IMU family 2
    ble_bridge_publish(&features);
    bridge_packet_t packet = {.bytes = {1, 5, result, 255}}; // capabilities: bitrate + bounded motor pulse + persistent BLE name
    wire_put_u32(packet.bytes + 4, bitrate);
    wire_put_u32(packet.bytes + 12, 1000); // maximum adjustable detail sampling Hz
    wire_put_u32(packet.bytes + 8, tx_arbitration); // internal diagnostic count, not displayed in App
    wire_put_u32(packet.bytes + 16, token);
    publish_local_and_ble(&packet);
}
static void publish_pulse(uint8_t stage, const bridge_packet_t *command, uint32_t sent, uint32_t failed)
{
    bridge_packet_t packet = {.bytes = {1, 6, stage, 0}};
    wire_put_u32(packet.bytes + 4, wire_u32(command->bytes + 4));
    wire_put_u32(packet.bytes + 8, sent);
    wire_put_u32(packet.bytes + 12, failed);
    wire_put_u32(packet.bytes + 16, wire_u32(command->bytes + 16));
    ble_bridge_publish(&packet);
}
static esp_err_t set_bitrate(twai_node_handle_t node, uint32_t rate)
{
    // ESP32 and ESP32-S3 APB clock = 80 MHz. 20 time quanta => sample point 80%.
    twai_timing_advanced_config_t timing = {
        .brp = 80000000 / (rate * 20), .tseg_1 = 15, .tseg_2 = 4, .sjw = 3,
    };
    esp_err_t err = twai_node_disable(node);
    if (err != ESP_OK) return err; // refuse changing a bus-off/recovering node
    err = twai_node_reconfig_timing(node, &timing, NULL);
    esp_err_t enabled = twai_node_enable(node);
    return err == ESP_OK ? enabled : err;
}
void app_main(void)
{
    owner_task=xTaskGetCurrentTaskHandle();
    esp_timer_handle_t poll_timer;
    esp_timer_create_args_t timer_args={.callback=poll_wake,.name="dm_poll",.skip_unhandled_events=true};
    ESP_ERROR_CHECK(esp_timer_create(&timer_args,&poll_timer));
    bool timer_running=false;
    feedback_poll_t polls={0};uint32_t active_query=0;int64_t last_poll_report=esp_timer_get_time();
    rx_queue = xQueueCreate(64, sizeof(rx_message_t));
    tx_done_queue = xQueueCreate(1, sizeof(tx_result_t));
    command_queue = xQueueCreate(16, sizeof(bridge_packet_t));
    query_command_queue = xQueueCreate(16, sizeof(bridge_packet_t));
    ESP_ERROR_CHECK(rx_queue && tx_done_queue && command_queue && query_command_queue ? ESP_OK : ESP_ERR_NO_MEM);
    ble_bridge_init(command_queue, query_command_queue);
    twai_node_handle_t node;
    twai_onchip_node_config_t config = {
        .io_cfg = {.tx = CAN_TX_GPIO, .rx = CAN_RX_GPIO,
                   .quanta_clk_out = GPIO_NUM_NC, .bus_off_indicator = GPIO_NUM_NC},
        .clk_src = TWAI_CLK_SRC_APB,
        // Use hardware single-shot on both targets; poll_result bounds retries in software.
        .bit_timing.bitrate = CAN_BITRATE, .fail_retry_cnt = 0, .tx_queue_depth = 1,
    };
    ESP_ERROR_CHECK(twai_new_node_onchip(&config, &node));
    twai_event_callbacks_t callbacks = {.on_rx_done = on_rx, .on_tx_done = on_tx, .on_error = on_error};
    ESP_ERROR_CHECK(twai_node_register_event_callbacks(node, &callbacks, NULL));
    ESP_ERROR_CHECK(xTaskCreate(rx_task, "can_rx", 4096, NULL, 5, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    can_load_reset(&bus_load, CAN_BITRATE, esp_timer_get_time());
    ESP_ERROR_CHECK(twai_node_enable(node));
    board_ui_start();
    ESP_LOGI(TAG, "Classic CAN + motor panel: TX=GPIO%d RX=GPIO%d bitrate=%d", CAN_TX_GPIO, CAN_RX_GPIO, CAN_BITRATE);

    static bridge_packet_t active, pulse_command;
    static twai_frame_t frame;
    uint32_t counter = 0, bitrate = CAN_BITRATE, pulse_sent = 0, pulse_failed = 0;
    bool pending = false, recovering = false, demo = false;
    bool pulse = false, final_frame = false;
    uint8_t ending = 0;
    can_rates_t rates;
    can_rates_reset(&rates,atomic_load(&rx_count),tx_count,bitrate,esp_timer_get_time());
    int64_t next_demo = 0, next_status = 0, pulse_deadline = 0, next_pulse = 0;
    for (;;) {
        int64_t now = esp_timer_get_time() / 1000;
        tx_result_t result;
        if (pending && xQueueReceive(tx_done_queue, &result, 0) == pdTRUE) {
            bool success=result.success;
            pending = false;
            report_tx(&active, success,result.errors,active_query==0);
            if(active_query){poll_result(&polls,active_query,success,result.errors,esp_timer_get_time());active_query=0;}
            if (pulse) {
                if (success) pulse_sent++; else pulse_failed++;
                if (final_frame) {
                    publish_pulse(success ? ending : 5, &pulse_command, pulse_sent, pulse_failed);
                    pulse = false;
                    ble_bridge_set_motor_busy(false);
                } else if (!success) ending = 4;
            }
        }
        if(!ble_bridge_connected())poll_clear(&polls,1);
        poll_expire(&polls,esp_timer_get_time());
        if (ble_bridge_take_stop()) {
            poll_clear(&polls,1);
            xQueueReset(command_queue);
            bridge_packet_t cancelled;
            for(unsigned i=0;i<16&&xQueueReceive(query_command_queue,&cancelled,0)==pdTRUE;i++)
                poll_cancel_command(&cancelled);
            demo = false;
            if (pulse) ending = 3;
            else ble_bridge_set_motor_busy(false);
        }
        twai_node_status_t status;
        ESP_ERROR_CHECK(twai_node_get_info(node, &status, NULL));
        if (status.state == TWAI_ERROR_BUS_OFF) {
            poll_bus_off(&polls);
            if (pulse) ending = 4;
            if (!recovering) {
                ESP_LOGW(TAG, "Bus-off: starting recovery");
                ESP_ERROR_CHECK(twai_node_recover(node));
                recovering = true;
            }
        } else if (recovering) {
            ESP_LOGI(TAG, "Bus recovered");
            recovering = false;
        }
        // Query configuration is management, not a CAN frame. Handle it even
        // while hardware TX waits for an idle bus, preserving active/frame.
        bridge_packet_t query_command;
        for(unsigned i=0;i<16&&xQueueReceive(query_command_queue,&query_command,0)==pdTRUE;i++) {
            if(!ble_bridge_connected()) poll_cancel_command(&query_command);
            else poll_command_available(&polls,&query_command,bitrate,esp_timer_get_time(),!recovering);
        }
        if (pulse) {
            if (!ble_bridge_connected()) ending = 3;
            else if (!ending && now >= pulse_deadline) ending = 2;
        }
        if (!pending) {
            bridge_packet_t command;
            bool have_frame = false;
            if (pulse && (ending || now >= next_pulse)) {
                active = pulse_command;
                active.bytes[1] = 0x10;
                active.bytes[2] = 0; // standard data frames only
                wire_put_u32(active.bytes + 16, 0); // pulse completion uses event 6, not individual frame tokens
                final_frame = ending != 0;
                if (final_frame) {
                    active.bytes[3] = 8;
                    memset(active.bytes + 8, pulse_command.bytes[2] == 0x40 ? 0 : 0xff, 8);
                    if (pulse_command.bytes[2] == 0x80) active.bytes[15] = 0xfd;
                }
                next_pulse = now + 10;
                have_frame = true;
            } else if (!pulse && xQueueReceive(command_queue, &command, 0) == pdTRUE) {
                if (command.bytes[1] == 0x11) {
                    demo = command.bytes[2] != 0;
                    next_status = 0;
                } else if (command.bytes[1] == 0x12) {
                    poll_clear(&polls,1);
                    uint32_t requested = wire_u32(command.bytes + 4);
                    demo = false;
                    esp_err_t err = recovering ? ESP_ERR_INVALID_STATE : set_bitrate(node, requested);
                    if (err == ESP_OK) {
                        bitrate = requested;
                        can_load_reset(&bus_load, bitrate, esp_timer_get_time());
                        can_rates_reset(&rates,atomic_load(&rx_count),tx_count,bitrate,esp_timer_get_time());
                    }
                    publish_config(bitrate, err == ESP_OK ? 0 : 1, wire_u32(command.bytes + 16));
                    ESP_LOGI(TAG, "Bitrate request=%" PRIu32 " result=%s", requested, esp_err_to_name(err));
                    next_status = 0;
                } else if (command.bytes[1] == 0x20) {
                    poll_clear(&polls,1);
                    pulse_command = command;
                    pulse = true;
                    demo = false;
                    final_frame = false;
                    pulse_sent = pulse_failed = 0;
                    ending = 0;
                    pulse_deadline = now + 500;
                    next_pulse = now;
                    publish_pulse(1, &pulse_command, 0, 0);
                } else { active = command; have_frame = true; }
            } else if (!pulse && demo && now >= next_demo && !recovering) {
                active = (bridge_packet_t){.bytes = {1, 0x10, 0, 8}};
                wire_put_u32(active.bytes + 4, CAN_TX_ID);
                wire_put_u32(active.bytes + 8, counter++);
                memcpy(active.bytes + 12, (uint8_t[]){0x11, 0x22, 0x33, 0x44}, 4);
                next_demo = now + 1000;
                have_frame = true;
            }
            if(!have_frame&&!pulse&&!recovering&&ble_bridge_connected()) {
                active_query=poll_next(&polls,esp_timer_get_time());
                if(active_query){
                    active=(bridge_packet_t){.bytes={1,0x10,0,4}};
                    wire_put_u32(active.bytes+4,0x7ff);
                    active.bytes[8]=active_query;active.bytes[9]=active_query>>8;active.bytes[10]=0xcc;
                    have_frame=true;
                }
            }
            if (have_frame) {
                frame = (twai_frame_t){
                    .header = {.id = wire_u32(active.bytes + 4), .dlc = active.bytes[3],
                               .ide = !!(active.bytes[2] & 1), .rtr = !!(active.bytes[2] & 2)},
                    .buffer = active.bytes + 8, .buffer_len = active.bytes[3],
                };
                atomic_store(&active_tx_errors,0);
                atomic_store(&inflight_frame_bits,
                             can_load_frame_bits(frame.header.ide, frame.header.rtr, frame.header.dlc));
                atomic_store(&tx_inflight,true);
                esp_err_t err = twai_node_transmit(node, &frame, 0);
                if (err == ESP_OK) pending = true;
                else {
                    atomic_store(&tx_inflight,false);
                    atomic_store(&inflight_frame_bits,0);
                    report_tx(&active, false,POLL_ERR_DRIVER,active_query==0);
                    if(active_query){poll_result(&polls,active_query,false,POLL_ERR_DRIVER,esp_timer_get_time());active_query=0;}
                    if (pulse) {
                        pulse_failed++;
                        if (final_frame) {
                            publish_pulse(5, &pulse_command, pulse_sent, pulse_failed);
                            pulse = false;
                            ble_bridge_set_motor_busy(false);
                        } else ending = 4;
                    }
                }
            }
        }
        if (now >= next_status) {
            bridge_packet_t packet = {.bytes = {1, 4, status.state, demo}};
            wire_put_u32(packet.bytes + 4, atomic_load(&rx_count));
            wire_put_u32(packet.bytes + 8, tx_count);
            wire_put_u32(packet.bytes + 12, tx_failed);
            wire_put_u32(packet.bytes + 16, ble_bridge_dropped() + atomic_load(&rx_dropped));
            publish_local_and_ble(&packet);
            publish_config(bitrate, 0, 0);
            next_status = now + 1000;
        }
        int64_t report_now=esp_timer_get_time();
        bridge_packet_t load_packet;
        if (can_load_snapshot(&bus_load, &load_packet, report_now)) publish_local_and_ble(&load_packet);
        bridge_packet_t rate_packet;
        if(can_rates_snapshot(&rates,&rate_packet,atomic_load(&rx_count),tx_count,report_now))publish_local_and_ble(&rate_packet);
        if(report_now-last_poll_report>=1000000){poll_report(&polls,report_now-last_poll_report);last_poll_report=report_now;}
        bool need_timer=poll_active(&polls);
        if(need_timer&&!timer_running)ESP_ERROR_CHECK(esp_timer_start_periodic(poll_timer,250));
        if(!need_timer&&timer_running)ESP_ERROR_CHECK(esp_timer_stop(poll_timer));
        timer_running=need_timer;
        ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(10));
    }
}
