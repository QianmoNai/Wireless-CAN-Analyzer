"""Exercise the production STA power adapter offline, with driver failures."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'firmware/main/network_link.c').read_text()
adapter = source.split('static esp_err_t start_station_radio(void){', 1)[1].split('\n}', 1)[0]
adapter = 'static esp_err_t start_station_radio(void){' + adapter + '\n}\n'
harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdio.h>
typedef int esp_err_t;
#define ESP_OK 0
static atomic_bool radio_on;
static int start_rc,power_rc,starts,powers;
static esp_err_t esp_wifi_start(void){
 assert(!atomic_load(&radio_on));starts++;return start_rc;
}
static esp_err_t esp_wifi_set_max_tx_power(int power){
 assert(atomic_load(&radio_on));assert(power==32);powers++;return power_rc;
}
'''
main = r'''
int main(void){
 // Cold start orders start before setting the same cap as the proven test app.
 assert(start_station_radio()==ESP_OK);
 assert(starts==1&&powers==1&&atomic_load(&radio_on));
 // Scan followed by APPLY/reconnect must also set the cap on a warm radio.
 assert(start_station_radio()==ESP_OK&&starts==1&&powers==2);
 // Propagate power errors: callers must not proceed with the default high cap.
 power_rc=42;assert(start_station_radio()==42&&starts==1&&powers==3);
 // A start failure must never claim radio-on or call the power API.
 atomic_store(&radio_on,false);start_rc=43;
 assert(start_station_radio()==43&&starts==2&&powers==3&&!atomic_load(&radio_on));
 start_rc=power_rc=0;assert(start_station_radio()==ESP_OK&&starts==3&&powers==4);
 puts("PASS: STA start/power ordering, warm radio cap, error propagation and recovery");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-power-') as directory:
    path = Path(directory) / 'test.c'
    path.write_text(harness + adapter + main)
    binary = str(Path(directory) / 'test')
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(path), '-o', binary], check=True)
    subprocess.run([binary], check=True)
