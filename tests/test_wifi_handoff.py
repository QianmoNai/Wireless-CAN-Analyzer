"""Offline fault injection of the production BLE release adapter; no hardware."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]


def function(path, marker):
    source = (root / path).read_text()
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if not depth:
            return source[start:end+1]
    raise AssertionError(marker)


prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#define CONFIG_BLE_CONNECTION_POLICY 0
#define BLE_HS_CONN_HANDLE_NONE 65535
#define BLE_ERR_REM_USER_CONN_TERM 19
#define pdMS_TO_TICKS(n) (n)
struct ble_npl_event { int unused; };
static struct ble_npl_event wifi_radio_event;
static atomic_bool wifi_paused,wifi_pause_ready,apply_acknowledged;
static atomic_int connection=3;
static bool advertising=true,queued,complete_disconnect=true;
static int stop_rc,terminate_rc,terminated,resumed,waits;
static uint32_t clock_ms;
static int ble_gap_adv_active(void){return advertising;}
static int ble_gap_adv_stop(void){if(!stop_rc)advertising=false;return stop_rc;}
static int ble_gap_terminate(int handle,int reason){assert(handle==3&&reason==19);terminated++;return terminate_rc;}
static void advertise(void){assert(!atomic_load(&wifi_paused));resumed++;advertising=true;}
static void *nimble_port_get_dflt_eventq(void){return NULL;}
static void ble_npl_eventq_put(void*q,struct ble_npl_event*e){(void)q;(void)e;queued=true;}
static void wifi_radio_change(struct ble_npl_event*);
static void vTaskDelay(unsigned ms){
 clock_ms+=ms;waits++;
 if(queued){queued=false;wifi_radio_change(NULL);}
 if(complete_disconnect&&terminated&&!terminate_rc&&waits>=2)atomic_store(&connection,BLE_HS_CONN_HANDLE_NONE);
}
static uint32_t millis(void){return clock_ms;}
'''
main = r'''
int main(void){
 // No receipt after APPLY: never release BLE or proceed toward Wi-Fi.
 assert(!release_ble_for_wifi()&&clock_ms==2000&&!terminated&&!queued);
 atomic_store(&apply_acknowledged,true);waits=0;
 assert(release_ble_for_wifi());
 assert(terminated==1&&waits>=2&&!advertising&&atomic_load(&wifi_paused));
 // Failure recovery happens on the host queue; no bond operation is called.
 ble_bridge_wifi_resume();assert(queued);vTaskDelay(10);
 assert(resumed==1&&advertising&&!atomic_load(&wifi_paused));
 // Failed terminate must not be treated as a released RF connection.
 atomic_store(&connection,3);terminate_rc=7;waits=0;
 assert(!ble_bridge_wifi_pause()&&waits==200);
 ble_bridge_wifi_resume();vTaskDelay(10);assert(!atomic_load(&wifi_paused));
 // Accepted terminate with no disconnect callback also fails closed.
 terminate_rc=0;complete_disconnect=false;waits=0;
 assert(!ble_bridge_wifi_pause()&&waits==200);
 ble_bridge_wifi_resume();vTaskDelay(10);
 puts("PASS: APPLY receipt fence, host-queue pause/resume, disconnect completion and bounded failures");
}
'''
adapter = '\n'.join(function('firmware/main/ble_bridge.c', marker) for marker in (
    'static void wifi_radio_change(', 'bool ble_bridge_wifi_pause(', 'void ble_bridge_wifi_resume('))
fence = function('firmware/main/network_link.c', 'static bool release_ble_for_wifi(')
with tempfile.TemporaryDirectory(prefix='wifi-handoff-') as directory:
    source = Path(directory) / 'test.c'
    source.write_text(prelude + adapter + fence + main)
    binary = str(Path(directory) / 'test')
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', binary], check=True)
    subprocess.run([binary], check=True)
