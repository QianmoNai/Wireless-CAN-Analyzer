#pragma once

// CAN raw mode can include RX and periodic-query TX at the same time. At
// 100 Hz FreeRTOS a 16-frame budget has little margin over 1500 events/s.
// Keep the separately measured UART batch configurable at its existing size.
#define CAN_WIFI_CAN_BATCH 64
#define CAN_WIFI_BATCH_CAPACITY 64

#ifndef CAN_WIFI_TLS_BATCH
#define CAN_WIFI_TLS_BATCH 16
#endif

#if CAN_WIFI_TLS_BATCH != 16 && CAN_WIFI_TLS_BATCH != 32 && CAN_WIFI_TLS_BATCH != 64
#error "CAN_WIFI_TLS_BATCH must be 16, 32, or 64"
#endif
