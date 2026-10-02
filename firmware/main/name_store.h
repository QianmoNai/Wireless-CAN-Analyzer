#pragma once
#include "esp_err.h"
#include "bridge_name.h"
void bridge_name_load(char out[BRIDGE_NAME_MAX + 1]);
esp_err_t bridge_name_save(const char *name);
