#include "network_diagnostics.h"
#include <stdarg.h>
#include <stdio.h>
#include <inttypes.h>
#include <string.h>

void network_diagnostics_begin(network_diagnostics_t *d, uint64_t now_ms) {
    uint32_t next=d->attempt_id+1;
    *d = (network_diagnostics_t){.reason=-1, .active=true,
        .attempt_id=next?next:1, .began_ms=now_ms,
        .start_ms=-1, .connected_ms=-1, .ip_ms=-1, .rssi=INT8_MIN, .tx_power=INT8_MIN};
}
void network_diagnostics_api(network_diagnostics_t *d, uint8_t stage, int32_t err) {
    if (d->active) { d->stage=stage; d->api_err=err; }
}
static int64_t elapsed(const network_diagnostics_t *d, uint64_t now_ms) {
    /* An APPLY normally lasts 20 s. Never wrap a late/invalid event timestamp. */
    return now_ms>=d->began_ms && now_ms-d->began_ms<=UINT32_MAX ?
        (int64_t)(now_ms-d->began_ms) : -1;
}
void network_diagnostics_start(network_diagnostics_t *d, uint64_t now_ms) {
    if (d->active && d->start_ms==-1) d->start_ms=elapsed(d,now_ms);
}
void network_diagnostics_connected(network_diagnostics_t *d, uint64_t now_ms,
                                   const uint8_t bssid[6], uint8_t channel) {
    if (d->active && d->connected_ms==-1) {
        d->connected_ms=elapsed(d,now_ms);
        memcpy(d->bssid,bssid,6);d->channel=channel;
    }
}
void network_diagnostics_disconnect(network_diagnostics_t *d, uint8_t reason,
                                    const uint8_t bssid[6], int8_t rssi) {
    if (!d->active) return;
    if (d->disconnect_count<UINT16_MAX) d->disconnect_count++;
    /* Keep the first failure, not a subsequent event caused by teardown. */
    if (d->reason == -1) {
        d->reason=reason;d->rssi=rssi;memcpy(d->bssid,bssid,6);
    }
}
void network_diagnostics_got_ip(network_diagnostics_t *d, uint64_t now_ms) {
    if (d->active && !d->got_ip) { d->got_ip=1;d->ip_ms=elapsed(d,now_ms); }
}
void network_diagnostics_finish(network_diagnostics_t *d) { d->active=false; }

static void mac_hex(char out[13], const uint8_t mac[6]) {
    unsigned any=0;
    for (unsigned i=0;i<6;i++) { snprintf(out+2*i,3,"%02x",mac[i]);any|=mac[i]; }
    if (!any) out[0]=0;
}
size_t network_status_json(char *out, size_t size, const network_status_t *s,
                           const network_diagnostics_t *d) {
    if (size>NETWORK_STATUS_JSON_LIMIT) size=NETWORK_STATUS_JSON_LIMIT;
    char bssid[13],mac[13];mac_hex(bssid,d->bssid);mac_hex(mac,d->sta_mac);
    /* diag v1: attempt, origin, START/CONNECTED/GOT_IP offsets, disconnects,
     * BSSID, RSSI, actual radio channel, country, TX quarter-dBm, STA MAC,
     * actual BLE interval (1.25 ms). No new credential access or wire packets. */
    int n=snprintf(out,size,
        "{\"state\":%u,\"error\":%u,\"busy\":%s,\"mode\":%u,\"ip\":\"%.15s\",\"ssid\":\"%.64s\",\"id\":\"%.12s\",\"fp\":\"%.64s\",\"token\":\"%.64s\",\"port\":32323,\"reason\":%"PRId32",\"stage\":%u,\"api_err\":%"PRId32",\"got_ip\":%u,"
        "\"diag\":[%"PRIu32",%"PRIu64",%"PRId64",%"PRId64",%"PRId64",%u,\"%s\",%d,%u,\"%.2s\",%d,\"%s\",%u]}",
        s->state,s->error,s->busy?"true":"false",(unsigned)s->mode,
        s->ip,s->ssid,s->id,s->fp,s->token,d->reason,d->stage,d->api_err,d->got_ip,
        d->attempt_id,d->began_ms,d->start_ms,d->connected_ms,d->ip_ms,
        d->disconnect_count,bssid,d->rssi,d->channel,d->country,d->tx_power,mac,d->ble_interval);
    return n>0&&(size_t)n<size?(size_t)n:0;
}

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
