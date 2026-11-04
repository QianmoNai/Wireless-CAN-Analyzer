"""Run production Wi-Fi/dual startup code offline with driver fault injection."""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'firmware/main/network_link.c').read_text()


def block(marker):
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if not depth:
            return source[start:end + 1]
    raise AssertionError(marker)


adapter = block('static esp_err_t start_station_radio(void)')
dual = block('if(wanted>=DUAL_A&&wanted<=DUAL_B)')
# Any new raw start or power setter outside the common gate is a regression.
for path in (root / 'firmware/main').glob('*.c'):
    code = path.read_text()
    if path.name == 'network_link.c':
        code = code.replace(adapter, '')
    assert not re.search(r'\besp_wifi_(start|set_max_tx_power)\s*\(', code), path
assert source.count('start_station_radio();') == 3  # scan, association, dual
station = block('static bool begin_station(')
assert re.search(r'err=start_station_radio\(\);.*?if\(err!=ESP_OK\)return false;.*?err=esp_wifi_connect\(\)', station, re.S)
scan = block('if(r.op==NET_SCAN)')
assert scan.index('start_station_radio()') < scan.index('if(err==ESP_OK)err=esp_wifi_scan_start')
# App-only maintenance must not have an automatic whole-NVS erase fallback.
assert 'nvs_flash_erase(' not in (root / 'firmware/main/ble_bridge.c').read_text()

harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define DUAL_A 2
#define DUAL_B 3
#define DUAL_CHANNEL 6
#define WIFI_SECOND_CHAN_NONE 0
#define WIFI_PS_NONE 0
#define portMAX_DELAY 0
#define pdMS_TO_TICKS(n) (n)
static atomic_bool radio_on,online;
static atomic_uint station_ip;
static int start_rc,power_rc,get_rc,stop_rc,channel_rc,ps_rc;
static int starts,powers,reads,stops,channels,ps_calls,dual_starts;
static int8_t read_power=28;
static bool hardware_on,cap_checked,dual_ok=true,busy;
static unsigned dual_role,dual_retry;
static int requests,lock,assembly,status,error_code;
static esp_err_t esp_wifi_start(void){
 assert(!atomic_load(&radio_on));assert(!hardware_on);starts++;
 if(!start_rc)hardware_on=true;
 return start_rc;
}
static esp_err_t esp_wifi_set_max_tx_power(int power){
 assert(atomic_load(&radio_on)&&hardware_on);assert(power==32);
 powers++;cap_checked=false;return power_rc;
}
static esp_err_t esp_wifi_get_max_tx_power(int8_t *power){
 assert(hardware_on);reads++;*power=read_power;
 cap_checked=!get_rc&&read_power<=32;return get_rc;
}
static esp_err_t esp_wifi_stop(void){
 stops++;if(!stop_rc){hardware_on=false;cap_checked=false;}return stop_rc;
}
static void esp_wifi_disconnect(void){}
static esp_err_t esp_wifi_set_channel(int channel,int secondary){
 assert(cap_checked&&channel==DUAL_CHANNEL&&secondary==0);channels++;return channel_rc;
}
static esp_err_t esp_wifi_set_ps(int ps){assert(cap_checked&&ps==0);ps_calls++;return ps_rc;}
static bool dual_link_start(unsigned role){
 assert(cap_checked&&(role==DUAL_A||role==DUAL_B));dual_starts++;return dual_ok;
}
static void set_status(int s,int e){status=s;error_code=e;}
static unsigned millis(void){return 100;}
#define xQueueReset(q) ((void)(q))
#define xSemaphoreTake(l,t) ((void)(l),(void)(t))
#define xSemaphoreGive(l) ((void)(l))
#define network_assembly_reset(a) ((void)(a))
#define vTaskDelay(ms) ((void)(ms))
'''
main = r'''
static void reset(void){
 atomic_store(&radio_on,false);hardware_on=cap_checked=false;
 start_rc=power_rc=get_rc=stop_rc=channel_rc=ps_rc=0;
 starts=powers=reads=stops=channels=ps_calls=dual_starts=0;
 dual_role=dual_retry=0;status=error_code=0;dual_ok=true;read_power=28;
}
int main(void){
 reset();
 assert(start_station_radio()==ESP_OK);
 assert(starts==1&&powers==1&&reads==1&&atomic_load(&radio_on));
 assert(start_station_radio()==ESP_OK&&starts==1&&powers==2&&reads==2);
 read_power=32;assert(start_station_radio()==ESP_OK); // exact ceiling is allowed
 read_power=33;assert(start_station_radio()==ESP_FAIL&&!hardware_on);
 assert(!atomic_load(&radio_on));
 reset();power_rc=42;
 assert(start_station_radio()==42&&powers==1&&reads==0&&stops==1&&!hardware_on);
 reset();get_rc=44;
 assert(start_station_radio()==44&&reads==1&&stops==1&&!hardware_on);
 reset();start_rc=43;
 assert(start_station_radio()==43&&starts==1&&powers==0&&stops==0&&!atomic_load(&radio_on));
 start_rc=0;assert(start_station_radio()==ESP_OK&&starts==2&&powers==1);
 reset();power_rc=42;stop_rc=45;
 assert(start_station_radio()==42&&atomic_load(&radio_on)); // failed stop cannot claim off
 power_rc=stop_rc=0;assert(start_station_radio()==ESP_OK&&starts==1&&powers==2);
 for(unsigned role=DUAL_A;role<=DUAL_B;role++){
  reset();run_dual_once(role);
  assert(dual_role==role&&dual_starts==1&&channels==1&&ps_calls==1&&reads==1);
  assert(status==0&&!dual_retry);
  run_dual_once(role);assert(dual_starts==1&&powers==1); // already started
  reset();assert(start_station_radio()==ESP_OK);run_dual_once(role); // from scan/STA
  assert(dual_role==role&&starts==2&&powers==2&&stops==1);
  for(int failure=0;failure<7;failure++){
   reset();
   if(failure==0)start_rc=43;
   if(failure==1)power_rc=42;
   if(failure==2)get_rc=44;
   if(failure==3)read_power=33;
   if(failure==4)channel_rc=46;
   if(failure==5)ps_rc=47;
   if(failure==6)dual_ok=false;
   run_dual_once(role);
   assert(!dual_role&&status==4&&error_code==9&&dual_retry==1100);
   assert(dual_starts==(failure==6));
   if(failure<4)assert(!channels&&!ps_calls&&!hardware_on);
  }
 }
 puts("PASS: common power gate, 28/32 cap, >32 rejection, driver failures and recovery");
 puts("PASS: production DUAL_A/B startup and error gates; scan/association coverage; no NVS erase fallback");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-power-') as directory:
    path = Path(directory) / 'test.c'
    path.write_text(harness + adapter + '\nstatic void run_dual_once(unsigned wanted){\n'
                    'for(int once=0;once<1;once++){\n' + dual + '\n}}\n' + main)
    binary = str(Path(directory) / 'test')
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(path), '-o', binary], check=True)
    subprocess.run([binary], check=True)
