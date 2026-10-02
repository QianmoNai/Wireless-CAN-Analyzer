#include "name_store.h"
#include "nvs.h"
#include <string.h>
void bridge_name_load(char out[BRIDGE_NAME_MAX + 1]) {
    strcpy(out, "ESP32-CAN");
    nvs_handle_t handle;
    if (nvs_open("can_scope", NVS_READONLY, &handle) != ESP_OK) return;
    char value[BRIDGE_NAME_MAX + 1]; size_t len = sizeof(value);
    if (nvs_get_str(handle, "ble_name", value, &len) == ESP_OK && len > 1 &&
        bridge_name_valid((const uint8_t *)value, len - 1)) memcpy(out, value, len);
    nvs_close(handle);
}
esp_err_t bridge_name_save(const char *name) {
    if (!name || !bridge_name_valid((const uint8_t *)name, strlen(name))) return ESP_ERR_INVALID_ARG;
    nvs_handle_t handle;
    esp_err_t err = nvs_open("can_scope", NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;
    char existing[BRIDGE_NAME_MAX + 1]; size_t len = sizeof(existing);
    if (nvs_get_str(handle, "ble_name", existing, &len) != ESP_OK || strcmp(existing, name)) {
        err = nvs_set_str(handle, "ble_name", name);
        if (err == ESP_OK) err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}
