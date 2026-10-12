#pragma once
#include "board_ui_model.h"
#include "dual_view.h"
#define UI_WIDTH 172
#define UI_HEIGHT 320
#define UI_BAND_HEIGHT 16
#define UI_DMA_PIXELS (UI_WIDTH * UI_BAND_HEIGHT)
typedef struct {
    uint16_t width, height, x_gap, y_gap;
    bool swap_xy, mirror_x, mirror_y;
} ui_panel_geometry_t;
static inline int ui_render_width(const ui_model_t *ui) { return (ui->rotation & 1) ? UI_HEIGHT : UI_WIDTH; }
static inline int ui_render_height(const ui_model_t *ui) { return (ui->rotation & 1) ? UI_WIDTH : UI_HEIGHT; }
int ui_render_rows(const ui_model_t *ui, int top);
ui_panel_geometry_t ui_panel_geometry(uint8_t rotation, int x_gap, int y_gap);
typedef struct {
    dual_view_t dual;
    uint32_t bitrate, rx_hz, tx_hz, failed, pairing_code;
    char ip[16];
    uint8_t wifi_state;
    bool pairing;
    uint16_t load;
    uint8_t state;
    bool connected, rates_valid, load_valid;
} ui_snapshot_t;
// Animation coordinates are independent of the logical state used by buttons.
// page/theme: 0..256, selection coordinates: screen pixels.
typedef struct { int page, selection_x, selection_y, theme; } ui_render_pose_t;
typedef struct { int x,y,width,height; } ui_rect_t;
ui_rect_t ui_setting_rect(const ui_model_t *ui, unsigned setting);
ui_render_pose_t ui_render_rest(const ui_model_t *ui);
uint32_t ui_render_all_bands(const ui_model_t *ui);
uint32_t ui_render_dirty(const ui_model_t *old_ui, const ui_snapshot_t *old_snapshot,
                         bool old_save_failed, const ui_render_pose_t *old_pose,
                         const ui_model_t *ui, const ui_snapshot_t *snapshot,
                         bool save_failed, const ui_render_pose_t *pose);
void ui_snapshot_packet(ui_snapshot_t *snapshot, const uint8_t packet[20]);
// Native-endian RGB565. At most UI_DMA_PIXELS are written: 16 portrait or 8 landscape rows.
void ui_render_band(uint16_t *pixels, int top, const ui_model_t *ui,
                    const ui_snapshot_t *snapshot, bool save_failed);
void ui_render_animated_band(uint16_t *pixels, int top, const ui_model_t *ui,
                             const ui_snapshot_t *snapshot, bool save_failed,
                             const ui_render_pose_t *pose);
