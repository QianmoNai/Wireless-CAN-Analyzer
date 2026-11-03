#pragma once

#ifndef CAN_WIFI_TLS_BATCH
#define CAN_WIFI_TLS_BATCH 16
#endif

#if CAN_WIFI_TLS_BATCH != 16 && CAN_WIFI_TLS_BATCH != 32 && CAN_WIFI_TLS_BATCH != 64
#error "CAN_WIFI_TLS_BATCH must be 16, 32, or 64"
#endif
