#include "network_diagnostics.h"
#include <stdarg.h>
#include <stdio.h>

void network_diagnostics_begin(network_diagnostics_t *d) {
    *d = (network_diagnostics_t){.reason=-1, .active=true};
}
void network_diagnostics_api(network_diagnostics_t *d, uint8_t stage, int32_t err) {
    if (d->active) { d->stage=stage; d->api_err=err; }
}
void network_diagnostics_disconnect(network_diagnostics_t *d, unsigned reason) {
    /* Keep the first failure, not a subsequent event caused by teardown. */
    if (d->active && d->reason == -1) d->reason=(int32_t)reason;
}
void network_diagnostics_got_ip(network_diagnostics_t *d) {
    if (d->active) d->got_ip=1;
}
void network_diagnostics_finish(network_diagnostics_t *d) { d->active=false; }

static bool append(char *out, size_t size, size_t *at, const char *format, ...) {
    if (*at >= size) return false;
    va_list args;
    va_start(args,format);
    int n=vsnprintf(out+*at,size-*at,format,args);
    va_end(args);
    if (n < 0 || (size_t)n >= size-*at) return false;
    *at+=(size_t)n;
    return true;
}
size_t network_scan_page_json(char *out, size_t size, const network_scan_row_t *rows,
                              unsigned count, unsigned total, unsigned offset) {
    size_t at=0;
    if (count>3 || !append(out,size,&at,"{\"scan\":[")) return 0;
    for (unsigned i=0;i<count;i++) {
        char ssid[65]; unsigned n=0;
        const char *hex="0123456789abcdef";
        while (n<32 && rows[i].ssid[n]) {
            ssid[2*n]=hex[rows[i].ssid[n]>>4];
            ssid[2*n+1]=hex[rows[i].ssid[n]&15]; n++;
        }
        ssid[2*n]=0;
        if (!append(out,size,&at,"%s[\"%s\",%d,%d,%d]",i?",":"",ssid,
                    rows[i].rssi,rows[i].authmode,rows[i].primary)) return 0;
    }
    if (!append(out,size,&at,"],\"total\":%u,\"offset\":%u}",total,offset)) return 0;
    return at;
}
