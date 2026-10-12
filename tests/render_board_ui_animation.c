// Animation preview from the actual firmware renderer, using simulated data.
#include <stdio.h>
#include "board_ui_animation.h"
int main(int argc,char **argv)
{
    if(argc!=2)return 2;
    const unsigned rotations[]={UI_ROTATION_0,UI_ROTATION_270};
    for(unsigned orientation=0;orientation<2;orientation++){
        ui_model_t ui;ui_model_init(&ui,0);ui.rotation=rotations[orientation];
        ui_animation_t a;ui_animation_init(&a,&ui);
        ui_snapshot_t s={.bitrate=1000000,.rx_hz=1000,.tx_hz=100,.load=1189,
            .rates_valid=true,.load_valid=true,.connected=true};
        for(unsigned now=0;now<=4200;now+=50){
            unsigned next=ui.rotation<2?2:0;
            switch(now){
            case 400:case 1850:case 2600:case 3500:ui_model_key(&ui,1,KEY_SHORT);break;
            case 1000:case 1250:case 1500:case 2350:ui_model_key(&ui,next,KEY_SHORT);break;
            case 3000:case 3650:ui_model_key(&ui,1,KEY_LONG);break;
            }
            s.connected=!ui.wifi;
            ui_animation_target(&a,&ui,now);
            ui_render_pose_t pose;ui_animation_sample(&a,now,&pose);
            char path[512];snprintf(path,sizeof(path),"%s/animation-%u-%04u.ppm",argv[1],ui.rotation*90,now);
            FILE *out=fopen(path,"wb");if(!out)return 1;
            int width=ui_render_width(&ui),height=ui_render_height(&ui);
            fprintf(out,"P6\n%d %d\n255\n",width,height);uint16_t band[UI_DMA_PIXELS];
            for(int top=0;top<height;){
                int rows=ui_render_rows(&ui,top);
                ui_render_animated_band(band,top,&ui,&s,false,&pose);
                for(int i=0;i<width*rows;i++){
                    unsigned v=band[i];unsigned char rgb[]={((v>>11)&31)*255/31,((v>>5)&63)*255/63,(v&31)*255/31};
                    fwrite(rgb,1,3,out);
                }
                top+=rows;
            }
            if(fclose(out))return 1;
        }
    }
}
