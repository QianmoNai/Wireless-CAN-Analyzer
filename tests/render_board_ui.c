// Render real firmware drawing code with simulated data, never a hardware capture.
#include <stdio.h>
#include "board_ui_render.h"
int main(int argc,char **argv)
{
    if(argc!=2){fputs("usage: render_board_ui OUTPUT_DIR\n",stderr);return 2;}
    ui_model_t ui;ui_model_init(&ui,0);
    ui_snapshot_t sample={.bitrate=1000000,.rx_hz=1000,.tx_hz=100,.load=1189,.failed=3,
        .rates_valid=true,.load_valid=true,.connected=true};
    const char *suffixes[]={"","-status","-sound","-rotation","-wifi","-pairing","-volume-low","-volume-medium","-volume-high","-brightness","-dual-a","-dual-b","-dual-search","-dual-wait","-dual-check","-dual-online","-dual-unbind","-uart","-uart-settings","-usb-can","-usb-uart"};
    for(int variant=0;variant<21;variant++) for(int angle=0;angle<4;angle++) for(int page=0;page<UI_PAGE_COUNT;page++) {
        ui_snapshot_t s=sample;ui.dual_role=0;ui.link_menu=false;ui.uart=false;ui.usb=false;
        ui.page=page;ui.rotation=angle;ui.selected=variant==2?1:2;ui.editing=(variant==1 || variant==3) && page==UI_SETTINGS;
        ui.sound=variant!=2;ui.wifi=variant>=4;if(variant>=4){ui.selected=3;ui.editing=false;s.wifi_state=2;snprintf(s.ip,sizeof(s.ip),"192.168.1.123");}if(variant==5){s.pairing=true;s.pairing_code=123456;}
        ui.volume=UI_VOLUME_HIGH;
        if(variant>=6 && variant<=8){ui.selected=UI_SETTING_SOUND;ui.volume=variant-6;ui.wifi=variant==7;}
        if(variant==9){ui.selected=0;ui.editing=page==UI_SETTINGS;ui.wifi=false;}
        if(variant>=10 && variant<17){
            ui.wifi=false;ui.dual_role=variant==11||variant==13?2:1;ui.selected=variant<12?3:5;
            ui.editing=false;ui.link_menu=variant>=12&&page==UI_SETTINGS;
            s.dual=(dual_view_t){.state=variant==12?DUAL_UI_SEARCH:variant==13?DUAL_UI_WAIT:variant==14?DUAL_UI_CHECK:variant==16?DUAL_UI_UNBIND:DUAL_UI_ONLINE,
                .bound=variant>=15,.code=123456,.count=variant==12?2:0,.index=0,.peer={0x90,0xda,0x72,0x73,0x3d,0x88},.sent=1234,.received=5678};
        }
        if(variant>=17&&variant<=18){ui.uart=true;ui.dual_role=variant==18?2:0;ui.link_menu=false;ui.editing=false;ui.selected=variant==18?6:4;s.bitrate=115200;s.rx_hz=1024;s.tx_hz=128;s.failed=0;}
        if(variant>=19){ui.usb=true;ui.wifi=false;ui.uart=variant==20;ui.selected=3;ui.editing=false;if(ui.uart){s.bitrate=115200;s.rx_hz=1024;s.tx_hz=128;}}
        if(variant==1){s.connected=false;s.state=3;s.failed=UINT32_MAX;s.rates_valid=false;s.load_valid=false;}
        char filename[512];
        snprintf(filename,sizeof(filename),"%s/panel-%d-%d%s.ppm",argv[1],angle*90,page,suffixes[variant]);
        FILE *out=fopen(filename,"wb");if(!out)return 1;
        int width=ui_render_width(&ui),height=ui_render_height(&ui);
        fprintf(out,"P6\n%d %d\n255\n",width,height);uint16_t band[UI_DMA_PIXELS];
        for(int y=0;y<height;) {
            int rows=ui_render_rows(&ui,y);ui_render_band(band,y,&ui,&s,variant==1);
            for(int i=0;i<width*rows;i++) {
                unsigned v=band[i];unsigned char rgb[]={((v>>11)&31)*255/31,((v>>5)&63)*255/63,(v&31)*255/31};
                fwrite(rgb,1,3,out);
            }
            y+=rows;
        }
        if(fclose(out))return 1;
    }
}
