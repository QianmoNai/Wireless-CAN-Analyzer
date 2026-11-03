#pragma once
#include "board_ui_model.h"
#include "ble_bridge.h"

enum { SETTINGS_COMMAND=0x19, SETTINGS_EVENT=22,
       SETTINGS_NETWORK=1, SETTINGS_DATA=2, SETTINGS_BAUD=4 };

// Parse the complete request before modifying any setting. Unselected fields
// must be zero, so malformed/extended requests cannot silently partly apply.
static inline bool settings_command_valid(const uint8_t p[20]) {
    if(p[0]!=1 || p[1]!=SETTINGS_COMMAND || p[2]>1 || p[6] || p[7] ||
       wire_u32(p+12) || !wire_u32(p+16))return false;
    unsigned mask=p[3];
    if(!p[2])return !mask&&!p[4]&&!p[5]&&!wire_u32(p+8);
    if(!mask || (mask&~7u) || (mask&SETTINGS_NETWORK?p[4]>4:p[4]!=0) ||
       (mask&SETTINGS_DATA?p[5]>1:p[5]!=0))return false;
    uint32_t baud=wire_u32(p+8);
    if(!(mask&SETTINGS_BAUD))return !baud;
    for(unsigned i=0;i<UI_UART_BAUD_COUNT;i++) {
        ui_model_t ui={.uart_baud=i};if(ui_uart_baud(&ui)==baud)return true;
    }
    return false;
}
static inline bool settings_usb_bootstrap(const uint8_t p[20]) {
    return settings_command_valid(p) && (!p[2] || ((p[3]&SETTINGS_NETWORK)&&p[4]==4));
}
static inline void settings_apply(ui_model_t *ui,const uint8_t p[20]) {
    if(p[3]&SETTINGS_NETWORK) {
        ui->wifi=p[4]==1;ui->usb=p[4]==4;ui->dual_role=p[4]>=2&&p[4]<=3?p[4]-1:0;
    }
    if(p[3]&SETTINGS_DATA)ui->uart=p[5];
    if(p[3]&SETTINGS_BAUD)for(unsigned i=0;i<UI_UART_BAUD_COUNT;i++) {
        ui->uart_baud=i;if(ui_uart_baud(ui)==wire_u32(p+8))break;
    }
}
