"""Exercise production socket options: CAN experiments cannot alter UART."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'firmware/main/network_link.c').read_text()
start = source.index('static bool data_socket_options(')
opening = source.index('{', start)
depth = 0
for end in range(opening, len(source)):
    depth += (source[end] == '{') - (source[end] == '}')
    if not depth:
        function = source[start:end+1]
        break

fixture = r'''
#include <assert.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <sys/socket.h>
#include "wifi_uart_diag.h"
static bool uart, reject;
static int submitted;
static uint32_t records[WD_FIELDS];
bool uart_bridge_selected(void){return uart;}
void wifi_diag_set(unsigned field,uint32_t value,uint32_t sequence){
 assert(sequence==WD_NOW_SEQUENCE);records[field]=value;
}
static int options(int fd,int level,int key,const void *value,socklen_t size){
 assert(fd==42&&level==IPPROTO_TCP&&key==TCP_NODELAY&&size==sizeof(int));
 submitted=*(const int*)value;
 if(reject){errno=ENOPROTOOPT;return -1;}return 0;
}
#define setsockopt options
/* PRODUCTION_FUNCTION */
int main(void){
 for(unsigned mode=0;mode<2;mode++){
  uart=mode;reject=false;
  assert(data_socket_options(42));
  int expected=uart?EXPECTED_UART:EXPECTED_CAN;
  assert(submitted==expected&&records[WD_TCP_NODELAY]==(unsigned)expected);
  reject=true;records[WD_TCP_NODELAY]=99;
  assert(!data_socket_options(42));
  assert(records[WD_SOCKET_LAST_ERRNO]==ENOPROTOOPT);
  assert(records[WD_TCP_CLOSE_REASON]==WD_CLOSE_OPTIONS);
  assert(records[WD_TCP_NODELAY]==99);
 }
 puts("PASS: production CAN/UART socket selection and option failure fencing");
}
'''

with tempfile.TemporaryDirectory(prefix='wifi-can-options-') as directory:
    path = Path(directory)
    c = path/'options.c'
    c.write_text(fixture.replace('/* PRODUCTION_FUNCTION */', function))
    for global_option, can_option, expected_can, expected_uart in (
            (0, None, 0, 0), (0, 0, 0, 0), (0, 1, 1, 0), (1, 0, 1, 1)):
        binary = path/f'options-{global_option}-{can_option}'
        flags = [f'-DCAN_WIFI_TCP_NODELAY={global_option}',
                 f'-DEXPECTED_CAN={expected_can}', f'-DEXPECTED_UART={expected_uart}']
        if can_option is not None:
            flags.append(f'-DCAN_WIFI_CAN_TCP_NODELAY={can_option}')
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', *flags,
                        '-I'+str(ROOT/'firmware/main'), '-I'+str(ROOT/'tests/stubs'),
                        str(c), '-o', str(binary)], check=True)
        print(subprocess.check_output([str(binary)], text=True).strip())
