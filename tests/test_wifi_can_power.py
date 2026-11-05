"""Production CAN Wi-Fi power-policy API and readback fault injection."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'firmware/main/network_link.c').read_text()
start = source.index('static bool can_wifi_power_update(')
opening = source.index('{', start)
depth = 0
for end in range(opening, len(source)):
    depth += (source[end] == '{') - (source[end] == '}')
    if not depth:
        function = source[start:end+1]
        break

fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
typedef enum {WIFI_PS_NONE,WIFI_PS_MIN_MODEM,WIFI_PS_MAX_MODEM} wifi_ps_type_t;
#define ESP_OK 0
static wifi_ps_type_t state;
static unsigned gets,sets,fail_get;
static bool fail_set,lie;
static int esp_wifi_get_ps(wifi_ps_type_t *value){
 gets++;if(gets==fail_get)return -1;
 *value=lie&&sets?WIFI_PS_MIN_MODEM:state;return ESP_OK;
}
static int esp_wifi_set_ps(wifi_ps_type_t value){
 sets++;if(fail_set)return -1;state=value;return ESP_OK;
}
/* PRODUCTION_FUNCTION */
static void reset(wifi_ps_type_t initial){state=initial;gets=sets=fail_get=0;fail_set=lie=false;}
int main(void){
 reset(WIFI_PS_MIN_MODEM);assert(can_wifi_power_update(true));
 assert(state==WIFI_PS_NONE&&gets==2&&sets==1);
 gets=sets=0;assert(can_wifi_power_update(true));assert(gets==1&&!sets);
 assert(can_wifi_power_update(false)&&state==WIFI_PS_MIN_MODEM&&sets==1);
 reset(WIFI_PS_MIN_MODEM);assert(can_wifi_power_update(false)&&!sets);
 reset(WIFI_PS_MIN_MODEM);fail_get=1;assert(!can_wifi_power_update(true)&&!sets);
 reset(WIFI_PS_MIN_MODEM);fail_set=true;assert(!can_wifi_power_update(true)&&state==WIFI_PS_MIN_MODEM);
 reset(WIFI_PS_MIN_MODEM);fail_get=2;assert(!can_wifi_power_update(true));
 reset(WIFI_PS_MIN_MODEM);lie=true;assert(!can_wifi_power_update(true));
 puts("PASS: CAN Wi-Fi wake, idle/UART restore, API failure and readback mismatch");
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-can-power-') as directory:
    path = Path(directory)
    c, binary = path/'power.c', path/'power'
    c.write_text(fixture.replace('/* PRODUCTION_FUNCTION */', function))
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(binary)], check=True)
    print(subprocess.check_output([str(binary)], text=True).strip())
