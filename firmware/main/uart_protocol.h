#pragma once
#include "ble_bridge.h"
enum { UART_RX_EVENT=19, UART_TX_EVENT=20, UART_STATUS_EVENT=21, UART_SEND_COMMAND=0x18, UART_WIRE_BYTES=12 };
static inline bool uart_command_valid(const uint8_t p[20]) {
    if(p[0]!=1 || p[1]!=UART_SEND_COMMAND || !p[2] || p[2]>UART_WIRE_BYTES || p[3] || !wire_u32(p+16))return false;
    for(unsigned i=4+p[2];i<16;i++)if(p[i])return false;
    return true;
}
