#include "board_ui_render.h"
#include "board_ui_font.h"
#include <stdio.h>
#include <string.h>
#include <inttypes.h>

// Compact ASCII for IDs/data; Chinese labels use the committed 16x16 subset.
static const char glyph_chars[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.:/%+> ?";
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
 {99,19,8,100,99},{8,8,62,8,8},{0,65,34,20,8},{0,0,0,0,0},{2,1,81,9,6}
};
enum { TEXT=0xef7d, MUTED=0x8cb3, RED=0xfacb };
_Static_assert(sizeof(glyphs)/sizeof(glyphs[0]) == sizeof(glyph_chars)-1, "ASCII glyph count");
typedef struct { uint16_t *pixels; int top, width, rows, offset; uint16_t bg,card,line,accent; } canvas_t;

ui_rect_t ui_setting_rect(const ui_model_t *ui, unsigned setting)
{
    bool landscape=(ui->rotation&1)!=0;
    int visible=(int)setting-(int)ui_setting_first(ui);
    return (ui_rect_t){.x=landscape?12+(visible%2)*152:12,
        .y=landscape?37+(visible/2)*30:68+visible*(ui_setting_count(ui)>5?35:36),
        .width=landscape?144:148,.height=landscape?26:34};
}
ui_render_pose_t ui_render_rest(const ui_model_t *ui)
{
    ui_rect_t r=ui_setting_rect(ui,ui->selected);
    return (ui_render_pose_t){.page=ui->page*256,.selection_x=r.x,.selection_y=r.y,.theme=ui->wifi?256:0};
}
uint32_t ui_render_all_bands(const ui_model_t *ui)
{
    int rows=UI_DMA_PIXELS/ui_render_width(ui);
    unsigned bands=(ui_render_height(ui)+rows-1)/rows;
    return (1u<<bands)-1;
}
static uint32_t dirty_rows(const ui_model_t *ui, int top, int bottom)
{
    uint32_t mask=0;
    int rows=UI_DMA_PIXELS/ui_render_width(ui),height=ui_render_height(ui);
    if(top<0)top=0;
    if(bottom>height)bottom=height;
    for(int band=top/rows;band*rows<bottom;band++)mask|=1u<<band;
    return mask;
}
static uint32_t dirty_setting(const ui_model_t *ui, unsigned selected)
{
    ui_rect_t r=ui_setting_rect(ui,selected);
    return dirty_rows(ui,r.y,r.y+r.height);
}
uint32_t ui_render_dirty(const ui_model_t *old, const ui_snapshot_t *before,
                         bool old_save_failed, const ui_render_pose_t *from,
                         const ui_model_t *ui, const ui_snapshot_t *s,
                         bool save_failed, const ui_render_pose_t *to)
{
    if(old->uart!=ui->uart || old->uart_baud!=ui->uart_baud || ui_setting_first(old)!=ui_setting_first(ui) || old->rotation!=ui->rotation || old->usb!=ui->usb || old->wifi!=ui->wifi || old->dual_role!=ui->dual_role || old->link_menu!=ui->link_menu ||
        (ui->link_menu && memcmp(&before->dual,&s->dual,sizeof(s->dual))) ||
        from->page!=to->page || from->theme!=to->theme || before->pairing!=s->pairing ||
        (to->page>0 && to->page<256))return ui_render_all_bands(ui);
    if(s->pairing)return before->pairing_code!=s->pairing_code?ui_render_all_bands(ui):0;
    bool landscape=(ui->rotation&1)!=0;
    uint32_t dirty=0;
    if(ui->wifi && strcmp(before->ip,s->ip))dirty|=dirty_rows(ui,0,landscape?31:62);
    // A newly requested page can still be at its old visual endpoint for the
    // first sample. Avoid retransmitting an identical screen before it moves.
    if(to->page==0){
        if(before->connected!=s->connected || before->dual.bound!=s->dual.bound || before->wifi_state!=s->wifi_state ||
            before->bitrate!=s->bitrate || before->state!=s->state)
            dirty|=dirty_rows(ui,landscape?40:72,landscape?56:110);
        if(before->rx_hz!=s->rx_hz || before->rates_valid!=s->rates_valid)
            dirty|=dirty_rows(ui,landscape?64:120,landscape?118:174);
        if(before->tx_hz!=s->tx_hz || before->rates_valid!=s->rates_valid)
            dirty|=dirty_rows(ui,landscape?64:182,landscape?118:236);
        if(before->load!=s->load || before->load_valid!=s->load_valid)
            dirty|=dirty_rows(ui,landscape?126:248,landscape?142:275);
        if(before->failed!=s->failed)dirty|=dirty_rows(ui,landscape?126:283,landscape?142:299);
        return dirty;
    }
    if(from->selection_x!=to->selection_x || from->selection_y!=to->selection_y || old->editing!=ui->editing){
        int height=ui_setting_rect(ui,0).height;
        dirty|=dirty_rows(ui,from->selection_y,from->selection_y+height);
        dirty|=dirty_rows(ui,to->selection_y,to->selection_y+height);
    }
    if(old->selected!=ui->selected){dirty|=dirty_setting(ui,old->selected);dirty|=dirty_setting(ui,ui->selected);}
    if(old->selected!=ui->selected || old->editing!=ui->editing)
        dirty|=dirty_rows(ui,ui_render_height(ui)-18,ui_render_height(ui));
    if(old->brightness!=ui->brightness)dirty|=dirty_setting(ui,0);
    if(old->sound!=ui->sound)dirty|=dirty_setting(ui,1);
    if(old->volume!=ui->volume)dirty|=dirty_setting(ui,UI_SETTING_SOUND);
    if(old_save_failed!=save_failed){int y=landscape?130:ui_setting_count(ui)>5?282:252;dirty|=dirty_rows(ui,y,y+16);}
    return dirty;
}

int ui_render_rows(const ui_model_t *ui, int top)
{
    int height=ui_render_height(ui), rows=UI_DMA_PIXELS/ui_render_width(ui);
    if (top < 0 || top >= height) return 0;
    return rows < height-top ? rows : height-top;
}

ui_panel_geometry_t ui_panel_geometry(uint8_t rotation, int x_gap, int y_gap)
{
    rotation &= 3;
    bool swap=(rotation & 1)!=0, mx=rotation==UI_ROTATION_90 || rotation==UI_ROTATION_180;
    bool my=rotation==UI_ROTATION_180 || rotation==UI_ROTATION_270;
    // ST7789 RAM is 240x320; preserve the visible window when native axes mirror.
    int column=mx ? 240-UI_WIDTH-x_gap : x_gap;
    int row=my ? 320-UI_HEIGHT-y_gap : y_gap;
    return (ui_panel_geometry_t){.width=swap?UI_HEIGHT:UI_WIDTH,.height=swap?UI_WIDTH:UI_HEIGHT,
        .x_gap=swap?row:column,.y_gap=swap?column:row,.swap_xy=swap,.mirror_x=mx,.mirror_y=my};
}

static void rect(canvas_t *c, int x, int y, int w, int h, uint16_t color)
{
    x+=c->offset;
    int bottom=y+h, right=x+w;
    if (y<c->top) y=c->top;
    if (bottom>c->top+c->rows) bottom=c->top+c->rows;
    int left=c->offset>0?c->offset:0;
    int limit=c->offset<0?c->width+c->offset:c->width;
    if (x<left) x=left;
    if (right>limit) right=limit;
    for (;y<bottom;y++) for (int xx=x;xx<right;xx++) c->pixels[(y-c->top)*c->width+xx]=color;
}

// ASCII can be doubled for values; Chinese stays at a legible fixed 16 pixels.
static int text_width(const char *text, int scale)
{
    int width=0, gap=0; uint32_t codepoint;
    while ((codepoint=ui_utf8_next(&text))) {
        gap=codepoint<128 ? scale : 1;
        width+=codepoint<128 ? 6*scale : 17;
    }
    return width-gap;
}
static void label_limit(canvas_t *c, int x, int y, const char *text, int scale, uint16_t color, int right)
{
    if (y+16<=c->top || y>=c->top+c->rows) return;
    if (right>c->width-8) right=c->width-8;
    uint32_t codepoint;
    while ((codepoint=ui_utf8_next(&text))) {
        int width=codepoint<128 ? 5*scale : 16;
        if (x+width>right) break;
        if (codepoint<128) {
            const char *found=strchr(glyph_chars,(char)codepoint);
            const uint8_t *glyph=glyphs[found ? (size_t)(found-glyph_chars) : sizeof(glyphs)/sizeof(glyphs[0])-1];
            int baseline=y+(16-7*scale)/2;
            for (int xx=0;xx<5;xx++) for (int yy=0;yy<7;yy++)
                if (glyph[xx]&(1u<<yy)) rect(c,x+xx*scale,baseline+yy*scale,scale,scale,color);
            x+=6*scale;
        } else {
            const uint16_t *glyph=ui_cn_glyph(codepoint);
            if (glyph) {
                for (int yy=0;yy<16;yy++) {
                    if (y+yy<c->top || y+yy>=c->top+c->rows) continue;
                    for (int xx=0;xx<16;xx++) if (glyph[yy]&(0x8000u>>xx)) rect(c,x+xx,y+yy,1,1,color);
                }
            } else {
                rect(c,x+2,y+2,12,1,color);rect(c,x+2,y+13,12,1,color);
                rect(c,x+2,y+2,1,12,color);rect(c,x+13,y+2,1,12,color);
            }
            x+=17;
        }
    }
}
static void label(canvas_t *c, int x, int y, const char *text, int scale, uint16_t color)
{
    label_limit(c,x,y,text,scale,color,c->width-8);
}
static void right_label(canvas_t *c, int right, int y, const char *text, int scale, uint16_t color)
{
    label(c,right-text_width(text,scale),y,text,scale,color);
}
static void rate_card(canvas_t *c, int x, int y, int width, bool tx, bool uart, const ui_snapshot_t *s)
{
    char value[16];
    rect(c,x,y,width,54,c->card);
    label(c,x+10,y+6,uart?(tx?"发送 字节/秒":"接收 字节/秒"):(tx?"发送 TX/秒":"接收 RX/秒"),1,MUTED);
    if (s->rates_valid) snprintf(value,sizeof(value),"%"PRIu32,tx?s->tx_hz:s->rx_hz);
    else strcpy(value,"--");
    label_limit(c,x+10,y+32,value,2,tx?TEXT:c->accent,x+width-8);
}
static void format_load(char *line, size_t size, const ui_snapshot_t *s)
{
    if (s->load_valid) snprintf(line,size,"%u.%u%%",s->load/100,(s->load/10)%10);
    else snprintf(line,size,"--");
}
static void key_hints(canvas_t *c, const ui_model_t *ui)
{
    int y=ui_render_height(ui)-18;
    char hint[24];
    bool editing=ui->page==UI_SETTINGS && ui->editing;
    const char *action=ui_setting_id(ui,ui->selected)==UI_SETTING_PAIR&&ui->page==UI_SETTINGS?"进入":ui->page==UI_OVERVIEW?"设置":(ui->selected==1 || ui->selected==3 || ui->selected>=UI_SETTING_DATA)?"切换":ui->editing?"完成":"编辑";
    snprintf(hint,sizeof(hint),"2%s",action);
    label(c,(c->width-text_width(hint,1))/2,y,hint,1,TEXT);
    unsigned previous=ui_key_role(ui->rotation,0)==UI_KEY_PREVIOUS?1:3;
    unsigned next=4-previous;
    bool landscape=(ui->rotation & 1)!=0;
    bool rotating=editing && ui->selected==2;
    int margin=rotating && !landscape?8:12;
    snprintf(hint,sizeof(hint),"%u%s",previous,rotating?(previous==1?"顺时针":"逆时针"):editing?(ui_brightness_step(ui->rotation,previous-1)>0?"加":"减"):landscape?"左":"上");
    label(c,margin,y,hint,1,c->accent);
    snprintf(hint,sizeof(hint),"%u%s",next,rotating?(next==1?"顺时针":"逆时针"):editing?(ui_brightness_step(ui->rotation,next-1)>0?"加":"减"):landscape?"右":"下");
    right_label(c,c->width-margin,y,hint,1,c->accent);
}
static void overview(canvas_t *c, bool landscape, const ui_model_t *ui, const ui_snapshot_t *s)
{
    char line[48];
    const char *state=ui->uart?(s->state==0?"正常":"异常"):s->state==0?"正常":s->state==1?"警告":s->state==2?"被动":s->state==3?"离线":"未知";
    const char *connection=ui->usb?(s->connected?"USB已连接":"USB未连接"):ui->dual_role?(s->connected?"双机已连接":s->dual.bound?"对端离线":"双机未配对"):ui->wifi?(s->wifi_state==2?(s->connected?"局域网已连接":"等待局域网"):s->wifi_state==1?"网络连接中":s->wifi_state==4?"网络连接失败":"等待手机配网"):(s->connected?"蓝牙已连接":"蓝牙未连接");
    label(c,12,landscape?40:72,connection,1,s->connected?c->accent:MUTED);
    if(ui->uart)snprintf(line,sizeof(line),"%"PRIu32" BAUD",s->bitrate);else snprintf(line,sizeof(line),"%"PRIu32" KBIT/S",s->bitrate/1000);
    label(c,landscape?132:12,landscape?40:94,line,1,TEXT);
    right_label(c,c->width-12,landscape?40:94,state,1,s->state?RED:c->accent);
    if (landscape) {
        rate_card(c,12,64,144,false,ui->uart,s);rate_card(c,164,64,144,true,ui->uart,s);
        label(c,12,126,ui->uart?"8N1":"负载估算",1,MUTED);if(ui->uart)strcpy(line,"3.3V TTL");else format_load(line,sizeof(line),s);label(c,88,126,line,1,TEXT);
        if(!ui->uart)rect(c,134,133,62,5,c->line);
        if (!ui->uart && s->load_valid) rect(c,134,133,(62u*s->load)/10000,5,c->accent);
        snprintf(line,sizeof(line),"%s %"PRIu32,ui->uart?"丢弃":"失败",s->failed);label(c,208,126,line,1,s->failed?RED:MUTED);
    } else {
        rate_card(c,12,120,148,false,ui->uart,s);rate_card(c,12,182,148,true,ui->uart,s);
        label(c,12,248,ui->uart?"8N1":"负载估算",1,MUTED);if(ui->uart)strcpy(line,"3.3V TTL");else format_load(line,sizeof(line),s);right_label(c,160,248,line,1,TEXT);
        if(!ui->uart)rect(c,12,270,148,5,c->line);
        if (!ui->uart && s->load_valid) rect(c,12,270,(148u*s->load)/10000,5,c->accent);
        snprintf(line,sizeof(line),"%s %"PRIu32,ui->uart?"丢弃":"失败",s->failed);label(c,12,283,line,1,s->failed?RED:MUTED);
    }
}
static uint16_t blend(uint16_t a, uint16_t b, unsigned weight)
{
    unsigned inv=256-weight;
    return (((((a>>11)&31)*inv+((b>>11)&31)*weight)/256)<<11) |
        (((((a>>5)&63)*inv+((b>>5)&63)*weight)/256)<<5) |
        (((a&31)*inv+(b&31)*weight)/256);
}
static void settings(canvas_t *c, bool landscape, const ui_model_t *ui, bool save_failed,
                     const ui_render_pose_t *pose)
{
    const char *names[]={"亮度","声音","屏幕方向","通信模式","数据选择","串口速率","双机配对"};char value[32];
    const char *volumes[]={"低","中","高"};
    _Static_assert(sizeof(names)/sizeof(names[0])==UI_SETTING_COUNT+2,"setting label count");
    for(unsigned i=ui_setting_first(ui);i<ui_setting_count(ui)&&i<ui_setting_first(ui)+6;i++){
        ui_rect_t r=ui_setting_rect(ui,i);
        rect(c,r.x,r.y,r.width,r.height,c->card);
    }
    ui_rect_t selected=ui_setting_rect(ui,ui->selected);
    rect(c,pose->selection_x,pose->selection_y,selected.width,selected.height,blend(c->card,c->accent,24));
    rect(c,pose->selection_x,pose->selection_y,3,selected.height,ui->editing?TEXT:c->accent);
    for(unsigned i=ui_setting_first(ui);i<ui_setting_count(ui)&&i<ui_setting_first(ui)+6;i++){
        ui_rect_t r=ui_setting_rect(ui,i);
        int x=r.x,y=r.y,width=r.width;
        label(c,x+10,y+(landscape?5:1),names[ui_setting_id(ui,i)],1,MUTED);
        if(i==0)snprintf(value,sizeof(value),"%u%%",ui->brightness);
        else if(i==1)strcpy(value,ui->sound?volumes[ui->volume<UI_VOLUME_COUNT?ui->volume:UI_VOLUME_HIGH]:"关闭");
        else if(i==2)snprintf(value,sizeof(value),"%u度",ui->rotation*90u);
        else if(i==3)strcpy(value,ui->usb?"USB直连":ui->dual_role?(ui->dual_role==1?"双机A端":"双机B端"):ui->wifi?"WIFI":"蓝牙");
        else if(i==UI_SETTING_DATA)strcpy(value,ui->uart?"UART":"CAN");
        else if(ui_setting_id(ui,i)==UI_SETTING_BAUD)snprintf(value,sizeof(value),"%"PRIu32,ui_uart_baud(ui));
        else strcpy(value,"进入");
        if(landscape)right_label(c,x+width-8,y+5,value,1,ui->selected==i?c->accent:TEXT);
        else label(c,x+10,y+17,value,1,ui->selected==i?c->accent:TEXT);
    }
    bool compact = !landscape && ui_setting_count(ui)>5;
    label(c,12,landscape?130:compact?282:252,save_failed?"保存失败":compact?"长按2返回":"自动保存",1,save_failed?RED:MUTED);
    if(landscape)right_label(c,308,130,"长按2返回",1,MUTED);else if(ui_setting_count(ui)<=5)label(c,12,282,"长按2返回",1,MUTED);
}

static void dual_scene(canvas_t *c,const ui_model_t *ui,const ui_snapshot_t *s)
{
    bool wide=(ui->rotation&1)!=0;const dual_view_t *d=&s->dual;char line[48];
    label(c,12,wide?8:14,"双机配对",2,TEXT);
    const char *states[]={"启动中","尚未绑定","对端离线","对端在线","搜索B端","等待A端","正在配对","核对配对码","配对失败","确认解除绑定"};
    label(c,12,wide?40:76,states[d->state<=DUAL_UI_UNBIND?d->state:DUAL_UI_ERROR],1,c->accent);
    if(d->bound||d->count||d->state==DUAL_UI_HANDSHAKE||d->state==DUAL_UI_CHECK){
        snprintf(line,sizeof(line),"%02X:%02X:%02X:%02X:%02X:%02X",d->peer[0],d->peer[1],d->peer[2],d->peer[3],d->peer[4],d->peer[5]);
        label(c,12,wide?62:108,line,1,TEXT);
    }
    const char *hint="中键开始配对";
    if(d->state==DUAL_UI_CHECK){
        snprintf(line,sizeof(line),"%06"PRIu32,d->code);label(c,12,wide?84:148,line,2,TEXT);
        hint=d->confirmed?"等待对端确认":"两端相同按2";
    }else if(d->state==DUAL_UI_SEARCH){
        snprintf(line,sizeof(line),"设备 %u/%u",d->count?d->index+1:0,d->count);label(c,12,wide?88:148,line,1,TEXT);
        hint=d->count?"中键选择设备":"在B端开始配对";
    }else if(d->state==DUAL_UI_WAIT)hint="在A端选择本机";
    else if(d->state==DUAL_UI_HANDSHAKE)hint="等待密钥协商";
    else if(d->state==DUAL_UI_UNBIND)hint="中键确认解除";
    else if(d->bound){
        if(wide){snprintf(line,sizeof(line),"收 %"PRIu32" 发 %"PRIu32,d->received,d->sent);label(c,12,84,line,1,TEXT);}
        else{snprintf(line,sizeof(line),"收 %"PRIu32,d->received);label(c,12,148,line,1,TEXT);snprintf(line,sizeof(line),"发 %"PRIu32,d->sent);label(c,12,178,line,1,TEXT);}
        snprintf(line,sizeof(line),"丢失 %"PRIu32,d->dropped);label(c,12,wide?104:208,line,1,MUTED);
        hint=d->clear_selected?"中键解除绑定":"中键重新配对";
    }
    label(c,12,wide?128:d->bound?252:222,hint,1,MUTED);
    unsigned previous=ui_key_role(ui->rotation,0)==UI_KEY_PREVIOUS?1:3;
    snprintf(line,sizeof(line),"%u%s",previous,wide?"左":"上");label(c,12,ui_render_height(ui)-18,line,1,c->accent);
    label(c,(c->width-text_width("长按2返回",1))/2,ui_render_height(ui)-18,"长按2返回",1,TEXT);
    snprintf(line,sizeof(line),"%u%s",4-previous,wide?"右":"下");right_label(c,c->width-12,ui_render_height(ui)-18,line,1,c->accent);
}

static void scene(canvas_t *c, const ui_model_t *ui, const ui_snapshot_t *s, bool save_failed,
                   const ui_render_pose_t *pose)
{
    bool landscape=(ui->rotation & 1)!=0;
    rect(c,0,c->top,c->width,c->rows,c->bg);
    if(ui->link_menu){dual_scene(c,ui,s);return;}
    if(s->pairing){
        label(c,12,landscape?24:70,"蓝牙配对",2,TEXT);
        label(c,12,landscape?50:108,"在手机输入配对码",1,MUTED);
        char code[16];snprintf(code,sizeof(code),"%06"PRIu32,s->pairing_code);
        label(c,(c->width-text_width(code,2))/2,landscape?82:150,code,2,c->accent);
        label(c,12,landscape?124:210,"配对后设置网络",1,MUTED);return;
    }
    const char *title=ui->page==UI_OVERVIEW?(ui->uart?"UART总览":"CAN总览"):"设置";
    const char *subtitle=ui->usb?"USB直连":ui->dual_role?(ui->dual_role==1?"双机A端":"双机B端"):ui->wifi?(s->ip[0]?s->ip:"WIFI"):"蓝牙";
    label(c,12,landscape?8:14,title,2,TEXT);
    if (landscape) right_label(c,c->width-12,8,subtitle,1,c->accent);
    else label(c,12,39,subtitle,1,c->accent);
    rect(c,12,landscape?30:61,c->width-24,1,c->line);
    if (ui->page==UI_OVERVIEW) overview(c,landscape,ui,s);
    else settings(c,landscape,ui,save_failed,pose);
    key_hints(c,ui);
}
void ui_render_animated_band(uint16_t *pixels, int top, const ui_model_t *ui,
                             const ui_snapshot_t *s, bool save_failed, const ui_render_pose_t *pose)
{
    canvas_t c={.pixels=pixels,.top=top,.width=ui_render_width(ui),.rows=ui_render_rows(ui,top),
        .bg=blend(0x0864,0x1064,pose->theme),.card=blend(0x10e8,0x20c8,pose->theme),
        .line=blend(0x2230,0x41cf,pose->theme),.accent=blend(0x55df,0xc39f,pose->theme)};
    if(!c.rows)return;
    if(ui->usb){c.bg=0x1802;c.card=0x3004;c.line=0x7809;c.accent=0xfaaa;}
    else if(ui->dual_role==1){c.bg=0x0863;c.card=0x10e5;c.line=0x224b;c.accent=0x5f33;}
    else if(ui->dual_role==2){c.bg=0x1081;c.card=0x2102;c.line=0x6306;c.accent=0xff47;}
    if(s->pairing||ui->link_menu){scene(&c,ui,s,save_failed,pose);return;}
    ui_model_t page=*ui;
    if(pose->page<256){
        page.page=UI_OVERVIEW;c.offset=-pose->page*c.width/256;
        scene(&c,&page,s,save_failed,pose);
    }
    if(pose->page>0){
        page.page=UI_SETTINGS;c.offset=c.width-pose->page*c.width/256;
        scene(&c,&page,s,save_failed,pose);
    }
}
void ui_render_band(uint16_t *pixels, int top, const ui_model_t *ui, const ui_snapshot_t *s, bool save_failed)
{
    ui_render_pose_t pose=ui_render_rest(ui);
    ui_render_animated_band(pixels,top,ui,s,save_failed,&pose);
}
