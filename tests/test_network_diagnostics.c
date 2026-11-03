#include "network_diagnostics.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    network_diagnostics_t d={.reason=-1};
    const uint8_t ap[6]={0xdc,0xd8,0x7b,0x53,0xee,0x5c};
    network_diagnostics_disconnect(&d,8,ap,-59); /* scan/cleanup before an APPLY */
    assert(d.reason==-1 && d.stage==0 && !d.got_ip);
    for (unsigned stage=NET_STAGE_SET_CONFIG;stage<=NET_STAGE_SAVE;stage++) {
        network_diagnostics_begin(&d,1000);
        network_diagnostics_api(&d,stage,-123);
        network_diagnostics_finish(&d);
        network_diagnostics_api(&d,NET_STAGE_CONNECT,0);
        network_diagnostics_got_ip(&d,1010);
        network_diagnostics_disconnect(&d,8,ap,-59);
        assert(d.stage==stage && d.api_err==-123 && d.reason==-1 && !d.got_ip);
    }
    network_diagnostics_begin(&d,1000);
    network_diagnostics_start(&d,1002);
    network_diagnostics_connected(&d,1005,ap,1);
    network_diagnostics_api(&d,NET_STAGE_WAIT_IP,0);
    network_diagnostics_got_ip(&d,1008);
    network_diagnostics_disconnect(&d,203,ap,-59);
    network_diagnostics_disconnect(&d,8,ap,-1);
    network_diagnostics_finish(&d);
    assert(d.reason==203 && d.got_ip==1 && d.stage==NET_STAGE_WAIT_IP);
    assert(d.start_ms==2 && d.connected_ms==5 && d.ip_ms==8 && d.disconnect_count==2);
    assert(d.rssi==-59 && d.channel==1 && !memcmp(d.bssid,ap,6));
    network_diagnostics_disconnect(&d,202,ap,-1);
    network_diagnostics_start(&d,1011);
    network_diagnostics_connected(&d,1012,ap,11);
    network_diagnostics_got_ip(&d,1013);
    assert(d.start_ms==2 && d.connected_ms==5 && d.ip_ms==8 && d.disconnect_count==2 && d.channel==1);
    assert(d.reason==203); /* intentional cleanup cannot overwrite the snapshot */
    uint32_t previous=d.attempt_id;
    network_diagnostics_begin(&d,UINT64_C(4294967300)); /* no 49-day uptime wrap */
    assert(d.attempt_id==previous+1 && d.began_ms==UINT64_C(4294967300));
    assert(d.start_ms==-1 && d.connected_ms==-1 && d.ip_ms==-1 && !d.disconnect_count);
    network_diagnostics_api(&d,NET_STAGE_WAIT_IP,0);
    network_diagnostics_finish(&d); /* timeout with no event, not fabricated esp_err */
    assert(d.reason==-1 && !d.got_ip && d.api_err==0);
    network_diagnostics_begin(&d,1000);
    network_diagnostics_got_ip(&d,1008);
    network_diagnostics_api(&d,NET_STAGE_SAVE,0);
    network_diagnostics_finish(&d);
    assert(d.got_ip==1 && d.stage==NET_STAGE_SAVE && d.api_err==0);

    /* The production STATUS serializer, with every field at its largest legal
     * width, includes the original TLS fields and fits 512 B INCLUDING NUL. */
    char hex[65];memset(hex,'f',64);hex[64]=0;
    network_status_t status={UINT8_MAX,UINT8_MAX,false,true,"255.255.255.255",hex,hex,hex,hex};
    d=(network_diagnostics_t){.reason=UINT8_MAX,.api_err=INT32_MIN,.stage=5,.got_ip=1,
        .attempt_id=UINT32_MAX,.began_ms=INT64_MAX/1000,
        .start_ms=UINT32_MAX,.connected_ms=UINT32_MAX,.ip_ms=UINT32_MAX,
        .disconnect_count=UINT16_MAX,.ble_interval=UINT16_MAX,.channel=UINT8_MAX,
        .rssi=INT8_MIN,.tx_power=INT8_MIN,.country="ZZ"};
    memset(d.bssid,0xff,6);memset(d.sta_mac,0xff,6);
    char status_out[NETWORK_STATUS_JSON_LIMIT];
    size_t status_n=network_status_json(status_out,sizeof(status_out),&status,&d);
    assert(status_n==511 && strlen(status_out)==status_n && status_out[status_n]==0);
    assert(strstr(status_out,"\"diag\":[4294967295,9223372036854775,4294967295"));
    assert(!strcmp(status_out+status_n-2,"]}"));
    for(size_t size=0;size<=status_n;size++) {
        char guarded[514];memset(guarded,'!',sizeof(guarded));
        assert(network_status_json(guarded+1,size,&status,&d)==0);
        assert(guarded[0]=='!' && guarded[size+1]=='!');
    }
    printf("STATUS worst case: %zu bytes + NUL <= %u\n",status_n,NETWORK_STATUS_JSON_LIMIT);
    d.active=true;network_diagnostics_disconnect(&d,2,ap,-59);
    assert(d.disconnect_count==UINT16_MAX); /* saturated, not wrapped */
    network_diagnostics_begin(&d,0);assert(d.attempt_id==1); /* wrap skips no-APPLY ID 0 */

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
