// Offline showcase timeline using the production renderer and key model.
#include <stdio.h>
#include "board_ui_animation.h"
static void key(ui_model_t *u, unsigned k) { ui_model_key(u,k,KEY_SHORT); }
static void next(ui_model_t *u) { key(u,u->rotation<2?2:0); }
int main(int argc,char **argv)
{
    if(argc!=2)return 2;
    for(unsigned view=0;view<2;view++){
        ui_model_t u;ui_model_init(&u,0);u.rotation=view?UI_ROTATION_270:UI_ROTATION_0;
        ui_animation_t a;ui_animation_init(&a,&u);
        ui_snapshot_t s={.bitrate=1000000,.rates_valid=true,.load_valid=true,.connected=true};
        for(unsigned now=0;now<46000;now+=50){
            switch(now){
            case 4000:key(&u,1);break;
            case 4700:key(&u,1);break;
            case 5400:case 5800:key(&u,u.rotation<2?0:2);break;
            case 6400:key(&u,u.rotation<2?2:0);break;
            case 7100:key(&u,1);break;
            case 8000:next(&u);break;
            case 8700:case 9800:key(&u,1);break;
            case 11000:case 11300:case 11600:next(&u);break;
            case 12200:case 13000:case 13800:key(&u,1);break;
            case 14500:key(&u,u.rotation<2?0:2);break;
            case 15200:key(&u,1);break;
            case 17000:ui_model_key(&u,1,KEY_LONG);break;
            case 20000:key(&u,1);break;
            case 20900:key(&u,1);break;
            case 21800:case 22100:next(&u);break;
            case 23000:key(&u,1);s.dual.state=DUAL_UI_SEARCH;s.dual.count=2;break;
            case 25000:s.dual.state=DUAL_UI_CHECK;s.dual.code=123456;break;
            case 27400:s.dual.state=DUAL_UI_ONLINE;s.dual.bound=true;break;
            case 28500:u.link_menu=false;u.page=UI_OVERVIEW;break;
            case 31000:key(&u,1);u.selected=3;break;
            case 31800:key(&u,1);s.dual.state=DUAL_UI_WAIT;s.dual.bound=false;break;
            case 32600:u.selected=5;key(&u,1);break;
            case 33800:s.dual.state=DUAL_UI_ONLINE;s.dual.bound=true;break;
            case 34700:u.link_menu=false;u.page=UI_OVERVIEW;break;
            case 38000:u.dual_role=0;u.wifi=false;u.page=UI_SETTINGS;u.selected=2;u.editing=true;break;
            case 40000:case 42000:case 44000:key(&u,0);break;
            }
            s.connected=!u.dual_role||s.dual.state==DUAL_UI_ONLINE;s.wifi_state=u.wifi?2:0;
            snprintf(s.ip,sizeof(s.ip),"192.168.1.123");
            unsigned tick=now/500;
            s.rx_hz=680+(tick*17)%43;s.tx_hz=u.dual_role?s.rx_hz-3:90+(tick*7)%21;
            s.load=(s.rx_hz+s.tx_hz)*12/10;s.dual.sent=now/2;s.dual.received=now*2/3;
            s.dual.peer[0]=0x90;s.dual.peer[1]=0xda;s.dual.peer[2]=0x72;s.dual.peer[3]=0x73;s.dual.peer[4]=0x3d;s.dual.peer[5]=0x88;
            ui_animation_target(&a,&u,now);ui_render_pose_t p;ui_animation_sample(&a,now,&p);
            char path[512];snprintf(path,sizeof(path),"%s/view-%u-%04u.ppm",argv[1],view,now/50);
            FILE *out=fopen(path,"wb");if(!out)return 1;
            int w=ui_render_width(&u),h=ui_render_height(&u);fprintf(out,"P6\n%d %d\n255\n",w,h);
            uint16_t band[UI_DMA_PIXELS];
            for(int top=0;top<h;){int rows=ui_render_rows(&u,top);ui_render_animated_band(band,top,&u,&s,false,&p);
                for(int i=0;i<w*rows;i++){unsigned v=band[i];unsigned char rgb[]={((v>>11)&31)*255/31,((v>>5)&63)*255/63,(v&31)*255/31};fwrite(rgb,1,3,out);}top+=rows;}
            if(fclose(out))return 1;
        }
    }
    return 0;
}
