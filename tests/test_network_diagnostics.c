#include "network_diagnostics.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    network_diagnostics_t d={.reason=-1};
    network_diagnostics_disconnect(&d,8); /* scan/cleanup before an APPLY */
    assert(d.reason==-1 && d.stage==0 && !d.got_ip);
    for (unsigned stage=NET_STAGE_SET_CONFIG;stage<=NET_STAGE_SAVE;stage++) {
        network_diagnostics_begin(&d);
        network_diagnostics_api(&d,stage,-123);
        network_diagnostics_finish(&d);
        network_diagnostics_api(&d,NET_STAGE_CONNECT,0);
        network_diagnostics_got_ip(&d);
        network_diagnostics_disconnect(&d,8);
        assert(d.stage==stage && d.api_err==-123 && d.reason==-1 && !d.got_ip);
    }
    network_diagnostics_begin(&d);
    network_diagnostics_api(&d,NET_STAGE_WAIT_IP,0);
    network_diagnostics_got_ip(&d);
    network_diagnostics_disconnect(&d,203);
    network_diagnostics_disconnect(&d,8);
    network_diagnostics_finish(&d);
    assert(d.reason==203 && d.got_ip==1 && d.stage==NET_STAGE_WAIT_IP);
    network_diagnostics_disconnect(&d,202);
    assert(d.reason==203); /* intentional cleanup cannot overwrite the snapshot */
    network_diagnostics_begin(&d);
    network_diagnostics_api(&d,NET_STAGE_WAIT_IP,0);
    network_diagnostics_finish(&d); /* timeout with no event, not fabricated esp_err */
    assert(d.reason==-1 && !d.got_ip && d.api_err==0);
    network_diagnostics_begin(&d);
    network_diagnostics_got_ip(&d);
    network_diagnostics_api(&d,NET_STAGE_SAVE,0);
    network_diagnostics_finish(&d);
    assert(d.got_ip==1 && d.stage==NET_STAGE_SAVE && d.api_err==0);

    network_scan_row_t rows[3]={{{'A'},-54,3,2},{{'B'},-127,255,14},{{'C'},0,0,1}};
    char out[512];
    size_t n=network_scan_page_json(out,sizeof(out),rows,3,12,6);
    assert(n==strlen(out));
    assert(!strcmp(out,"{\"scan\":[[\"41\",-54,3,2],[\"42\",-127,255,14],[\"43\",0,0,1]],\"total\":12,\"offset\":6}"));
    memset(rows[0].ssid,0xff,32);
    memcpy(rows[1].ssid,rows[0].ssid,32);memcpy(rows[2].ssid,rows[0].ssid,32);
    n=network_scan_page_json(out,sizeof(out),rows,3,12,9);
    assert(n>64*3 && n<512);
    /* Every truncated buffer returns failure, with both outer guards intact. */
    for(size_t size=0;size<=n;size++) {
        char guarded[514];memset(guarded,'!',sizeof(guarded));
        assert(network_scan_page_json(guarded+1,size,rows,3,12,9)==0);
        assert(guarded[0]=='!' && guarded[size+1]=='!');
    }
    assert(network_scan_page_json(out,n+1,rows,3,12,9)==n);
    assert(network_scan_page_json(out,sizeof(out),rows,4,12,0)==0);
    assert(network_scan_page_json(out,sizeof(out),NULL,0,0,0)>0);
    puts("network diagnostics and scan JSON bounds: PASS");
}
