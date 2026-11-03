#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef struct queue *QueueHandle_t;
typedef void *SemaphoreHandle_t;
typedef void *TaskHandle_t;
#define pdTRUE 1
#define pdPASS 1
#define pdMS_TO_TICKS(x) (x)
#define portMAX_DELAY UINT32_MAX
#define ESP_OK 0
#define ESP_ERR_NO_MEM 1
#define ESP_ERROR_CHECK(x) assert((x)==0)
QueueHandle_t xQueueCreate(unsigned n,size_t size);
int xQueueSend(QueueHandle_t q,const void *p,unsigned wait);
int xQueueReceive(QueueHandle_t q,void *p,unsigned wait);
void xQueueReset(QueueHandle_t q);
unsigned uxQueueMessagesWaiting(QueueHandle_t q);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
int xSemaphoreTake(SemaphoreHandle_t q,unsigned wait);
int xSemaphoreGive(SemaphoreHandle_t q);
int xTaskCreate(void (*f)(void*),const char *name,unsigned stack,void *arg,unsigned priority,void *handle);
void vTaskDelay(unsigned ticks);
void xTaskNotifyGive(TaskHandle_t task);
void vTaskNotifyGiveFromISR(TaskHandle_t task,int *woken);
unsigned ulTaskNotifyTake(int clear,unsigned ticks);
typedef enum {USJ_SELECT_READ_NOTIF,USJ_SELECT_WRITE_NOTIF,USJ_SELECT_ERROR_NOTIF} usj_select_notif_t;
typedef void (*usj_select_notif_callback_t)(usj_select_notif_t,int*);
void usb_serial_jtag_set_select_notif_callback(usj_select_notif_callback_t callback);
int64_t esp_timer_get_time(void);
typedef struct {uint32_t rx_buffer_size,tx_buffer_size;} usb_serial_jtag_driver_config_t;
int usb_serial_jtag_driver_install(const usb_serial_jtag_driver_config_t *cfg);
int usb_serial_jtag_write_bytes(const void *data,size_t n,uint32_t wait);
int usb_serial_jtag_read_bytes(void *data,uint32_t n,uint32_t wait);
bool usb_serial_jtag_is_connected(void);
