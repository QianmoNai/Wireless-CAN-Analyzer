#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "board_ui_render.h"
static void put(uint8_t *p,uint32_t n) { for(int i=0;i<4;i++)p[i]=n>>(8*i); }
int main(void)
{
    ui_button_t key={0};
    assert(ui_button_sample(&key,true,0)==KEY_NONE);
    assert(ui_button_sample(&key,false,8)==KEY_NONE);
    assert(ui_button_sample(&key,true,12)==KEY_NONE);
    assert(ui_button_sample(&key,true,41)==KEY_NONE && !key.stable);
    assert(ui_button_sample(&key,true,42)==KEY_NONE && key.stable);
    assert(ui_button_sample(&key,false,100)==KEY_NONE);
    assert(ui_button_sample(&key,false,130)==KEY_SHORT);
    assert(ui_button_sample(&key,false,131)==KEY_NONE);
    assert(ui_button_sample(&key,true,200)==KEY_NONE);
    assert(ui_button_sample(&key,true,230)==KEY_NONE);
    assert(ui_button_sample(&key,true,880)==KEY_LONG);
    assert(ui_button_sample(&key,true,2000)==KEY_NONE);
    assert(ui_button_sample(&key,false,2010)==KEY_NONE);
    assert(ui_button_sample(&key,false,2040)==KEY_NONE); // No short after long.
    key=(ui_button_t){0};
    ui_button_sample(&key,true,UINT32_MAX-10);
    assert(ui_button_sample(&key,true,20)==KEY_NONE && key.stable);
    assert(ui_button_sample(&key,true,670)==KEY_LONG);

    ui_model_t ui; ui_model_init(&ui,0xffffffff);
    assert(ui.brightness==60 && ui.sound && !ui.rotated);
    ui_model_key(&ui,1,KEY_SHORT); assert(ui.page==UI_TRAFFIC);
    ui_model_key(&ui,2,KEY_SHORT); assert(ui.paused);
    ui_model_key(&ui,2,KEY_LONG); assert(ui.page==UI_OVERVIEW && !ui.paused);
    ui_model_key(&ui,0,KEY_SHORT); assert(ui.page==UI_SETTINGS);
    ui_model_key(&ui,2,KEY_SHORT); assert(ui.editing);
    for(int i=0;i<20;i++)ui_model_key(&ui,0,KEY_SHORT);
    assert(ui.brightness==10);
    for(int i=0;i<20;i++)ui_model_key(&ui,1,KEY_SHORT);
    assert(ui.brightness==100);
    ui_model_key(&ui,2,KEY_LONG); assert(!ui.editing && ui.page==UI_SETTINGS);
    ui_model_key(&ui,1,KEY_SHORT); ui_model_key(&ui,2,KEY_SHORT); ui_model_key(&ui,0,KEY_SHORT);
    assert(!ui.sound);
    ui_model_key(&ui,2,KEY_SHORT); ui_model_key(&ui,1,KEY_SHORT);
    ui_model_key(&ui,2,KEY_SHORT); ui_model_key(&ui,1,KEY_SHORT); assert(ui.rotated);
    ui_model_t restored; ui_model_init(&restored,ui_model_settings(&ui));
    assert(restored.brightness==100 && !restored.sound && restored.rotated);
    assert(restored.page==UI_OVERVIEW && !restored.editing);
    ui_model_init(&restored,0x10100); assert(restored.brightness==60); // Invalid zero brightness.

    ui_snapshot_t s={.bitrate=1000000}; uint8_t packet[20]={1,14};
    put(packet+4,150);put(packet+8,75);put(packet+12,1500000);
    ui_snapshot_packet(&s,packet); assert(s.rates_valid && s.rx_hz==100 && s.tx_hz==50);
    put(packet+12,0);ui_snapshot_packet(&s,packet);assert(!s.rates_valid);
    packet[1]=13;packet[2]=0x88;packet[3]=0x13;ui_snapshot_packet(&s,packet);
    assert(s.load==5000 && s.load_valid);
    packet[1]=5;packet[2]=0;put(packet+4,500000);ui_snapshot_packet(&s,packet);
    assert(s.bitrate==500000 && !s.load_valid && !s.rates_valid);
    packet[1]=1;packet[2]=0;packet[3]=15;
    for(int i=0;i<10000;i++){put(packet+4,i);ui_snapshot_packet(&s,packet);}
    assert(s.frame_count==UI_FRAMES && s.frames[0].id==9999 && s.frames[4].id==9995);
    packet[1]=3;packet[2]=0x80;ui_snapshot_packet(&s,packet);assert(s.frames[0].kind==1);
    packet[2]=0x88;ui_snapshot_packet(&s,packet);assert(s.frames[0].kind==3);
    packet[1]=4;packet[2]=3;put(packet+12,42);ui_snapshot_packet(&s,packet);assert(s.state==3 && s.failed==42);

    // Guard every DMA band; malformed classic DLCs and long extended IDs must not overflow.
    uint16_t guarded[UI_WIDTH*UI_BAND_HEIGHT+2];
    s.rates_valid=true;s.load_valid=true;s.rx_hz=UINT32_MAX;s.load=10000;
    for(int page=0;page<3;page++) {
        ui.page=page;
        for(int top=0;top<UI_HEIGHT;top+=UI_BAND_HEIGHT) {
            guarded[0]=0xdead;guarded[UI_WIDTH*UI_BAND_HEIGHT+1]=0xbeef;
            ui_render_band(guarded+1,top,&ui,&s,true);
            assert(guarded[0]==0xdead && guarded[UI_WIDTH*UI_BAND_HEIGHT+1]==0xbeef);
        }
    }
    puts("board UI: debounce, navigation, settings, telemetry, bounds PASS");
}
