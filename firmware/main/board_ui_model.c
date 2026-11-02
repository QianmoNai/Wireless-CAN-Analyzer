#include "board_ui_model.h"

ui_key_event_t ui_button_sample(ui_button_t *key, bool down, uint32_t now)
{
    if (down != key->raw) { key->raw = down; key->changed_ms = now; }
    if (down != key->stable && (uint32_t)(now - key->changed_ms) >= 30) {
        key->stable = down;
        if (down) { key->pressed_ms = now; key->long_sent = false; }
        else if (!key->long_sent) return KEY_SHORT;
    }
    if (key->stable && down && !key->long_sent && (uint32_t)(now - key->pressed_ms) >= 650) {
        key->long_sent = true;
        return KEY_LONG;
    }
    return KEY_NONE;
}

void ui_model_init(ui_model_t *ui, uint32_t stored)
{
    *ui = (ui_model_t){.brightness = 60, .sound = true, .volume = UI_VOLUME_HIGH, .uart_baud = UI_UART_BAUD_DEFAULT};
    bool usb_version = (stored >> 24) == 7;
    bool modern = (stored >> 24) == 6 || usb_version;
    unsigned version = modern ? 5 : stored >> 16;
    if (modern && ((stored & (usb_version ? 0x00c00000u : 0x00e00000u)) || ((stored >> 17) & 15) >= UI_UART_BAUD_COUNT)) return;
    if (version < 1 || version > 5 || (stored & 255) < 10 || (stored & 255) > 100 ||
        (stored & (version == 1 ? 0xfc00u : version == 2 ? 0xf800u : version == 3 ? 0xf000u : version == 4 ? 0xc000u : 0)) ||
        (version >= 4 && ((stored >> 12) & 3) >= UI_VOLUME_COUNT) || (version == 5 && ((stored >> 14) & 3) > 2)) return;
    ui->uart = modern && (stored & 0x10000u);
    if (modern) ui->uart_baud = (stored >> 17) & 15;
    ui->wifi = version >= 3 && (stored & 0x800u);
    if (version >= 4) ui->volume = (stored >> 12) & 3;
    if (version == 5) ui->dual_role = (stored >> 14) & 3;
    ui->usb = usb_version && (stored & 0x200000u);
    if (ui->usb) ui->dual_role = 0;
    if (ui->dual_role || ui->usb) ui->wifi = false;
    ui->brightness = stored & 255;
    ui->sound = (stored & 0x100) != 0;
    // v1 stored a 180-degree boolean; do not reinterpret it as 90 degrees.
    ui->rotation = version == 1 ? ((stored & 0x200) ? UI_ROTATION_180 : UI_ROTATION_0) : (stored >> 9) & 3;
}
uint32_t ui_model_settings(const ui_model_t *ui)
{
    return 0x07000000u | (ui->usb ? 0x200000u : 0) | ((uint32_t)ui->uart_baud << 17) | (ui->uart ? 0x10000u : 0) | ((uint32_t)ui->dual_role << 14) | ((uint32_t)ui->volume << 12) | (ui->wifi ? 0x800u : 0) |
        ui->brightness | (ui->sound ? 0x100u : 0) | ((ui->rotation & 3u) << 9);
}
ui_key_role_t ui_key_role(uint8_t rotation, unsigned key)
{
    if (key > 2) return UI_KEY_INVALID;
    // At 270 degrees the upper row is BT3, BT2, BT1 from left to right.
    // At 0/90 degrees BT1 is upper/left; at 180/270 degrees BT3 is.
    if (key == 1) return UI_KEY_ACTION;
    return ((rotation & 3) >= UI_ROTATION_180 ? key == 2 : key == 0)
        ? UI_KEY_PREVIOUS : UI_KEY_NEXT;
}
int ui_brightness_step(uint8_t rotation, unsigned key)
{
    ui_key_role_t role = ui_key_role(rotation, key);
    if (role == UI_KEY_ACTION || role == UI_KEY_INVALID) return 0;
    return role == UI_KEY_PREVIOUS ? 1 : -1;
}
void ui_model_key(ui_model_t *ui, unsigned key, ui_key_event_t event)
{
    if (key > 2 || event == KEY_NONE) return;
    ui_key_role_t role = ui_key_role(ui->rotation, key);
    if (event == KEY_LONG) {
        if (role == UI_KEY_ACTION) {
            if (ui->editing) ui->editing = false;
            else ui->page = UI_OVERVIEW;
        }
        return;
    }
    if (ui->page == UI_SETTINGS) {
        if (role == UI_KEY_ACTION) {
            if (ui->selected == 1) {
                if (!ui->sound) { ui->sound=true;ui->volume=UI_VOLUME_LOW; }
                else if (ui->volume==UI_VOLUME_HIGH) ui->sound=false;
                else ui->volume++;
                ui->editing = false;
            } else if(ui->selected == 3) {
                // Keep transport IDs and stored settings stable; only change menu order.
                // BLE -> Wi-Fi -> USB -> dual A -> dual B -> BLE.
                static const uint8_t next_mode[]={1,4,3,0,2};
                unsigned mode=next_mode[ui_mode(ui)];
                ui->wifi=mode==1;ui->usb=mode==4;ui->dual_role=mode>=2&&mode<=3?mode-1:0;ui->editing=false;
            } else if(ui_setting_id(ui,ui->selected) == UI_SETTING_PAIR && ui->dual_role) { ui->link_menu=true;ui->editing=false; }
            else if(ui->selected == UI_SETTING_DATA) {
                ui->uart = !ui->uart;ui->editing=false;
            } else if(ui_setting_id(ui,ui->selected) == UI_SETTING_BAUD && ui->uart) {
                ui->uart_baud = (ui->uart_baud+1)%UI_UART_BAUD_COUNT;ui->editing=false;
            }
            else ui->editing = !ui->editing;
        }
        else if (!ui->editing) ui->selected = (ui->selected + (role == UI_KEY_PREVIOUS ? ui_setting_count(ui)-1 : 1)) % ui_setting_count(ui);
        else if (ui->selected == 0) {
            int value = ui->brightness + 10 * ui_brightness_step(ui->rotation, key);
            ui->brightness = value < 10 ? 10 : value > 100 ? 100 : value;
        } else if (ui->selected == 2) {
            // Direction editing keeps physical BT1 clockwise and BT3 counterclockwise.
            ui->rotation = (ui->rotation + (key == 0 ? 1 : 3)) & 3;
        }
    } else ui->page = UI_SETTINGS;
}
