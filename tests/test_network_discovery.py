"""Exercise the production UDP discovery handler, including legacy wire form."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'firmware/main/network_link.c').read_text()
start = source.index('static void discovery(int fd)')
end = source.index('\nstatic void ', start + 1)
handler = source[start:end]
fixture = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#define NET_PORT 32323
static const char *device_id="fixture-id";
static unsigned char incoming[100];
static size_t input_size;
static int replies;
static ssize_t fake_recvfrom(int fd,void *out,size_t size,int flags,struct sockaddr *peer,socklen_t *length){
 (void)flags;(void)peer;assert(fd==7&&*length==sizeof(struct sockaddr_in));
 size_t used=input_size<size?input_size:size;memcpy(out,incoming,used);return (ssize_t)used;
}
static ssize_t fake_sendto(int fd,const void *out,size_t size,int flags,const struct sockaddr *peer,socklen_t length){
 (void)flags;(void)peer;assert(fd==7&&length==sizeof(struct sockaddr_in));
 const char expected[]="CAN_SCOPE_1 fixture-id 32323";
 assert(size==sizeof(expected)-1&&!memcmp(out,expected,size));replies++;return (ssize_t)size;
}
#define recvfrom fake_recvfrom
#define sendto fake_sendto
'''
main = r'''
int main(void){
 const char query[]="CAN_SCOPE_DISCOVER_1";
 for(size_t n=0;n<sizeof(incoming);n++){
  memset(incoming,0,sizeof(incoming));memcpy(incoming,query,sizeof(query)-1);input_size=n;replies=0;discovery(7);
  assert(replies==(n==19||n==20));
 }
 // No truncated prefix, altered version, embedded NUL or suffix is accepted.
 for(size_t n=19;n<=20;n++)for(size_t i=0;i<n;i++){
  memcpy(incoming,query,20);incoming[i]^=0x20;input_size=n;replies=0;discovery(7);assert(!replies);
 }
 puts("UDP discovery: complete 20-byte request, exact legacy prefix and malformed rejection PASS");
}
'''
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary)
    (path / 'test.c').write_text(fixture + handler + main)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
