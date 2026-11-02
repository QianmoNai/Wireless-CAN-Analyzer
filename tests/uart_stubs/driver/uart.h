#pragma once
#include "sim.h"
enum {UART_NUM_1=1,UART_DATA_8_BITS=8,UART_PARITY_DISABLE=0,UART_STOP_BITS_1=1,UART_HW_FLOWCTRL_DISABLE=0,UART_SCLK_DEFAULT=0,UART_PIN_NO_CHANGE=-1};
enum {UART_DATA,UART_FIFO_OVF,UART_BUFFER_FULL,UART_FRAME_ERR,UART_PARITY_ERR};
typedef struct {int baud_rate,data_bits,parity,stop_bits,flow_ctrl,source_clk;} uart_config_t;
typedef struct {int type;size_t size;} uart_event_t;
int uart_param_config(int,const uart_config_t*);
int uart_set_pin(int,int,int,int,int);
int uart_driver_install(int,int,int,int,QueueHandle_t*,int);
int uart_driver_delete(int);
int uart_set_rx_timeout(int,int);
int uart_flush_input(int);
int uart_get_buffered_data_len(int,size_t*);
int uart_read_bytes(int,void*,unsigned,unsigned);
int uart_tx_chars(int,const char*,unsigned);
int uart_wait_tx_done(int,unsigned);
