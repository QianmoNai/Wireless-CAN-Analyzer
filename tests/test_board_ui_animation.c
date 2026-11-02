#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "board_ui_animation.h"

static uint16_t screen[UI_WIDTH*UI_HEIGHT],expected[UI_WIDTH*UI_HEIGHT];
static ui_model_t old_ui;
static ui_snapshot_t old_snapshot;
static ui_render_pose_t old_pose;
static bool old_failed;
static bool have_screen;
static uint32_t step(ui_animation_t *a,const ui_model_t *ui,const ui_snapshot_t *s,bool failed,uint32_t now)
{
    ui_animation_target(a,ui,now);
    ui_render_pose_t pose;ui_animation_sample(a,now,&pose);
    uint32_t dirty=have_screen?ui_render_dirty(&old_ui,&old_snapshot,old_failed,&old_pose,ui,s,failed,&pose):ui_render_all_bands(ui);
    int width=ui_render_width(ui),height=ui_render_height(ui),index=0;
    for(int top=0;top<height;index++){
        uint16_t buffer[UI_DMA_PIXELS+2];
        for(unsigned i=0;i<UI_DMA_PIXELS+2;i++)buffer[i]=0xdead;
        int rows=ui_render_rows(ui,top),count=rows*width;
        ui_render_animated_band(buffer+1,top,ui,s,failed,&pose);
        assert(buffer[0]==0xdead);
        for(int i=count+1;i<UI_DMA_PIXELS+2;i++)assert(buffer[i]==0xdead);
        memcpy(expected+top*width,buffer+1,count*sizeof(uint16_t));
        if(dirty&(1u<<index))memcpy(screen+top*width,buffer+1,count*sizeof(uint16_t));
        top+=rows;
    }
    // Apply only the scheduled bands to the old display, then compare every
    // pixel with a full fresh render. Missed invalidations leave visible trails.
    assert(memcmp(screen,expected,sizeof(screen))==0);
    old_ui=*ui;old_snapshot=*s;old_pose=pose;old_failed=failed;have_screen=true;
    return dirty;
}
static void timeline(void)
{
    ui_model_t ui;ui_model_init(&ui,0);
    ui_animation_t a;ui_animation_init(&a,&ui);
    ui_render_pose_t p;
    assert(!ui_animation_sample(&a,0,&p) && p.page==0 && p.theme==0);
    ui.page=UI_SETTINGS;ui_animation_target(&a,&ui,100);
    assert(ui_animation_sample(&a,100,&p) && p.page==0);
    ui_animation_sample(&a,120,&p);assert(p.page>0 && p.page<16); // Gentle first step.
    ui_animation_sample(&a,220,&p);assert(p.page==128); // Symmetric page acceleration.
    ui_animation_target(&a,&ui,120); // Repeated target cannot restart animation.
    assert(ui_animation_sample(&a,180,&p) && p.page>0 && p.page<256);
    int current=p.page;
    ui.page=UI_OVERVIEW;ui_animation_target(&a,&ui,180);
    ui_animation_sample(&a,180,&p);assert(p.page==current); // Continuous reversal.
    assert(!ui_animation_sample(&a,420,&p) && p.page==0);
    ui.page=UI_SETTINGS;ui.wifi=true;ui_animation_target(&a,&ui,500);
    assert(ui_animation_sample(&a,600,&p) && p.theme>0 && p.theme<256);
    ui_animation_target(&a,&ui,700);
    assert(!ui_animation_sample(&a,780,&p) && p.page==256 && p.theme==256);
    ui.selected=1;ui_animation_target(&a,&ui,UINT32_MAX-50);
    assert(ui_animation_sample(&a,30,&p) && p.selection_y>68 && p.selection_y<104);
    assert(!ui_animation_sample(&a,200,&p) && p.selection_y==104);
    ui.selected=4;ui_animation_target(&a,&ui,300);
    ui_animation_sample(&a,340,&p);int y=p.selection_y;
    ui.selected=0;ui_animation_target(&a,&ui,340);
    ui_animation_sample(&a,340,&p);assert(p.selection_y==y);
    ui.rotation=UI_ROTATION_270;ui_animation_target(&a,&ui,350);
    assert(!ui_animation_sample(&a,350,&p) && p.selection_x==12 && p.selection_y==37);
}
int main(void)
{
    timeline();
    unsigned partial_frames=0;
    for(unsigned angle=0;angle<4;angle++)for(unsigned wifi=0;wifi<2;wifi++){
        ui_model_t ui;ui_model_init(&ui,0);ui.rotation=angle;ui.wifi=wifi;
        ui_snapshot_t s={.bitrate=1000000,.rates_valid=true,.load_valid=true};
        ui_animation_t a;ui_animation_init(&a,&ui);have_screen=false;
        uint32_t now=0;
        step(&a,&ui,&s,false,now);
        assert(step(&a,&ui,&s,false,now+10)==0); // Idle screen sends nothing.
        ui_model_key(&ui,1,KEY_SHORT);
        assert(step(&a,&ui,&s,false,now+=20)==0); // Initial slide pose is unchanged.
        for(unsigned frame=0;frame<16;frame++)step(&a,&ui,&s,false,now+=20);
        unsigned next=angle<2?2:0;
        for(unsigned selected=0;selected<UI_SETTING_COUNT*2;selected++){
            ui_model_key(&ui,next,KEY_SHORT);
            for(unsigned frame=0;frame<12;frame++){
                uint32_t dirty=step(&a,&ui,&s,false,now+=20);
                if(dirty && dirty!=ui_render_all_bands(&ui))partial_frames++;
            }
        }
        // Quick presses while page/menu/theme transitions are still moving.
        uint32_t random=1234+angle;
        bool failed=false;
        for(unsigned frame=0;frame<320;frame++){
            random=random*1664525u+1013904223u;
            if(frame%3==0)ui_model_key(&ui,(random>>16)%3,frame%11==0?KEY_LONG:KEY_SHORT);
            if(frame%17==0){ui.wifi=!ui.wifi;s.connected=!s.connected;s.wifi_state=(s.wifi_state+1)%5;}
            if(frame%31==0){ui.usb=!ui.usb;ui.uart=!ui.uart;}
            if(frame%19==0){ui.volume=(ui.volume+1)%UI_VOLUME_COUNT;ui.sound=!ui.sound;}
            if(frame%23==0){s.pairing=!s.pairing;s.pairing_code=123456+frame;}
            if(frame%29==0){failed=!failed;snprintf(s.ip,sizeof(s.ip),"192.168.1.%u",frame%255);}
            s.bitrate=frame%7?1000000:500000;s.state=frame%4;s.failed=frame;
            s.rx_hz=frame;s.tx_hz=frame*7;s.load=frame%10000;
            s.rates_valid=frame%13!=0;s.load_valid=frame%7!=0;
            step(&a,&ui,&s,failed,now+=20);
        }
        // Rotation in mid-animation always invalidates the whole frame.
        for(unsigned rotation=0;rotation<4;rotation++){
            ui.rotation=(ui.rotation+1)%4;
            assert(step(&a,&ui,&s,failed,now+=20)==ui_render_all_bands(&ui));
        }
        // Settled animation output must exactly equal the ordinary renderer.
        s.pairing=false;step(&a,&ui,&s,failed,now+=1000);
        for(int top=0;top<ui_render_height(&ui);){
            uint16_t buffer[UI_DMA_PIXELS];int rows=ui_render_rows(&ui,top),width=ui_render_width(&ui);
            ui_render_band(buffer,top,&ui,&s,failed);
            assert(memcmp(buffer,screen+top*width,rows*width*sizeof(uint16_t))==0);top+=rows;
        }
    }
    // Dual-mode menus and overlays must invalidate exactly the pixels they change.
    for(unsigned angle=0;angle<4;angle++)for(unsigned role=1;role<=2;role++){
        ui_model_t ui;ui_model_init(&ui,0);ui.rotation=angle;ui.dual_role=role;
        ui_snapshot_t s={.bitrate=1000000};ui_animation_t a;ui_animation_init(&a,&ui);have_screen=false;
        uint32_t now=0;step(&a,&ui,&s,false,now);
        s.dual.bound=true;step(&a,&ui,&s,false,now+=100);
        ui.page=UI_SETTINGS;ui.selected=5;
        for(unsigned i=0;i<20;i++)step(&a,&ui,&s,false,now+=20);
        step(&a,&ui,&s,true,now+=100);
        ui.link_menu=true;
        for(unsigned state=0;state<=DUAL_UI_UNBIND;state++){
            s.dual.state=state;s.dual.code=123456+state;s.dual.count=2;s.dual.index=1;
            s.dual.peer[0]=0x90;s.dual.peer[5]=0xac;s.dual.sent=state*3;s.dual.received=state*4;
            step(&a,&ui,&s,false,now+=100);s.dual.confirmed=!s.dual.confirmed;step(&a,&ui,&s,false,now+=100);
        }
        ui.link_menu=false;step(&a,&ui,&s,false,now+=100);
        ui.uart=true;step(&a,&ui,&s,false,now+=100);
        for(unsigned selected=0;selected<7;selected++){
            ui.selected=selected;ui.uart_baud=selected;
            for(unsigned frame=0;frame<12;frame++)step(&a,&ui,&s,false,now+=20);
        }
        ui.page=UI_OVERVIEW;for(unsigned frame=0;frame<20;frame++)step(&a,&ui,&s,false,now+=20);
    }
    assert(partial_frames>100);
    printf("board animation: eased retarget/reverse, timer wrap, four rotations, DMA bounds, %u partial frames and pixel-exact dirty redraws PASS\n",partial_frames);
}
