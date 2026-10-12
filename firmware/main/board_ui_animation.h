#pragma once
#include "board_ui_render.h"
enum { UI_PAGE_ANIMATION_MS=240, UI_SELECTION_ANIMATION_MS=180, UI_THEME_ANIMATION_MS=280,
       UI_ANIMATION_FRAME_MS=20, UI_IDLE_FRAME_MS=100 };
typedef struct { int from,to; uint32_t started; unsigned duration; } ui_tween_t;
typedef struct { ui_tween_t page,x,y,theme; uint8_t rotation; } ui_animation_t;
void ui_animation_init(ui_animation_t *animation, const ui_model_t *ui);
// Retarget from the current interpolated position; repeated calls do not restart.
void ui_animation_target(ui_animation_t *animation, const ui_model_t *ui, uint32_t now);
// Returns true while a transition is active; sample once per complete frame.
bool ui_animation_sample(const ui_animation_t *animation, uint32_t now, ui_render_pose_t *pose);
