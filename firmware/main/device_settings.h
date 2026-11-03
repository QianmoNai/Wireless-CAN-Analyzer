#pragma once
#include "settings_protocol.h"

// One owner for the existing local_panel/settings packed NVS value. The LCD
// edits snapshots; compare-and-set prevents stale screen state overwriting a
// concurrently saved remote change. No LCD hardware is required by this owner.
void device_settings_init(void);
uint32_t device_settings_read(ui_model_t *ui);
void device_settings_update(const ui_model_t *ui,uint32_t expected);
bool device_settings_save_failed(void);
// Returns the same ATT admission codes as other bridge commands. A valid
// request always fills reply; result 2 reports a durable-save failure.
int device_settings_command(const uint8_t packet[20],bridge_packet_t *reply);
