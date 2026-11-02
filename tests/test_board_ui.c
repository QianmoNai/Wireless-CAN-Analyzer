#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "board_ui_render.h"
#include "board_ui_font.h"
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
    assert(ui.brightness==60 && ui.sound && ui.rotation==UI_ROTATION_0 && ui.volume==UI_VOLUME_HIGH);
    assert(UI_PAGE_COUNT==2);
    for(unsigned button=0;button<3;button++) {
        ui_model_key(&ui,button,KEY_SHORT);assert(ui.page==UI_SETTINGS && !ui.editing);
        ui_model_key(&ui,1,KEY_LONG);assert(ui.page==UI_OVERVIEW);
    }
    ui_model_key(&ui,1,KEY_SHORT); assert(ui.page==UI_SETTINGS);
    ui_model_key(&ui,1,KEY_SHORT); assert(ui.editing);
    for(int i=0;i<20;i++)ui_model_key(&ui,2,KEY_SHORT);
    assert(ui.brightness==10);
    for(int i=0;i<20;i++)ui_model_key(&ui,0,KEY_SHORT);
    assert(ui.brightness==100);
    ui_model_key(&ui,1,KEY_LONG); assert(!ui.editing && ui.page==UI_SETTINGS);
    ui_model_key(&ui,2,KEY_SHORT); ui_model_key(&ui,1,KEY_SHORT);
    assert(!ui.sound && !ui.editing && ui.selected==1);
    ui_model_key(&ui,2,KEY_SHORT);
    ui_model_key(&ui,1,KEY_SHORT); ui_model_key(&ui,0,KEY_SHORT); assert(ui.rotation==UI_ROTATION_90);
    ui_model_t restored; ui_model_init(&restored,ui_model_settings(&ui));
    assert(restored.brightness==100 && !restored.sound && restored.rotation==UI_ROTATION_90);
    assert(restored.page==UI_OVERVIEW && !restored.editing);
    ui_model_init(&restored,0x10100); assert(restored.brightness==60); // Invalid zero brightness.
    // Read legacy 0/180 records without losing brightness/sound; save all four new angles.
    ui_model_init(&restored,0x10328);
    assert(restored.brightness==40 && restored.sound && restored.rotation==UI_ROTATION_180);
    ui_model_init(&restored,0x10050);
    assert(restored.brightness==80 && !restored.sound && restored.rotation==UI_ROTATION_0);
    for (int angle=0;angle<4;angle++) for (int sound=0;sound<2;sound++) for(int wifi=0;wifi<2;wifi++) for(int volume=0;volume<UI_VOLUME_COUNT;volume++) {
        ui.rotation=angle;ui.sound=sound;ui.wifi=wifi;ui.volume=volume;
        ui_model_init(&restored,ui_model_settings(&ui));
        assert(restored.rotation==angle && restored.sound==sound && restored.brightness==100 && restored.wifi==wifi && restored.volume==volume);
        ui_model_key(&ui,0,KEY_SHORT);assert(ui.rotation==((angle+1)&3));
        ui_model_key(&ui,2,KEY_SHORT);assert(ui.rotation==angle);
    }
    // Use physical BT indices: middle BT2 always acts; outer roles follow the
    // current top/left and bottom/right positions, including rotation changes.
    const unsigned previous_keys[]={0,0,2,2},next_keys[]={2,2,0,0};
    const unsigned decrease_keys[]={2,2,0,0},increase_keys[]={0,0,2,2};
    assert(ui_key_role(0,3)==UI_KEY_INVALID);
    for(unsigned angle=0;angle<4;angle++) {
        unsigned previous=previous_keys[angle],next=next_keys[angle];
        assert(ui_key_role(angle,previous)==UI_KEY_PREVIOUS);
        assert(ui_key_role(angle,next)==UI_KEY_NEXT && ui_key_role(angle,1)==UI_KEY_ACTION);
        ui_model_init(&ui,0);ui.rotation=angle;
        ui_model_key(&ui,1,KEY_SHORT);assert(ui.page==UI_SETTINGS && !ui.editing);
        ui_model_key(&ui,next,KEY_SHORT);assert(ui.selected==1);
        ui_model_key(&ui,previous,KEY_SHORT);assert(ui.selected==0);
        ui_model_key(&ui,previous,KEY_SHORT);assert(ui.selected==UI_SETTING_DATA); // Wrap upwards/leftwards.
        ui_model_key(&ui,next,KEY_SHORT);assert(ui.selected==0);
        ui_model_key(&ui,1,KEY_SHORT);assert(ui.editing);
        ui_model_key(&ui,decrease_keys[angle],KEY_SHORT);assert(ui.brightness==50);
        ui_model_key(&ui,increase_keys[angle],KEY_SHORT);assert(ui.brightness==60);
        for(unsigned side=0;side<3;side+=2) {
            ui_model_key(&ui,side,KEY_LONG);assert(ui.editing && ui.page==UI_SETTINGS);
        }
        ui_model_key(&ui,1,KEY_LONG);assert(!ui.editing && ui.page==UI_SETTINGS);
        ui_model_key(&ui,next,KEY_SHORT);assert(ui.selected==1);
        ui_model_key(&ui,1,KEY_SHORT);assert(!ui.sound && !ui.editing && ui.selected==1);
        ui_model_init(&restored,ui_model_settings(&ui));assert(!restored.sound && restored.rotation==angle);
        ui_model_key(&ui,1,KEY_SHORT);assert(ui.sound && !ui.editing && ui.selected==1);
        uint32_t sound_settings=ui_model_settings(&ui);
        ui_model_key(&ui,previous,KEY_SHORT);assert(ui.selected==0 && ui_model_settings(&ui)==sound_settings);
        ui_model_key(&ui,next,KEY_SHORT);assert(ui.selected==1 && ui_model_settings(&ui)==sound_settings);
        ui_model_key(&ui,next,KEY_SHORT);assert(ui.selected==2 && ui_model_settings(&ui)==sound_settings);
        ui_model_key(&ui,previous,KEY_SHORT);assert(ui.selected==1 && ui_model_settings(&ui)==sound_settings);
        // One sound item cycles off -> low -> medium -> high, in every rotation.
        ui.selected=UI_SETTING_SOUND;ui.sound=false;
        for(unsigned cycle=0;cycle<3;cycle++) {
            for(unsigned level=0;level<UI_VOLUME_COUNT;level++) {
                ui_model_key(&ui,1,KEY_SHORT);assert(ui.sound&&ui.volume==level&&!ui.editing);
                ui_model_init(&restored,ui_model_settings(&ui));assert(restored.sound&&restored.volume==level);
            }
            ui_model_key(&ui,1,KEY_SHORT);assert(!ui.sound&&!ui.editing);
        }
        ui_model_key(&ui,1,KEY_LONG);assert(ui.page==UI_OVERVIEW&&!ui.editing);
    }
    ui.page=UI_SETTINGS;ui.selected=3;ui.editing=false;
    ui_model_key(&ui,1,KEY_SHORT);assert(ui.wifi && !ui.editing);
    ui_model_init(&restored,ui_model_settings(&ui));assert(restored.wifi);
    ui_model_key(&ui,1,KEY_SHORT);assert(!ui.wifi && !ui.editing);
    ui_model_init(&restored,0x2073c);assert(!restored.wifi && restored.rotation==3); // Existing v2 defaults to BLE.
    // Existing records acquire high volume without changing their other settings.
    const uint32_t legacy[]={0x10232,0x20632,0x30e32};
    for(unsigned i=0;i<3;i++) {
        ui_model_init(&restored,legacy[i]);
        assert(restored.volume==UI_VOLUME_HIGH && !restored.sound && restored.brightness==50);
        assert(restored.rotation==(i==0?UI_ROTATION_180:UI_ROTATION_270));
        assert(restored.wifi==(i==2));
        uint32_t migrated=ui_model_settings(&restored);
        assert((migrated>>24)==7);
        ui_model_init(&restored,migrated);assert(ui_model_settings(&restored)==migrated);
    }
    ui.page=UI_SETTINGS;ui.editing=true;ui.selected=2;
    // Repeated presses of the same physical key must keep the same direction
    // through both portrait/landscape changes and the 270/0 wrap.
    for(unsigned step=0;step<8;step++) {
        unsigned angle=ui.rotation;
        ui_model_key(&ui,0,KEY_SHORT);assert(ui.rotation==((angle+1)&3));
    }
    for(unsigned step=0;step<8;step++) {
        unsigned angle=ui.rotation;
        ui_model_key(&ui,2,KEY_SHORT);assert(ui.rotation==((angle+3)&3));
    }
    const uint32_t invalid[]={0x60432,0x5c432,0x20f32,0x10432,0x20009,0x20165,0x43032,0x44032,0x48032,0x31032};
    for (unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++) {
        ui_model_init(&restored,invalid[i]);
        assert(restored.brightness==60 && restored.sound && restored.rotation==0 && restored.volume==UI_VOLUME_HIGH);
    }

    // Five explicit transports; paired modes expose one additional menu item.
    ui_model_init(&ui,0);ui.page=UI_SETTINGS;ui.selected=3;
    const unsigned menu_modes[]={1,4,2,3,0};
    for(unsigned step=0;step<5;step++){
        unsigned mode=menu_modes[step];
        ui_model_key(&ui,1,KEY_SHORT);assert(ui_mode(&ui)==mode);
        ui_model_init(&restored,ui_model_settings(&ui));assert(ui_mode(&restored)==mode);
        assert(ui_setting_count(&ui)==(mode>=2&&mode<=3?6:5));
    }
    ui.dual_role=1;ui.selected=5;ui_model_key(&ui,1,KEY_SHORT);assert(ui.link_menu&&!ui.editing);
    ui_model_init(&restored,ui_model_settings(&ui));assert(!restored.link_menu&&restored.dual_role==1);
    ui_model_init(&ui,0);

    // Both data sources retain USB across restart, without adding pairing settings.
    for(unsigned data=0;data<2;data++)for(unsigned angle=0;angle<4;angle++) {
        ui_model_init(&ui,0);ui.usb=true;ui.uart=data;ui.rotation=angle;
        ui_model_init(&restored,ui_model_settings(&ui));
        assert(restored.usb&&restored.uart==data&&restored.rotation==angle);
        assert(ui_mode(&restored)==4&&!restored.wifi&&!restored.dual_role);
        assert(ui_setting_count(&restored)==5+data);
    }
    ui_model_init(&restored,0x06092132); // Prior UART settings remain UART at 115200.
    assert(restored.uart&&!restored.usb&&restored.uart_baud==4&&restored.brightness==50);
    // UART selection and baud are persisted independently of radio transport.
    for(unsigned baud=0;baud<UI_UART_BAUD_COUNT;baud++)for(unsigned role=0;role<3;role++) {
        ui_model_init(&ui,0);ui.uart=true;ui.uart_baud=baud;ui.dual_role=role;
        ui_model_init(&restored,ui_model_settings(&ui));
        assert(restored.uart&&restored.uart_baud==baud&&restored.dual_role==role);
        assert(ui_uart_baud(&restored)>=9600&&ui_uart_baud(&restored)<=921600);
        assert(ui_setting_count(&ui)==(role?7:6));
        ui.page=UI_SETTINGS;ui.selected=UI_SETTING_BAUD;ui_model_key(&ui,1,KEY_SHORT);
        assert(ui.uart_baud==(baud+1)%UI_UART_BAUD_COUNT&&!ui.editing);
        ui.selected=UI_SETTING_DATA;ui_model_key(&ui,1,KEY_SHORT);assert(!ui.uart);
    }
    ui_model_init(&ui,0);
    const char *text="A总览/\xc2\xb0\xf0\x9f\x98\x80";
    const uint32_t codes[]={'A',0x603b,0x89c8,'/',0xb0,0x1f600,0};
    for (unsigned i=0;i<sizeof(codes)/sizeof(codes[0]);i++) assert(ui_utf8_next(&text)==codes[i]);
    assert(ui_utf8_next(&text)==0);
    // Truncated strings are deliberately short objects, also checked under ASan.
    const char truncated2[]={ (char)0xc2,0 },truncated3[]={ (char)0xe4,(char)0xb8,0 };
    const char *bad[]={truncated2,truncated3,"\x80","\xc0\xaf","\xed\xa0\x80","\xf4\x90\x80\x80","\xff"};
    for (unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);i++) {
        text=bad[i];unsigned consumed=0;
        while (*text) { const char *before=text;assert(ui_utf8_next(&text)==0xfffd);assert(text>before);consumed++; }
        assert(consumed<=strlen(bad[i]));
    }
    assert(ui_cn_glyph(0x603b)!=NULL && ui_cn_glyph(0x89c8)!=NULL && ui_cn_glyph(0x1f600)==NULL);

    ui_snapshot_t s={.bitrate=1000000}; uint8_t packet[20]={1,14};
    put(packet+4,150);put(packet+8,75);put(packet+12,1500000);
    ui_snapshot_packet(&s,packet); assert(s.rates_valid && s.rx_hz==100 && s.tx_hz==50);
    put(packet+12,0);ui_snapshot_packet(&s,packet);assert(!s.rates_valid);
    packet[1]=13;packet[2]=0x88;packet[3]=0x13;ui_snapshot_packet(&s,packet);
    assert(s.load==5000 && s.load_valid);
    packet[1]=5;packet[2]=0;put(packet+4,500000);ui_snapshot_packet(&s,packet);
    assert(s.bitrate==500000 && !s.load_valid && !s.rates_valid);
    ui_snapshot_t before_frames=s;
    packet[3]=15;
    for(int type=1;type<=3;type++) for(int i=0;i<1000;i++) {
        packet[1]=type;packet[2]=i;put(packet+4,i);ui_snapshot_packet(&s,packet);
        assert(memcmp(&s,&before_frames,sizeof(s))==0);
    }
    packet[1]=4;packet[2]=3;put(packet+12,42);ui_snapshot_packet(&s,packet);assert(s.state==3 && s.failed==42);

    // Guard every DMA band, including the final four rows in landscape.
    uint16_t guarded[UI_DMA_PIXELS+2];
    s.rates_valid=true;s.load_valid=true;s.rx_hz=UINT32_MAX;s.load=10000;s.failed=UINT32_MAX;
    for(int angle=0;angle<4;angle++) {
        ui.rotation=angle;
        int width=ui_render_width(&ui),height=ui_render_height(&ui);
        assert(width*height==UI_WIDTH*UI_HEIGHT);
        assert(ui_render_rows(&ui,-1)==0 && ui_render_rows(&ui,height)==0);
        // Cover the same physical crop exactly once, including asymmetric offsets.
        for(int gap=0;gap<=68;gap+=17) {
            ui_panel_geometry_t g=ui_panel_geometry(angle,gap,0);
            assert(g.width==width && g.height==height);
            bool seen[UI_HEIGHT][UI_WIDTH]={0};
            for(int y=0;y<height;y++) for(int x=0;x<width;x++) {
                int px=g.swap_xy?y+g.y_gap:x+g.x_gap;
                int py=g.swap_xy?x+g.x_gap:y+g.y_gap;
                if(g.mirror_x)px=239-px;
                if(g.mirror_y)py=319-py;
                assert(px>=gap && px<gap+UI_WIDTH && py>=0 && py<UI_HEIGHT);
                assert(!seen[py][px-gap]);seen[py][px-gap]=true;
            }
            // Actual buttons sit at native x<0, in ascending native y (BT1..3).
            // Project them into screen coordinates, anchored by the user's
            // confirmed 270-degree upper row BT3 / BT2 / BT1.
            int axis[3];
            for(int button=0;button<3;button++) {
                int px=gap-10,py=80+button*80;
                if(g.mirror_x)px=239-px;
                if(g.mirror_y)py=319-py;
                int x=g.swap_xy?py-g.x_gap:px-g.x_gap;
                int y=g.swap_xy?px-g.y_gap:py-g.y_gap;
                axis[button]=(angle&1)?x:y;
            }
            assert(axis[previous_keys[angle]]<axis[1] && axis[1]<axis[next_keys[angle]]);
        }
        for(int page=0;page<UI_PAGE_COUNT;page++) for(int state=0;state<4;state++) for(int selected=0;selected<UI_SETTING_COUNT;selected++) for(int wifi=0;wifi<2;wifi++) for(int pairing=0;pairing<2;pairing++) for(int volume=0;volume<UI_VOLUME_COUNT;volume++) {
            ui.wifi=wifi;ui.volume=volume;s.pairing=pairing;s.pairing_code=123456;
            ui.page=page;ui.editing=(state&2) && (selected==0 || selected==2);ui.selected=selected;
            s.state=state;s.connected=state&1;
            int top=0,total=0;
            while(top<height) {
                int rows=ui_render_rows(&ui,top),count=rows*width;
                assert(rows>0 && count<=UI_DMA_PIXELS);
                for(int i=0;i<UI_DMA_PIXELS+2;i++)guarded[i]=0xdead;
                ui_render_band(guarded+1,top,&ui,&s,true);
                assert(guarded[0]==0xdead);
                for(int i=count+1;i<UI_DMA_PIXELS+2;i++)assert(guarded[i]==0xdead);
                for(int i=1;i<=count;i++)assert(guarded[i]!=0xdead);
                total+=count;top+=rows;
            }
            assert(total==UI_WIDTH*UI_HEIGHT);
        }
    }
    puts("board UI: debounce, spatial keys in four rotations, direct sound/volume/mode actions, navigation, v1-v5 settings, UTF-8, telemetry, panel crop and partial DMA bounds PASS");
}
