#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
// Configuration travels only over an authenticated, encrypted BLE characteristic.
enum { NET_STATUS=1, NET_SCAN, NET_BEGIN, NET_DATA, NET_APPLY, NET_CLEAR, NET_SCAN_PAGE };
typedef struct { uint8_t ssid_len, password_len; uint8_t ssid[32], password[64]; } network_credentials_t;
typedef struct { uint8_t transaction, received; bool active; uint32_t started; network_credentials_t credentials; } network_assembly_t;
void network_assembly_reset(network_assembly_t *assembly);
bool network_credentials_valid(const network_credentials_t *credentials);
bool network_assembly_packet(network_assembly_t *assembly, const uint8_t packet[20], uint32_t now, network_credentials_t *result);
