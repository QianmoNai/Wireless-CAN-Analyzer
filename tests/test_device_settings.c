// Run the actual persistent settings owner with a transactional NVS stub.
#include "sim.h"
#include "../firmware/main/device_settings.c"
#include <stdio.h>
static uint32_t now,flash,staged,live_baud;
static unsigned commits,live_mode,locked;
static bool live_uart,fail_open,fail_set,fail_commit;
SemaphoreHandle_t xSemaphoreCreateMutex(void){return (void*)1;}
int xSemaphoreTake(SemaphoreHandle_t s,unsigned wait){(void)s;(void)wait;assert(!locked);locked=1;return 1;}
int xSemaphoreGive(SemaphoreHandle_t s){(void)s;assert(locked);locked=0;return 1;}
int xTaskCreate(void(*f)(void*),const char*n,unsigned stack,void*a,unsigned prio,void*h){(void)f;(void)n;(void)stack;(void)a;(void)prio;(void)h;return 1;}
void vTaskDelay(unsigned t){(void)t;}
int64_t esp_timer_get_time(void){return (int64_t)now*1000;}
int nvs_open(const char*n,int mode,nvs_handle_t*h){assert(!strcmp(n,"local_panel"));(void)mode;*h=1;return fail_open?1:0;}
int nvs_get_u32(nvs_handle_t h,const char*k,uint32_t*v){(void)h;assert(!strcmp(k,"settings"));*v=flash;return 0;}
int nvs_set_u32(nvs_handle_t h,const char*k,uint32_t v){(void)h;assert(!strcmp(k,"settings"));staged=v;return fail_set?1:0;}
int nvs_commit(nvs_handle_t h){(void)h;commits++;if(fail_commit)return 1;flash=staged;return 0;}
void nvs_close(nvs_handle_t h){(void)h;}
void network_set_mode(unsigned mode){assert(!locked);live_mode=mode;}
void uart_bridge_configure(bool selected,uint32_t baud){assert(!locked);live_uart=selected;live_baud=baud;}
static bridge_packet_t reply;
static void command(uint8_t p[20],unsigned mask,unsigned mode,unsigned data,uint32_t baud){
    memset(p,0,20);p[0]=1;p[1]=SETTINGS_COMMAND;p[2]=1;p[3]=mask;p[4]=mode;p[5]=data;
    wire_put_u32(p+8,baud);wire_put_u32(p+16,0x12345678);
}
static void query(uint8_t p[20]){command(p,0,0,0,0);p[2]=0;}
static void apply(uint8_t p[20]){assert(device_settings_command(p,&reply)==0);assert(reply.bytes[2]==1);assert(wire_u32(reply.bytes+16)==0x12345678);}
int main(void){
    uint8_t p[20];query(p);assert(device_settings_command(p,&reply)==17);
    device_settings_init();assert(live_mode==0&&!live_uart&&live_baud==115200);
    assert(device_settings_command(p,&reply)==0&&reply.bytes[2]==0&&reply.bytes[3]==0&&wire_u32(reply.bytes+8)==115200&&!commits);
    for(unsigned i=0;i<16;i++){
        uint8_t bad[20];query(bad);bad[i]=i==0?2:0xff;
        assert(device_settings_command(bad,&reply)==19&&!commits);
    }
    query(p);memset(p+16,0,4);assert(device_settings_command(p,&reply)==19);
    command(p,7,5,1,115200);assert(device_settings_command(p,&reply)==19);
    command(p,7,4,2,115200);assert(device_settings_command(p,&reply)==19);
    command(p,7,4,1,12345);assert(device_settings_command(p,&reply)==19);
    command(p,8,0,0,0);assert(device_settings_command(p,&reply)==19);
    command(p,0,0,0,0);assert(device_settings_command(p,&reply)==19);
    for(unsigned mode=0;mode<5;mode++){
        command(p,7,mode,1,115200);apply(p);assert(reply.bytes[3]==mode&&reply.bytes[4]==1);
        step();assert(live_mode==mode&&live_uart&&live_baud==115200);
        ui_model_t decoded;ui_model_init(&decoded,flash);assert(ui_mode(&decoded)==mode&&decoded.uart);
    }
    const unsigned rates[]={9600,19200,38400,57600,115200,230400,460800,921600};
    for(unsigned i=0;i<8;i++){command(p,4,0,0,rates[i]);apply(p);step();assert(live_baud==rates[i]);}
    unsigned before=commits;apply(p);assert(commits==before);
    uint32_t saved=current;
    for(unsigned fail=0;fail<3;fail++){
        fail_open=fail==0;fail_set=fail==1;fail_commit=fail==2;
        command(p,2,0,0,0);assert(!device_settings_command(p,&reply)&&reply.bytes[2]==2);
        assert(current==saved&&flash==saved&&reply.bytes[4]==1&&device_settings_save_failed());
    }
    fail_open=fail_set=fail_commit=false;
    // A stale screen snapshot cannot undo a concurrent remote save.
    ui_model_t ui={0};uint32_t expected=device_settings_read(&ui);ui.brightness=80;
    command(p,2,0,0,0);apply(p);device_settings_update(&ui,expected);assert(current!=ui_model_settings(&ui));
    expected=device_settings_read(&ui);ui.brightness=70;device_settings_update(&ui,expected);
    before=commits;now+=1499;step();assert(commits==before);
    now++;step();assert(commits==before+1&&flash==current);
    expected=device_settings_read(&ui);ui.brightness=90;device_settings_update(&ui,expected);
    fail_commit=true;now+=1500;step();assert(device_settings_save_failed()&&dirty);
    fail_commit=false;now+=10000;step();assert(!device_settings_save_failed()&&!dirty&&flash==current);
    // Simulate restart: only NVS survives, not the current snapshot.
    saved=flash;current=persisted=retry_at=0;dirty=save_failed=false;
    device_settings_init();assert(current==saved&&persisted==saved&&live_mode==4&&!live_uart&&live_baud==921600);
    query(p);assert(settings_usb_bootstrap(p));command(p,1,4,0,0);assert(settings_usb_bootstrap(p));
    command(p,2,0,1,0);assert(!settings_usb_bootstrap(p));command(p,1,0,0,0);assert(!settings_usb_bootstrap(p));
    puts("settings: complete validation, all modes/rates, transactional NVS failure, idempotence, screen CAS/debounce/retry, deferred apply and restart PASS");
}
