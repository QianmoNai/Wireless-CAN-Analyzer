"""Host fault injection of the actual NimBLE callout adapter, no radio proof."""
from pathlib import Path
import subprocess
import tempfile
root=Path(__file__).resolve().parents[1]
source=(root/'firmware/main/ble_bridge.c').read_text()
def function(marker):
 start=source.index(marker);opening=source.index('{',start);depth=0
 for i in range(opening,len(source)):
  depth+=(source[i]=='{')-(source[i]=='}')
  if depth==0:return source[start:i+1]
 raise AssertionError(marker)
prelude=r'''
#include <assert.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include "ble_connection_policy.h"
#define CONFIG_BLE_CONNECTION_POLICY 1
#define BLE_HS_CONN_HANDLE_NONE 0xffff
struct ble_npl_event {int unused;};
struct ble_npl_callout {int unused;};
struct ble_gap_conn_desc {uint16_t conn_itvl,conn_latency,supervision_timeout;};
struct ble_gap_upd_params {uint16_t itvl_min,itvl_max,latency,supervision_timeout,min_ce_len,max_ce_len;};
static struct ble_npl_callout parameter_timer;
static bridge_ble_policy_t parameter_policy;
static atomic_uint ble_generation=1;
static atomic_int connection=3;
static atomic_bool subscribed=true;
static bool wifi,usb,dual;
static int64_t clock_ms;
static unsigned requests,timer_ms,stopped;
static int api_rc;
static struct ble_gap_upd_params last;
static unsigned bd[2];
enum {BD_PARAM_REQUESTS,BD_PARAM_REQUEST_RC};
static void bd_add(unsigned w,unsigned n){bd[w]+=n;}
static void bd_set(unsigned w,unsigned n){bd[w]=n;}
static bool network_wifi_mode(void){return wifi;}
static bool network_usb_mode(void){return usb;}
static bool network_dual_mode(void){return dual;}
static int64_t esp_timer_get_time(void){return clock_ms*1000;}
static int ble_gap_conn_find(int conn,struct ble_gap_conn_desc*d){assert(conn==3);*d=(struct ble_gap_conn_desc){36,0,42};return 0;}
static int ble_gap_update_params(int conn,const struct ble_gap_upd_params*p){assert(conn==3);last=*p;requests++;return api_rc;}
static void ble_npl_callout_reset(struct ble_npl_callout*t,unsigned ticks){(void)t;timer_ms=ticks;}
static unsigned ble_npl_time_ms_to_ticks32(unsigned ms){return ms;}
static void ble_npl_callout_stop(struct ble_npl_callout*t){(void)t;stopped++;}
'''
main=r'''
int main(void){
 parameter_subscribe(true);assert(timer_ms==1000);
 parameter_tick(NULL);assert(requests==0&&timer_ms==1000);
 clock_ms=1000;parameter_tick(NULL);
 assert(requests==1&&bd[BD_PARAM_REQUESTS]==1&&bd[BD_PARAM_REQUEST_RC]==0);
 assert(last.itvl_min==12&&last.itvl_max==24&&last.latency==0&&last.supervision_timeout==42);
 clock_ms=10000;parameter_tick(NULL);assert(requests==1);
 parameter_subscribe(false);assert(stopped==1);
 parameter_subscribe(true);parameter_tick(NULL);assert(requests==1);
 // New generation gets a fresh budget; two immediate rejections exhaust it.
 atomic_store(&ble_generation,2);parameter_subscribe(true);api_rc=7;
 clock_ms=11000;parameter_tick(NULL);assert(requests==2&&timer_ms==3000);
 clock_ms=14000;parameter_tick(NULL);assert(requests==3&&parameter_policy.finished);
 clock_ms=20000;parameter_tick(NULL);assert(requests==3);
 // A pending old callout cannot submit on a replacement connection or LAN.
 atomic_store(&ble_generation,3);parameter_tick(NULL);assert(requests==3);
 parameter_subscribe(true);clock_ms=21000;wifi=true;parameter_tick(NULL);assert(requests==3);
 wifi=false;usb=true;parameter_tick(NULL);assert(requests==3);
 usb=false;dual=true;parameter_tick(NULL);assert(requests==3);
 dual=false;atomic_store(&subscribed,false);parameter_tick(NULL);assert(requests==3);
 puts("PASS: actual BLE parameter adapter, delayed host callout, finite budget, local rejection, stale generation and mode isolation");
}
'''
with tempfile.TemporaryDirectory(prefix='ble-connection-')as d:
 p=Path(d);(p/'test.c').write_text(prelude+function('static void parameter_tick(')+function('static void parameter_subscribe(')+main)
 subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(root/'firmware/main'),str(p/'test.c'),str(root/'firmware/main/ble_connection_policy.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
