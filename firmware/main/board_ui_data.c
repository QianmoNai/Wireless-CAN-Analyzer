#include "board_ui_render.h"
#include <string.h>
static uint32_t u32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
void ui_snapshot_packet(ui_snapshot_t *s, const uint8_t p[20])
{
    if (p[0]!=1) return;
    unsigned type=p[1];
    if (type>=1 && type<=3) {
        if (type==3 && (p[2]&0xfc)==0x80) return; // Pure arbitration is not a failure.
        memmove(s->frames+1,s->frames,(UI_FRAMES-1)*sizeof(ui_frame_t));
        s->frames[0]=(ui_frame_t){.id=u32(p+4),.kind=type,.flags=p[2],.len=p[3]};
        memcpy(s->frames[0].data,p+8,8);
        if (s->frame_count<UI_FRAMES) s->frame_count++;
    } else if (type==4) {
        s->state=p[2]; s->failed=u32(p+12);
    } else if (type==5 && p[2]==0) {
        uint32_t bitrate=u32(p+4);
        if (bitrate!=s->bitrate) { s->rates_valid=false; s->load_valid=false; }
        s->bitrate=bitrate;
    } else if (type==13) {
        s->load=p[2] | (uint16_t)p[3]<<8;
        if (s->load>10000) s->load=10000;
        s->load_valid=true;
    } else if (type==14) {
        uint32_t window=u32(p+12);
        s->rates_valid=window!=0;
        s->rx_hz=window?(uint64_t)u32(p+4)*1000000/window:0;
        s->tx_hz=window?(uint64_t)u32(p+8)*1000000/window:0;
    }
}
