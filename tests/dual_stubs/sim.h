#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef int BaseType_t;
typedef void *TaskHandle_t;
typedef void *SemaphoreHandle_t;
typedef struct queue *QueueHandle_t;
typedef int esp_err_t;
typedef unsigned nvs_handle_t;
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define pdMS_TO_TICKS(x) (x)
#define ESP_OK 0
#define ESP_ERR_TIMEOUT 1
#define portTICK_PERIOD_MS 10
#define ESP_ERR_NVS_NOT_FOUND 1
#define ESP_MAC_WIFI_STA 0
#define WIFI_IF_STA 0
#define NVS_READONLY 0
#define NVS_READWRITE 1
#define ESP_NOW_SEND_SUCCESS 0
#define ESP_NOW_SEND_FAIL 1
typedef int esp_now_send_status_t;
typedef struct {
  int unused;
} esp_now_send_info_t;
typedef struct {
  const uint8_t *src_addr;
} esp_now_recv_info_t;
typedef struct {
  uint8_t peer_addr[6];
  int channel, ifidx;
  bool encrypt;
} esp_now_peer_info_t;
typedef void (*recv_cb_t)(const esp_now_recv_info_t *, const uint8_t *, int);
typedef void (*send_cb_t)(const esp_now_send_info_t *, esp_now_send_status_t);
QueueHandle_t xQueueCreate(unsigned n, size_t size);
int xQueueSend(QueueHandle_t q, const void *p, unsigned wait);
int xQueueReceive(QueueHandle_t q, void *p, unsigned wait);
int xQueuePeek(QueueHandle_t q, void *p, unsigned wait);
extern void (*sim_queue_after_receive)(QueueHandle_t q);
void xQueueReset(QueueHandle_t q);
unsigned uxQueueMessagesWaiting(QueueHandle_t q);
unsigned uxQueueSpacesAvailable(QueueHandle_t q);
SemaphoreHandle_t xSemaphoreCreateMutex(void);
int xSemaphoreTake(SemaphoreHandle_t s, unsigned wait);
void xSemaphoreGive(SemaphoreHandle_t s);
int xTaskCreate(void (*task)(void *), const char *name, unsigned stack,
                void *arg, unsigned priority, TaskHandle_t *handle);
void xTaskNotifyGive(TaskHandle_t t);
unsigned ulTaskNotifyTake(int clear, unsigned wait);
void vTaskDelay(unsigned ticks);
void taskYIELD(void);
int64_t esp_timer_get_time(void);
void sim_clock_set(uint32_t value);
void esp_read_mac(uint8_t *mac, int type);
int esp_now_init(void);
int esp_now_deinit(void);
int esp_now_register_recv_cb(recv_cb_t cb);
int esp_now_register_send_cb(send_cb_t cb);
bool esp_now_is_peer_exist(const uint8_t *mac);
int esp_now_add_peer(const esp_now_peer_info_t *p);
int esp_now_del_peer(const uint8_t *mac);
int esp_now_send(const uint8_t *mac, const uint8_t *p, size_t n);
int nvs_open(const char *name, int mode, nvs_handle_t *h);
int nvs_get_blob(nvs_handle_t h, const char *key, void *data, size_t *n);
int nvs_set_blob(nvs_handle_t h, const char *key, const void *data, size_t n);
int nvs_erase_key(nvs_handle_t h, const char *key);
int nvs_commit(nvs_handle_t h);
void nvs_close(nvs_handle_t h);
