#pragma once
#include <stdbool.h>
#include <stdint.h>

enum { UI_OVERVIEW, UI_SETTINGS, UI_PAGE_COUNT };
enum { UI_SETTING_VOLUME = 4, UI_SETTING_PAIR = 5, UI_SETTING_COUNT = 5 };
enum { UI_VOLUME_LOW, UI_VOLUME_MEDIUM, UI_VOLUME_HIGH, UI_VOLUME_COUNT };
enum { UI_ROTATION_0, UI_ROTATION_90, UI_ROTATION_180, UI_ROTATION_270 };
typedef enum { KEY_NONE, KEY_SHORT, KEY_LONG } ui_key_event_t;
typedef enum { UI_KEY_PREVIOUS, UI_KEY_ACTION, UI_KEY_NEXT, UI_KEY_INVALID } ui_key_role_t;
typedef struct {
    bool raw, stable, long_sent;
    uint32_t changed_ms, pressed_ms;
} ui_button_t;
typedef struct {
    uint8_t page, selected, brightness, rotation, volume, dual_role;
    bool sound, editing, wifi, link_menu;
} ui_model_t;
ui_key_event_t ui_button_sample(ui_button_t *key, bool down, uint32_t now);
void ui_model_init(ui_model_t *ui, uint32_t stored);
uint32_t ui_model_settings(const ui_model_t *ui);
// Physical key indices are BT1=0, BT2=1, BT3=2. Navigation roles follow screen
// positions; direction editing uses fixed physical BT1/BT3.
ui_key_role_t ui_key_role(uint8_t rotation, unsigned key);
// Brightness direction: -1/0/+1. The top/left key increases, bottom/right decreases.
int ui_brightness_step(uint8_t rotation, unsigned key);
void ui_model_key(ui_model_t *ui, unsigned key, ui_key_event_t event);

static inline unsigned ui_mode(const ui_model_t *ui) { return ui->dual_role ? ui->dual_role+1u : (unsigned)ui->wifi; }
static inline unsigned ui_setting_count(const ui_model_t *ui) { return UI_SETTING_COUNT+(ui->dual_role?1:0); }
