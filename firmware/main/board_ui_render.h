#pragma once
#include "board_ui_model.h"
#define UI_WIDTH 172
#define UI_HEIGHT 320
#define UI_BAND_HEIGHT 16
#define UI_FRAMES 5
typedef struct {
    uint32_t id;
    uint8_t kind, flags, len, data[8];
} ui_frame_t;
typedef struct {
    uint32_t bitrate, rx_hz, tx_hz, failed;
    uint16_t load;
    uint8_t state, frame_count;
    bool connected, rates_valid, load_valid;
    ui_frame_t frames[UI_FRAMES];
} ui_snapshot_t;
void ui_snapshot_packet(ui_snapshot_t *snapshot, const uint8_t packet[20]);
// Native-endian RGB565; only a 16-row DMA band is required.
void ui_render_band(uint16_t *pixels, int top, const ui_model_t *ui,
                    const ui_snapshot_t *snapshot, bool save_failed);
