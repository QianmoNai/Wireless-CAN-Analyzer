#include "board_ui_render.h"
#include <stdio.h>
#include <string.h>
#include <inttypes.h>

// Original compact 5x7 glyphs. Technical labels keep the narrow screen legible.
static const char glyph_chars[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.:/%+> ";
static const uint8_t glyphs[][5] = {
 {62,81,73,69,62},{0,66,127,64,0},{98,81,73,73,70},{34,65,73,73,54},
 {24,20,18,127,16},{39,69,69,69,57},{62,73,73,73,50},{1,113,9,5,3},
 {54,73,73,73,54},{38,73,73,73,62},
 {126,9,9,9,126},{127,73,73,73,54},{62,65,65,65,34},{127,65,65,34,28},
 {127,73,73,73,65},{127,9,9,9,1},{62,65,73,73,122},{127,8,8,8,127},
 {0,65,127,65,0},{32,64,65,63,1},{127,8,20,34,65},{127,64,64,64,64},
 {127,2,12,2,127},{127,4,8,16,127},{62,65,65,65,62},{127,9,9,9,6},
 {62,65,81,33,94},{127,9,25,41,70},{38,73,73,73,50},{1,1,127,1,1},
 {63,64,64,64,63},{31,32,64,32,31},{127,32,24,32,127},{99,20,8,20,99},
 {3,4,120,4,3},{97,81,73,69,67},
 {8,8,8,8,8},{0,96,96,0,0},{0,54,54,0,0},{96,16,8,4,3},
 {99,19,8,100,99},{8,8,62,8,8},{0,65,34,20,8},{0,0,0,0,0}
};
enum { BG=0x0863, CARD=0x10c5, TEXT=0xef7d, MUTED=0x8cb3, TEAL=0x36d6, RED=0xfacb, LINE=0x21a9 };
typedef struct { uint16_t *pixels; int top; } canvas_t;
static void rect(canvas_t *c, int x, int y, int w, int h, uint16_t color)
{
    int bottom=y+h, right=x+w;
    if (y<c->top) y=c->top;
    if (bottom>c->top+UI_BAND_HEIGHT) bottom=c->top+UI_BAND_HEIGHT;
    if (x<0) x=0;
    if (right>UI_WIDTH) right=UI_WIDTH;
    for (;y<bottom;y++) for (int xx=x;xx<right;xx++) c->pixels[(y-c->top)*UI_WIDTH+xx]=color;
}
static void label(canvas_t *c, int x, int y, const char *text, int scale, uint16_t color)
{
    if (y+7*scale<=c->top || y>=c->top+UI_BAND_HEIGHT) return;
    for (;*text && x+5*scale<=UI_WIDTH-8;text++,x+=6*scale) {
        const char *found=strchr(glyph_chars,*text);
        if (!found) continue;
        const uint8_t *glyph=glyphs[found-glyph_chars];
        for (int xx=0;xx<5;xx++) for (int yy=0;yy<7;yy++)
            if (glyph[xx]&(1u<<yy)) rect(c,x+xx*scale,y+yy*scale,scale,scale,color);
    }
}
void ui_render_band(uint16_t *pixels, int top, const ui_model_t *ui, const ui_snapshot_t *s, bool save_failed)
{
    canvas_t c={pixels,top}; char line[48];
    rect(&c,0,top,UI_WIDTH,UI_BAND_HEIGHT,BG);
    const char *title=ui->page==UI_OVERVIEW?"CAN SCOPE":ui->page==UI_TRAFFIC?"CAN FRAMES":"SETTINGS";
    label(&c,12,14,title,2,TEXT);
    label(&c,12,39,ui->page==UI_TRAFFIC?(ui->paused?"PAUSED":"LIVE - LATEST 5"):"CLASSIC CAN",1,TEAL);
    rect(&c,12,55,148,1,LINE);
    if (ui->page==UI_OVERVIEW) {
        label(&c,12,65,s->connected?"BLE CONNECTED":"BLE DISCONNECTED",1,s->connected?TEAL:MUTED);
        snprintf(line,sizeof(line),"%"PRIu32" KBIT/S",s->bitrate/1000);
        label(&c,12,83,line,1,TEXT);
        const char *state=s->state==0?"ACTIVE":s->state==1?"WARNING":s->state==2?"PASSIVE":s->state==3?"BUS OFF":"UNKNOWN";
        label(&c,108,83,state,1,s->state?RED:TEAL);
        for (unsigned i=0;i<2;i++) {
            int y=104+i*62;
            rect(&c,12,y,148,54,CARD);
            label(&c,22,y+9,i?"TX / SEC":"RX / SEC",1,MUTED);
            if (s->rates_valid) snprintf(line,sizeof(line),"%"PRIu32,i?s->tx_hz:s->rx_hz);
            else strcpy(line,"--");
            label(&c,22,y+28,line,2,i?TEXT:TEAL);
        }
        label(&c,12,239,"LOAD EST.",1,MUTED);
        if (s->load_valid) snprintf(line,sizeof(line),"%u.%u%%",s->load/100,(s->load/10)%10);
        else strcpy(line,"--");
        label(&c,100,239,line,1,TEXT);
        rect(&c,12,257,148,5,LINE);
        if (s->load_valid) rect(&c,12,257,(148u*s->load)/10000,5,TEAL);
        snprintf(line,sizeof(line),"FAIL  %"PRIu32,s->failed);
        label(&c,12,278,line,1,s->failed?RED:MUTED);
        label(&c,12,304,"1/2 PAGE   3 FRAMES",1,MUTED);
    } else if (ui->page==UI_TRAFFIC) {
        if (!s->frame_count) {
            label(&c,12,111,"WAITING FOR CAN...",1,MUTED);
            label(&c,12,131,"RX AND TX RESULTS",1,MUTED);
        }
        for (unsigned i=0;i<s->frame_count && i<UI_FRAMES;i++) {
            const ui_frame_t *f=&s->frames[i]; int y=65+i*44;
            rect(&c,12,y,148,39,CARD);
            snprintf(line,sizeof(line),"%s %s %0*"PRIX32" D%u",f->kind==1?"RX":f->kind==2?"TX":"FAIL",
                     (f->flags&1)?"EXT":"STD",(f->flags&1)?8:3,f->id,f->len);
            label(&c,17,y+6,line,1,f->kind==3?RED:TEAL);
            if (f->flags&2) snprintf(line,sizeof(line),"RTR  DLC %u",f->len);
            else {
                line[0]=0;
                for (unsigned j=0;j<f->len && j<8;j++) snprintf(line+j*2,sizeof(line)-j*2,"%02X",f->data[j]);
                if (!f->len) strcpy(line,"EMPTY");
            }
            label(&c,17,y+23,line,1,TEXT);
        }
        label(&c,12,292,ui->paused?"3 RESUME":"3 PAUSE",1,MUTED);
        label(&c,12,306,"HOLD 3 HOME",1,MUTED);
    } else {
        const char *names[]={"BRIGHTNESS","SOUND","ROTATION"};
        for (unsigned i=0;i<3;i++) {
            int y=68+i*58;
            rect(&c,12,y,148,50,CARD);
            if (ui->selected==i) rect(&c,12,y,3,50,ui->editing?TEXT:TEAL);
            label(&c,22,y+9,names[i],1,MUTED);
            if (i==0) snprintf(line,sizeof(line),"%u%%",ui->brightness);
            else strcpy(line,i==1?(ui->sound?"ON":"OFF"):(ui->rotated?"180 DEG":"0 DEG"));
            label(&c,22,y+26,line,2,ui->selected==i?TEAL:TEXT);
        }
        label(&c,12,258,save_failed?"SAVE FAILED":"AUTO SAVE",1,save_failed?RED:MUTED);
        label(&c,12,278,ui->editing?"1/2 CHANGE":"1/2 SELECT",1,TEXT);
        label(&c,12,292,ui->editing?"3 DONE":"3 EDIT",1,TEXT);
        label(&c,12,306,"HOLD 3 BACK",1,MUTED);
    }
}
