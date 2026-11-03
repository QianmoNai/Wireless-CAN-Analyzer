"""Test the production socket option and QUERY extension boundary offline."""
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[1]
source=(root/'firmware/main/network_link.c').read_text()
adapter=source.split('static bool data_socket_options(int fd){',1)[1].split('\n}',1)[0]
adapter='static bool data_socket_options(int fd){'+adapter+'\n}\n'
test=r'''
#include "wifi_uart_diag.h"
#include <assert.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
static int result,calls;
int64_t esp_timer_get_time(void){return 1000000;}
int setsockopt(int fd,int level,int option,const void *value,socklen_t n){
 assert(fd==23&&level==IPPROTO_TCP&&option==TCP_NODELAY&&n==sizeof(int));
 assert(*(const int*)value==1);calls++;errno=ENOPROTOOPT;return result;
}
'''+adapter+r'''
int main(void){
 wifi_diag_boot(1);wifi_diag_mode(1);wifi_diag_snapshot_t s;
 assert(data_socket_options(23)&&calls==1);wifi_diag_snapshot(&s);
 assert(s.records[WD_TCP_NODELAY].value==1);
 result=-1;assert(!data_socket_options(23)&&calls==2);wifi_diag_snapshot(&s);
 assert(s.records[WD_SOCKET_LAST_ERRNO].value==ENOPROTOOPT);
 assert(s.records[WD_TCP_CLOSE_REASON].value==WD_CLOSE_OPTIONS);
}
'''
with tempfile.TemporaryDirectory(prefix='wifi-socket-diag-') as d:
    c=Path(d)/'test.c';c.write_text(test);binary=Path(d)/'test'
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(root/'firmware/main'),
                    '-I'+str(root/'tests/stubs'),str(c),str(root/'firmware/main/wifi_uart_diag.c'),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
# Diagnostics are dispatched only after authenticated TLS establishment; BLE
# and CUSB command validators retain their old settings-only op allowlist.
assert source.index('if(memcmp(auth,"CAN1",4)')<source.index('if(wifi_diag_query_valid(incoming))')
assert 'wifi_diag_query_valid' not in (root/'firmware/main/ble_bridge.c').read_text()
assert 'wifi_diag_query_valid' not in (root/'firmware/main/usb_link.c').read_text()
print('PASS: device TCP_NODELAY, option error classification, TLS-only read-only diagnostics')
