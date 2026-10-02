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
    *ui = (ui_model_t){.brightness = 60, .sound = true};
    if ((stored >> 16) != 1 || (stored & 255) < 10 || (stored & 255) > 100 ||
        (stored & 0xfc00)) return;
    ui->brightness = stored & 255;
    ui->sound = (stored & 0x100) != 0;
    ui->rotated = (stored & 0x200) != 0;
}
uint32_t ui_model_settings(const ui_model_t *ui)
{
    return 0x10000u | ui->brightness | (ui->sound ? 0x100u : 0) | (ui->rotated ? 0x200u : 0);
}
void ui_model_key(ui_model_t *ui, unsigned key, ui_key_event_t event)
{
    if (key > 2 || event == KEY_NONE) return;
    if (event == KEY_LONG) {
        if (key == 2) {
            if (ui->editing) ui->editing = false;
            else { ui->page = UI_OVERVIEW; ui->paused = false; }
        }
        return;
    }
    if (ui->page == UI_SETTINGS) {
        if (key == 2) ui->editing = !ui->editing;
        else if (!ui->editing) ui->selected = (ui->selected + (key == 0 ? 2 : 1)) % 3;
        else if (ui->selected == 0) {
            int value = ui->brightness + (key == 0 ? -10 : 10);
            ui->brightness = value < 10 ? 10 : value > 100 ? 100 : value;
        } else if (ui->selected == 1) ui->sound = !ui->sound;
        else ui->rotated = !ui->rotated;
    } else if (key < 2) {
        ui->page = (ui->page + (key == 0 ? 2 : 1)) % 3;
        ui->paused = false;
    } else if (ui->page == UI_TRAFFIC) ui->paused = !ui->paused;
    else ui->page = UI_TRAFFIC;
}
