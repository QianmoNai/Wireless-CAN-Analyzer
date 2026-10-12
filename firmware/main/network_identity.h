#pragma once
#include <stdbool.h>
#include <stdint.h>
extern char network_cert[1200], network_key[400], network_fingerprint[65];
extern uint8_t network_token[32];
bool network_identity_init(void);
