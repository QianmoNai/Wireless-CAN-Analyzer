#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <assert.h>
#define GPIO_NUM_4 4
#define GPIO_NUM_5 5
#define GPIO_NUM_21 21
#define GPIO_NUM_22 22
#define GPIO_NUM_NC -1
#define TWAI_CLK_SRC_APB 1
#define ESP_OK 0
#define ESP_ERR_NO_MEM 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERROR_CHECK(x) assert((x)==ESP_OK)
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(x) (x)
#define TWAI_ERROR_BUS_OFF 3
#define TWAI_ERROR_ACTIVE 0
#define TWAI_ERROR_WARNING 1
#define TWAI_ERROR_PASSIVE 2
typedef int esp_err_t;
typedef int BaseType_t;
typedef struct queue *QueueHandle_t;
typedef void *twai_node_handle_t;
typedef struct {uint32_t id; uint8_t dlc; bool ide,rtr;} twai_frame_header_t;
typedef struct {twai_frame_header_t header;uint8_t *buffer;size_t buffer_len;} twai_frame_t;
typedef struct {} twai_rx_done_event_data_t;
typedef struct {bool is_tx_success;} twai_tx_done_event_data_t;
typedef struct {struct {bool arb_lost,ack_err,bit_err,form_err,stuff_err;} err_flags;} twai_error_event_data_t;
typedef struct {int state;unsigned tx_error_count,rx_error_count;} twai_node_status_t;
typedef struct {uint32_t brp;uint8_t prop_seg,tseg_1,tseg_2,sjw,ssp_offset;} twai_timing_advanced_config_t;
typedef struct {struct {int tx,rx,quanta_clk_out,bus_off_indicator;} io_cfg;int clk_src;struct {uint32_t bitrate;} bit_timing;int fail_retry_cnt,tx_queue_depth;} twai_onchip_node_config_t;
typedef struct {bool (*on_error)(twai_node_handle_t,const twai_error_event_data_t*,void*);bool (*on_rx_done)(twai_node_handle_t,const twai_rx_done_event_data_t*,void*);bool (*on_tx_done)(twai_node_handle_t,const twai_tx_done_event_data_t*,void*);} twai_event_callbacks_t;
QueueHandle_t xQueueCreate(unsigned,size_t);
BaseType_t xQueueSend(QueueHandle_t,const void*,unsigned);
BaseType_t xQueueSendFromISR(QueueHandle_t,const void*,BaseType_t*);
BaseType_t xQueueReceive(QueueHandle_t,void*,unsigned);
void xQueueReset(QueueHandle_t);
BaseType_t xTaskCreate(void (*)(void*),const char*,unsigned,void*,unsigned,void*);
void vTaskDelay(unsigned);
int64_t esp_timer_get_time(void);
const char *esp_err_to_name(int);
int twai_new_node_onchip(const twai_onchip_node_config_t*,twai_node_handle_t*);
int twai_node_register_event_callbacks(twai_node_handle_t,const twai_event_callbacks_t*,void*);
int twai_node_enable(twai_node_handle_t);
int twai_node_disable(twai_node_handle_t);
int twai_node_reconfig_timing(twai_node_handle_t,const twai_timing_advanced_config_t*,const void*);
int twai_node_get_info(twai_node_handle_t,twai_node_status_t*,void*);
int twai_node_recover(twai_node_handle_t);
int twai_node_receive_from_isr(twai_node_handle_t,twai_frame_t*);
int twai_node_transmit(twai_node_handle_t,const twai_frame_t*,int);

typedef void *TaskHandle_t;
typedef void *esp_timer_handle_t;
typedef struct {void (*callback)(void*);const char *name;bool skip_unhandled_events;} esp_timer_create_args_t;
TaskHandle_t xTaskGetCurrentTaskHandle(void);
void vTaskPrioritySet(TaskHandle_t,unsigned);
void xTaskNotifyGive(TaskHandle_t);
void vTaskNotifyGiveFromISR(TaskHandle_t,BaseType_t*);
unsigned ulTaskNotifyTake(BaseType_t,unsigned);
int esp_timer_create(const esp_timer_create_args_t*,esp_timer_handle_t*);
int esp_timer_start_periodic(esp_timer_handle_t,uint64_t);
int esp_timer_stop(esp_timer_handle_t);
