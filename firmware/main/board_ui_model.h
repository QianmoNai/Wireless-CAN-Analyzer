#pragma once
#include <stdbool.h>
#include <stdint.h>

enum { UI_OVERVIEW, UI_TRAFFIC, UI_SETTINGS };
typedef enum { KEY_NONE, KEY_SHORT, KEY_LONG } ui_key_event_t;
typedef struct {
    bool raw, stable, long_sent;
    uint32_t changed_ms, pressed_ms;
} ui_button_t;
typedef struct {
    uint8_t page, selected, brightness;
    bool sound, rotated, editing, paused;
} ui_model_t;
ui_key_event_t ui_button_sample(ui_button_t *key, bool down, uint32_t now);
void ui_model_init(ui_model_t *ui, uint32_t stored);
uint32_t ui_model_settings(const ui_model_t *ui);
void ui_model_key(ui_model_t *ui, unsigned key, ui_key_event_t event);
