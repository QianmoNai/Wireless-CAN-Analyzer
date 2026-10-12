#include "board_ui_animation.h"

static int sample(const ui_tween_t *t, uint32_t now, bool smooth)
{
    uint32_t elapsed=now-t->started;
    if(!t->duration || elapsed>=t->duration)return t->to;
    int progress=(int)(elapsed*256/t->duration);
    int remaining=256-progress;
    // Whole-page motion is limited by full-screen SPI transfer time. Smooth
    // acceleration avoids spending the first displayed frame far across the
    // screen; smaller menu/theme transitions retain their quicker ease-out.
    int weight=smooth?progress*progress*(768-2*progress)/65536:
        256-remaining*remaining*remaining/65536;
    return t->from+(t->to-t->from)*weight/256;
}
static void target(ui_tween_t *t, int value, unsigned duration, uint32_t now, bool smooth)
{
    if(t->to==value)return;
    int from=sample(t,now,smooth);
    *t=(ui_tween_t){.from=from,.to=value,.started=now,.duration=duration};
}
void ui_animation_init(ui_animation_t *a, const ui_model_t *ui)
{
    ui_render_pose_t p=ui_render_rest(ui);
    *a=(ui_animation_t){.page={.from=p.page,.to=p.page},.x={.from=p.selection_x,.to=p.selection_x},
        .y={.from=p.selection_y,.to=p.selection_y},.theme={.from=p.theme,.to=p.theme},.rotation=ui->rotation};
}
void ui_animation_target(ui_animation_t *a, const ui_model_t *ui, uint32_t now)
{
    // A new panel orientation has different coordinates. Never animate an old
    // coordinate system into a new one; the driver changes it between frames.
    if(a->rotation!=ui->rotation){ui_animation_init(a,ui);return;}
    ui_render_pose_t p=ui_render_rest(ui);
    target(&a->page,p.page,UI_PAGE_ANIMATION_MS,now,true);
    target(&a->x,p.selection_x,UI_SELECTION_ANIMATION_MS,now,false);
    target(&a->y,p.selection_y,UI_SELECTION_ANIMATION_MS,now,false);
    target(&a->theme,p.theme,UI_THEME_ANIMATION_MS,now,false);
}
bool ui_animation_sample(const ui_animation_t *a, uint32_t now, ui_render_pose_t *p)
{
    *p=(ui_render_pose_t){.page=sample(&a->page,now,true),.selection_x=sample(&a->x,now,false),
        .selection_y=sample(&a->y,now,false),.theme=sample(&a->theme,now,false)};
    const ui_tween_t *tweens[]={&a->page,&a->x,&a->y,&a->theme};
    for(unsigned i=0;i<4;i++)
        if(tweens[i]->from!=tweens[i]->to && (uint32_t)(now-tweens[i]->started)<tweens[i]->duration)return true;
    return false;
}
