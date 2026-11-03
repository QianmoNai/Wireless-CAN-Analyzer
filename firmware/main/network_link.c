#include "network_link.h"
#include "usb_link.h"
#include "uart_bridge.h"
#include "dual_link.h"
#include "network_protocol.h"
#include "network_diagnostics.h"
#include "network_identity.h"
#include "wifi_uart_diag.h"
#include "device_settings.h"
#include "ack_delivery.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_tls.h"
#include "nvs.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "lwip/sockets.h"
#include "lwip/tcp.h"
#include <stdatomic.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <fcntl.h>
#define NET_PORT 32323
#define SCAN_MAX 12
static atomic_uint transport_mode;
static atomic_bool linked, online, identity_ready, started, radio_on;
static atomic_bool apply_pending, apply_acknowledged;
static atomic_uint pairing=UINT32_MAX,pairing_until,station_ip,disconnect_reason,network_epoch,disconnect_events;
static SemaphoreHandle_t lock;
static QueueHandle_t requests,raw_out,control_out;
static network_assembly_t assembly;
static network_credentials_t saved;
static wifi_ap_record_t scan_records[SCAN_MAX];
static unsigned scan_count,scan_page;
static bool scan_reply,busy;
static uint8_t state,error_code;
static network_diagnostics_t diagnostics={.reason=-1,.start_ms=-1,.connected_ms=-1,.ip_ms=-1,.rssi=INT8_MIN,.tx_power=INT8_MIN};
static char device_id[13];
typedef struct { uint8_t op; network_credentials_t credentials; } request_t;
static uint32_t millis(void){return esp_timer_get_time()/1000;}
static void hex_bytes(char *out,const uint8_t *p,size_t n){const char *h="0123456789abcdef";for(size_t i=0;i<n;i++){out[2*i]=h[p[i]>>4];out[2*i+1]=h[p[i]&15];}out[n*2]=0;}
static void set_status(uint8_t s,uint8_t error){xSemaphoreTake(lock,portMAX_DELAY);state=s;error_code=error;xSemaphoreGive(lock);}
void network_pairing_code(uint32_t code){atomic_store(&pairing_until,millis()+60000);atomic_store(&pairing,code);}
void network_pairing_end(void){atomic_store(&pairing,UINT32_MAX);}
void network_display(uint8_t *s,char ip[16],uint32_t *pin){
 *s=0;ip[0]=0;*pin=UINT32_MAX;if(!lock)return;
 xSemaphoreTake(lock,portMAX_DELAY);*s=state;xSemaphoreGive(lock);
 struct in_addr addr={.s_addr=atomic_load(&station_ip)};if(addr.s_addr)inet_ntoa_r(addr,ip,16);
 if((int32_t)(atomic_load(&pairing_until)-millis())>0)*pin=atomic_load(&pairing);
}
bool network_wifi_mode(void){return atomic_load(&transport_mode)==1;}
bool network_usb_mode(void){return atomic_load(&transport_mode)==4;}
bool network_dual_mode(void){unsigned mode=atomic_load(&transport_mode);return mode>=DUAL_A&&mode<=DUAL_B;}
bool network_connected(void){return network_wifi_mode()&&atomic_load(&linked)&&atomic_load(&online);}
void network_set_mode(unsigned mode){
 if(mode>4)return;
 if(atomic_exchange(&transport_mode,mode)!=mode){usb_link_reset();dual_link_request_mode(mode);atomic_fetch_add(&network_epoch,1);ble_bridge_reset_session();wifi_diag_mode(mode);if(mode==0)ble_bridge_wifi_resume();}
}
bool network_send(const bridge_packet_t *p){
 if(!network_connected()){wifi_diag_drop(WD_LAN_REJECT_OFFLINE,p);return false;}
 bool raw=bridge_packet_is_raw(p);QueueHandle_t q=raw?raw_out:control_out;
 bool ok=xQueueSend(q,p,0)==pdTRUE;
 wifi_diag_queue(raw?WD_LAN_RAW_DEPTH:WD_LAN_CONTROL_DEPTH,
                 raw?WD_LAN_RAW_HIGH:WD_LAN_CONTROL_HIGH,uxQueueMessagesWaiting(q));
 if(!ok)wifi_diag_drop(WD_LAN_REJECT_FULL,p); // Rejection observation, not another drop owner.
 return ok;
}
static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data){
 uint64_t now_ms=(uint64_t)(esp_timer_get_time()/1000);
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_START){xSemaphoreTake(lock,portMAX_DELAY);network_diagnostics_start(&diagnostics,now_ms);xSemaphoreGive(lock);}
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_CONNECTED){wifi_event_sta_connected_t *e=data;xSemaphoreTake(lock,portMAX_DELAY);network_diagnostics_connected(&diagnostics,now_ms,e->bssid,e->channel);xSemaphoreGive(lock);}
 if(base==IP_EVENT&&id==IP_EVENT_STA_GOT_IP&&!network_dual_mode()){ip_event_got_ip_t *e=data;xSemaphoreTake(lock,portMAX_DELAY);network_diagnostics_got_ip(&diagnostics,now_ms);xSemaphoreGive(lock);atomic_store(&station_ip,e->ip_info.ip.addr);atomic_store(&online,true);}
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_DISCONNECTED){wifi_event_sta_disconnected_t *e=data;xSemaphoreTake(lock,portMAX_DELAY);network_diagnostics_disconnect(&diagnostics,e->reason,e->bssid,e->rssi);xSemaphoreGive(lock);wifi_diag_add(WD_WIFI_DISCONNECTS,1,WD_NOW_SEQUENCE);wifi_diag_set(WD_WIFI_REASON,e->reason,WD_NOW_SEQUENCE);atomic_fetch_add(&disconnect_events,1);atomic_store(&disconnect_reason,e->reason);atomic_store(&online,false);atomic_store(&station_ip,0);}
}
static void capture_radio(void){
 /* Read only public driver getters. Never read wifi_config/SSID/password. */
 uint8_t mac[6]={0},channel=0;wifi_second_chan_t second;wifi_country_t country={0};
 int8_t power=INT8_MIN;int rssi=INT8_MIN;
 if(esp_wifi_get_mac(WIFI_IF_STA,mac)!=ESP_OK)memset(mac,0,sizeof(mac));
 if(esp_wifi_get_channel(&channel,&second)!=ESP_OK)channel=0;
 if(esp_wifi_get_country(&country)!=ESP_OK)memset(&country,0,sizeof(country));
 if(esp_wifi_get_max_tx_power(&power)!=ESP_OK)power=INT8_MIN;
 if(esp_wifi_sta_get_rssi(&rssi)!=ESP_OK)rssi=INT8_MIN;
 uint16_t interval=ble_bridge_connection_interval();
 xSemaphoreTake(lock,portMAX_DELAY);
 if(diagnostics.active){
  memcpy(diagnostics.sta_mac,mac,6);diagnostics.tx_power=power;diagnostics.ble_interval=interval;
  if(!diagnostics.channel)diagnostics.channel=channel; /* CONNECTED channel wins */
  if(diagnostics.reason==-1&&rssi>=INT8_MIN&&rssi<=INT8_MAX)diagnostics.rssi=(int8_t)rssi;
  if((country.cc[0]>='A'&&country.cc[0]<='Z'&&country.cc[1]>='A'&&country.cc[1]<='Z')||
     (country.cc[0]=='0'&&country.cc[1]=='1')){diagnostics.country[0]=country.cc[0];diagnostics.country[1]=country.cc[1];}
 }
 xSemaphoreGive(lock);
}
static esp_err_t save_credentials(const network_credentials_t *c){nvs_handle_t h;esp_err_t e=nvs_open("can_network",NVS_READWRITE,&h);if(e!=ESP_OK)return e;e=c?nvs_set_blob(h,"wifi",c,sizeof(*c)):nvs_erase_key(h,"wifi");if(e==ESP_ERR_NVS_NOT_FOUND)e=ESP_OK;if(e==ESP_OK)e=nvs_commit(h);nvs_close(h);return e;}
static void station_api(bool capture,uint8_t stage,esp_err_t err){
 if(capture){xSemaphoreTake(lock,portMAX_DELAY);network_diagnostics_api(&diagnostics,stage,err);xSemaphoreGive(lock);}
}
static esp_err_t start_station_radio(void){
 if(!atomic_load(&radio_on)){
  esp_err_t err=esp_wifi_start();if(err!=ESP_OK)return err;
  atomic_store(&radio_on,true);
 }
 // Same 32 quarter-dBm request as the working 8 dBm TX test; after start,
 // before either active scanning or association, including an already-on radio.
 return esp_wifi_set_max_tx_power(32);
}
static bool release_ble_for_wifi(void){
 uint32_t until=millis()+2000;
 while(!atomic_load(&apply_acknowledged)&&(int32_t)(until-millis())>0)vTaskDelay(pdMS_TO_TICKS(10));
 return atomic_load(&apply_acknowledged)&&ble_bridge_wifi_pause();
}
static bool begin_station(const network_credentials_t *c,bool capture){
 unsigned before=atomic_load(&disconnect_events);
 esp_err_t disconnected=esp_wifi_disconnect();
 // Drain the previous association's event before beginning another attempt.
 if(disconnected==ESP_OK){uint32_t until=millis()+1000;while(before==atomic_load(&disconnect_events)&&(int32_t)(until-millis())>0)vTaskDelay(pdMS_TO_TICKS(10));}
 atomic_store(&online,false);atomic_store(&station_ip,0);atomic_store(&disconnect_reason,0);
 if(capture){xSemaphoreTake(lock,portMAX_DELAY);network_diagnostics_begin(&diagnostics,(uint64_t)(esp_timer_get_time()/1000));xSemaphoreGive(lock);}
 if(capture){
  // A subsequent protected STATUS request proves the central received APPLY's
  // ATT write response. Do not guess ACK delivery from a fixed delay.
  station_api(true,NET_STAGE_CONNECT,ESP_OK);
  if(!release_ble_for_wifi()){
   station_api(true,NET_STAGE_CONNECT,ESP_ERR_TIMEOUT);return false;
  }
 }
 wifi_config_t cfg={0};memcpy(cfg.sta.ssid,c->ssid,c->ssid_len);memcpy(cfg.sta.password,c->password,c->password_len);cfg.sta.pmf_cfg.capable=true;
 station_api(capture,NET_STAGE_SET_CONFIG,ESP_OK);
 esp_err_t err=esp_wifi_set_config(WIFI_IF_STA,&cfg);memset(&cfg,0,sizeof(cfg));station_api(capture,NET_STAGE_SET_CONFIG,err);if(err!=ESP_OK)return false;
 station_api(capture,NET_STAGE_START,ESP_OK);err=start_station_radio();station_api(capture,NET_STAGE_START,err);
 if(err!=ESP_OK)return false;
 station_api(capture,NET_STAGE_CONNECT,ESP_OK);err=esp_wifi_connect();station_api(capture,NET_STAGE_CONNECT,err);
 if(err!=ESP_OK)return false;
 station_api(capture,NET_STAGE_WAIT_IP,ESP_OK);return true;
}
int network_config_write(const uint8_t p[20]){
 if(network_dual_mode()||network_usb_mode())return 3;
 if(!atomic_load(&started)||!atomic_load(&identity_ready))return 14;
 request_t r={.op=p[0]};int result=0;
 xSemaphoreTake(lock,portMAX_DELAY);
 if(p[0]==NET_STATUS){scan_reply=false;if(atomic_load(&apply_pending))atomic_store(&apply_acknowledged,true);}
 else if(p[0]==NET_SCAN_PAGE){scan_reply=true;scan_page=p[2]<SCAN_MAX?p[2]:0;}
 else if(busy){result=17;}
 else if(p[0]==NET_BEGIN||p[0]==NET_DATA){if(!network_assembly_packet(&assembly,p,millis(),&r.credentials))result=19;}
 else if(p[0]==NET_APPLY){if(!network_assembly_packet(&assembly,p,millis(),&r.credentials))result=19;else if(xQueueSend(requests,&r,0)!=pdTRUE)result=17;else{atomic_store(&apply_acknowledged,false);atomic_store(&apply_pending,true);busy=true;scan_reply=false;state=1;error_code=0;}}
 else if(p[0]==NET_SCAN||p[0]==NET_CLEAR){if(xQueueSend(requests,&r,0)!=pdTRUE)result=17;else{busy=true;scan_reply=false;error_code=0;}}
 else result=19;
 xSemaphoreGive(lock);memset(&r,0,sizeof(r));return result;
}
void network_config_disconnect(void){if(!lock)return;xSemaphoreTake(lock,portMAX_DELAY);network_assembly_reset(&assembly);scan_reply=false;xSemaphoreGive(lock);network_pairing_end();}
size_t network_config_read(char *out,size_t size){
 if(size<NETWORK_STATUS_JSON_LIMIT)return 0;
 if(!lock)return snprintf(out,size,"{\"state\":4,\"error\":9}");
 xSemaphoreTake(lock,portMAX_DELAY);
 if(atomic_load(&apply_pending))atomic_store(&apply_acknowledged,true);
 int n=0;
 if(scan_reply){
  unsigned end=scan_page+3;if(end>scan_count)end=scan_count;
  network_scan_row_t rows[3];unsigned count=0;
  for(unsigned i=scan_page;i<end;i++){
   network_scan_row_t *row=&rows[count++];memcpy(row->ssid,scan_records[i].ssid,32);
   row->rssi=scan_records[i].rssi;row->authmode=scan_records[i].authmode;row->primary=scan_records[i].primary;
  }
  n=(int)network_scan_page_json(out,size,rows,count,scan_count,scan_page);
 }else{
  char token[65],ssid[65],ip[16]="";hex_bytes(token,network_token,32);hex_bytes(ssid,saved.ssid,saved.ssid_len);
  struct in_addr a={.s_addr=atomic_load(&station_ip)};if(a.s_addr)inet_ntoa_r(a,ip,sizeof(ip));
  network_status_t status={state,error_code,busy,network_wifi_mode(),ip,ssid,device_id,network_fingerprint,token};
  n=(int)network_status_json(out,size,&status,&diagnostics);
 }
 xSemaphoreGive(lock);return n>0&&(size_t)n<size?(size_t)n:0;
}
static int bound_socket(int type){int s=socket(AF_INET,type,0);if(s<0)return -1;int on=1;setsockopt(s,SOL_SOCKET,SO_REUSEADDR,&on,sizeof(on));struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(NET_PORT),.sin_addr.s_addr=htonl(INADDR_ANY)};if(bind(s,(struct sockaddr*)&a,sizeof(a))){close(s);return -1;}fcntl(s,F_SETFL,O_NONBLOCK);return s;}
static void discovery(int fd){
 char message[64];struct sockaddr_in peer;socklen_t len=sizeof(peer);int n=recvfrom(fd,message,sizeof(message),0,(struct sockaddr*)&peer,&len);
 // Reply only to a small, versioned discovery request; never expose credentials.
 if(n==19&&!memcmp(message,"CAN_SCOPE_DISCOVER_1",19)){char reply[100];int count=snprintf(reply,sizeof(reply),"CAN_SCOPE_1 %s %d",device_id,NET_PORT);sendto(fd,reply,count,0,(struct sockaddr*)&peer,len);}
}
static void tls_diagnostic_end(int64_t began,int64_t waiting,uint64_t blocked,bool ok,int error,int socket_error,bool timeout){
 int64_t now=esp_timer_get_time();
 if(waiting>=0)blocked+=(uint64_t)(now-waiting);
 wifi_diag_add(WD_TLS_BLOCK_US_TOTAL,(uint32_t)blocked,WD_NOW_SEQUENCE);
 wifi_diag_max(WD_TLS_BLOCK_US_MAX,(uint32_t)blocked);
 wifi_diag_add(WD_TLS_CALL_US_TOTAL,(uint32_t)(now-began),WD_NOW_SEQUENCE);
 wifi_diag_max(WD_TLS_CALL_US_MAX,(uint32_t)(now-began));
 if(!ok){wifi_diag_add(WD_TLS_SEND_FAILURES,1,WD_NOW_SEQUENCE);
  if(timeout)wifi_diag_add(WD_TLS_SEND_TIMEOUTS,1,WD_NOW_SEQUENCE);
  wifi_diag_set(WD_TLS_LAST_ERROR,(uint32_t)error,WD_NOW_SEQUENCE);
  wifi_diag_set(WD_SOCKET_LAST_ERRNO,(uint32_t)socket_error,WD_NOW_SEQUENCE);}
}
static bool tls_write_count(esp_tls_t *tls,const uint8_t *p,size_t n,size_t *completed){
 int64_t began=esp_timer_get_time(),waiting=-1;uint64_t blocked=0;
 wifi_diag_add(WD_TLS_SEND_CALLS,1,WD_NOW_SEQUENCE);wifi_diag_max(WD_TLS_PENDING_HIGH_BYTES,n);
 *completed=0;uint32_t deadline=millis()+1500;
 while(n){int r=esp_tls_conn_write(tls,p,n);int saved_errno=errno;int64_t now=esp_timer_get_time();
  if(r>0){p+=r;n-=r;*completed+=r;if(waiting>=0){blocked+=(uint64_t)(now-waiting);waiting=-1;}}
  else if(r!=ESP_TLS_ERR_SSL_WANT_READ&&r!=ESP_TLS_ERR_SSL_WANT_WRITE){tls_diagnostic_end(began,waiting,blocked,false,r,saved_errno,false);return false;}
  else {if(waiting<0)waiting=now;wifi_diag_add(WD_TLS_WANT_EVENTS,1,WD_NOW_SEQUENCE);}
  if((int32_t)(millis()-deadline)>=0){tls_diagnostic_end(began,waiting,blocked,false,r,saved_errno,true);return false;}
  if(r<=0)vTaskDelay(pdMS_TO_TICKS(5));
 }tls_diagnostic_end(began,waiting,blocked,true,0,0,false);return true;
}
static bool tls_write_all(esp_tls_t *tls,const uint8_t *p,size_t n){
 size_t completed;return tls_write_count(tls,p,n,&completed);
}
static bool tls_write_batch(esp_tls_t *tls,const uint8_t *batch,unsigned count){
 size_t completed;
 if(tls_write_count(tls,batch,count*20,&completed))return true;
 // Fully written 20-byte frames already belong to the receiver. A partial
 // frame is unusable at EOF, so its entire UART payload is discarded once.
 for(unsigned i=completed/20;i<count;i++){
  bridge_packet_t packet;memcpy(packet.bytes,batch+20*i,20);
  wifi_diag_drop(WD_DROP_TLS,&packet);
  uart_bridge_drop_packet(&packet);
 }
 return false;
}
static void discard_network_queue(QueueHandle_t queue){
 bridge_packet_t packet;
 while(xQueueReceive(queue,&packet,0)==pdTRUE){wifi_diag_drop(WD_DROP_LAN_RESET,&packet);uart_bridge_drop_packet(&packet);}
}
static bool data_socket_options(int fd){
#ifdef CAN_WIFI_TCP_NODELAY
 int nodelay=CAN_WIFI_TCP_NODELAY;
#else
 int nodelay=1;
#endif
 if(setsockopt(fd,IPPROTO_TCP,TCP_NODELAY,&nodelay,sizeof(nodelay))){
  wifi_diag_set(WD_SOCKET_LAST_ERRNO,errno,WD_NOW_SEQUENCE);
  wifi_diag_set(WD_TCP_CLOSE_REASON,WD_CLOSE_OPTIONS,WD_NOW_SEQUENCE);return false;
 }
 wifi_diag_set(WD_TCP_NODELAY,(uint32_t)nodelay,WD_NOW_SEQUENCE);return true;
}
static bool tls_diagnostic_reply(esp_tls_t *tls,uint32_t token){
 int rssi=INT32_MIN;wifi_phy_mode_t phy;
 if(esp_wifi_sta_get_rssi(&rssi)!=ESP_OK)rssi=INT32_MIN;
 wifi_diag_set(WD_WIFI_RSSI,(uint32_t)rssi,WD_NOW_SEQUENCE);
 wifi_diag_set(WD_WIFI_PHY_MODE,esp_wifi_sta_get_negotiated_phymode(&phy)==ESP_OK?(uint32_t)phy:WD_UNKNOWN,WD_NOW_SEQUENCE);
 wifi_diag_snapshot_t snapshot;wifi_diag_snapshot(&snapshot);
 uint8_t frames[(WD_FIELDS+1)*20];
 size_t n=wifi_diag_frames(&snapshot,token,frames,sizeof(frames));
 return n&&tls_write_all(tls,frames,n);
}
static void serve_client(int fd){
 if(!data_socket_options(fd)){close(fd);return;}
 unsigned epoch=atomic_load(&network_epoch);struct timeval timeout={.tv_sec=4};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
 esp_tls_t *tls=esp_tls_init();if(!tls){close(fd);return;}
 esp_tls_cfg_server_t cfg={.servercert_buf=(uint8_t*)network_cert,.servercert_bytes=strlen(network_cert)+1,.serverkey_buf=(uint8_t*)network_key,.serverkey_bytes=strlen(network_key)+1,.tls_handshake_timeout_ms=4000};
 int handshake=esp_tls_server_session_create(&cfg,fd,tls);
 if(handshake){wifi_diag_add(WD_TLS_HANDSHAKE_FAILURES,1,WD_NOW_SEQUENCE);wifi_diag_set(WD_TLS_LAST_ERROR,(uint32_t)handshake,WD_NOW_SEQUENCE);goto done;}
 uint8_t auth[36];unsigned received=0;uint32_t deadline=millis()+4000;
 while(received<sizeof(auth)){int n=esp_tls_conn_read(tls,auth+received,sizeof(auth)-received);if(n<=0)goto done;received+=n;if((int32_t)(millis()-deadline)>=0)goto done;}
 unsigned mismatch=0;for(unsigned i=0;i<32;i++)mismatch|=auth[4+i]^network_token[i];
 if(memcmp(auth,"CAN1",4)||mismatch||!network_wifi_mode()||epoch!=atomic_load(&network_epoch))goto done;
 if(!tls_write_all(tls,(uint8_t*)"OK01",4))goto done;
 fcntl(fd,F_SETFL,O_NONBLOCK);discard_network_queue(raw_out);discard_network_queue(control_out);ble_bridge_reset_session();atomic_store(&linked,true);
 uint8_t incoming[20];unsigned at=0,close_reason=WD_CLOSE_MODE;uint32_t last_rx=millis();
 while(network_connected()&&epoch==atomic_load(&network_epoch)){
  // Limit each batch so commands and mode changes are serviced under full CAN load.
  uint8_t batch[20*16];unsigned count=0;bridge_packet_t p;
  while(count<16&&(xQueueReceive(control_out,&p,0)==pdTRUE||xQueueReceive(raw_out,&p,0)==pdTRUE)){memcpy(batch+20*count++,p.bytes,20);}
  wifi_diag_queue(WD_LAN_RAW_DEPTH,WD_LAN_RAW_HIGH,uxQueueMessagesWaiting(raw_out));
  wifi_diag_queue(WD_LAN_CONTROL_DEPTH,WD_LAN_CONTROL_HIGH,uxQueueMessagesWaiting(control_out));
  if(count&&!tls_write_batch(tls,batch,count)){close_reason=WD_CLOSE_WRITE;break;}
  int n=esp_tls_conn_read(tls,incoming+at,20-at);
  if(n>0){at+=n;last_rx=millis();if(at==20){at=0;
   if(incoming[0]==1&&incoming[1]==0x7f){if(!tls_write_all(tls,incoming,20)){close_reason=WD_CLOSE_WRITE;break;}continue;}
   if(epoch!=atomic_load(&network_epoch))break;
   int status;
   if(wifi_diag_query_valid(incoming)){
    if(!tls_diagnostic_reply(tls,wire_u32(incoming+16))){close_reason=WD_CLOSE_DIAGNOSTIC;break;}status=0;
   }else status=ble_bridge_command(incoming);
   bridge_packet_t ack={.bytes={1,18,status,0}};memcpy(ack.bytes+4,incoming+16,4);
   if(!tls_write_all(tls,ack.bytes,20)){close_reason=WD_CLOSE_WRITE;break;}
  }}
  else if(n==0){close_reason=WD_CLOSE_EOF;break;}
  else if(n!=ESP_TLS_ERR_SSL_WANT_READ&&n!=ESP_TLS_ERR_SSL_WANT_WRITE){
   wifi_diag_add(WD_TCP_READ_FAILURES,1,WD_NOW_SEQUENCE);wifi_diag_set(WD_TLS_LAST_ERROR,(uint32_t)n,WD_NOW_SEQUENCE);
   wifi_diag_set(WD_SOCKET_LAST_ERRNO,errno,WD_NOW_SEQUENCE);close_reason=WD_CLOSE_READ;break;
  }
  if((uint32_t)(millis()-last_rx)>12000){close_reason=WD_CLOSE_HEARTBEAT;break;}
  vTaskDelay(pdMS_TO_TICKS(2));
 }
 wifi_diag_add(WD_TCP_CLOSES,1,WD_NOW_SEQUENCE);wifi_diag_set(WD_TCP_CLOSE_REASON,close_reason,WD_NOW_SEQUENCE);
 atomic_store(&linked,false);discard_network_queue(raw_out);discard_network_queue(control_out);if(epoch==atomic_load(&network_epoch))ble_bridge_reset_session();
 wifi_diag_queue(WD_LAN_RAW_DEPTH,WD_LAN_RAW_HIGH,uxQueueMessagesWaiting(raw_out));
 wifi_diag_queue(WD_LAN_CONTROL_DEPTH,WD_LAN_CONTROL_HIGH,uxQueueMessagesWaiting(control_out));
 done:esp_tls_server_session_delete(tls);close(fd);
}
static void server_task(void *arg){
 // Certificate generation needs more stack than app_main and must not block CAN/BLE startup.
 if(!network_identity_init()){set_status(4,9);vTaskDelete(NULL);return;}
 atomic_store(&identity_ready,true);
 int tcp=bound_socket(SOCK_STREAM),udp=bound_socket(SOCK_DGRAM);if(tcp<0||udp<0){if(tcp>=0)close(tcp);if(udp>=0)close(udp);set_status(4,9);vTaskDelete(NULL);return;}listen(tcp,1);
 for(;;){discovery(udp);int fd=accept(tcp,NULL,NULL);if(fd>=0){if(network_wifi_mode()&&atomic_load(&online)&&atomic_load(&identity_ready))serve_client(fd);else close(fd);}vTaskDelay(pdMS_TO_TICKS(20));}
}
static void manager_task(void *arg){
 bool credentials_ok=network_credentials_valid(&saved);uint32_t retry=0,keep_until=0,dual_retry=0;request_t r;unsigned dual_role=0;
 for(;;){
  unsigned wanted=atomic_load(&transport_mode);
  if(dual_role&&dual_role!=wanted){dual_link_stop();esp_wifi_stop();atomic_store(&radio_on,false);esp_wifi_set_ps(WIFI_PS_MIN_MODEM);dual_role=0;}
  if(wanted==4){
   if(atomic_load(&radio_on)){esp_wifi_disconnect();esp_wifi_stop();atomic_store(&radio_on,false);}
   atomic_store(&online,false);atomic_store(&station_ip,0);xQueueReset(requests);
   xSemaphoreTake(lock,portMAX_DELAY);busy=false;network_assembly_reset(&assembly);xSemaphoreGive(lock);
   vTaskDelay(pdMS_TO_TICKS(50));continue;
  }
  if(wanted>=DUAL_A&&wanted<=DUAL_B){
   if(dual_role!=wanted&&(!dual_retry||(int32_t)(millis()-dual_retry)>=0)){
    esp_wifi_disconnect();if(atomic_load(&radio_on))esp_wifi_stop();
    atomic_store(&online,false);atomic_store(&station_ip,0);atomic_store(&radio_on,false);
    xQueueReset(requests);xSemaphoreTake(lock,portMAX_DELAY);busy=false;network_assembly_reset(&assembly);xSemaphoreGive(lock);
    esp_err_t e=esp_wifi_start();
    if(e==ESP_OK){atomic_store(&radio_on,true);e=esp_wifi_set_channel(DUAL_CHANNEL,WIFI_SECOND_CHAN_NONE);}
    if(e==ESP_OK)e=esp_wifi_set_ps(WIFI_PS_NONE);
    if(e!=ESP_OK||!dual_link_start(wanted)){set_status(4,9);dual_retry=millis()+1000;}
    else{set_status(0,0);dual_role=wanted;dual_retry=0;}
   }
   vTaskDelay(pdMS_TO_TICKS(100));continue;
  }
  if(xQueueReceive(requests,&r,pdMS_TO_TICKS(100))==pdTRUE){
   if(network_dual_mode()||network_usb_mode()){dual_zero(&r,sizeof(r));continue;}
   if(r.op!=NET_SCAN){atomic_fetch_add(&network_epoch,1);atomic_store(&linked,false);ble_bridge_reset_session();}
   if(r.op==NET_SCAN){
    esp_err_t err=start_station_radio();
    wifi_scan_config_t config={.show_hidden=false,.scan_type=WIFI_SCAN_TYPE_ACTIVE,.scan_time.active={.min=80,.max=180}};
    if(err==ESP_OK)err=esp_wifi_scan_start(&config,true);
    uint16_t total=SCAN_MAX;
    xSemaphoreTake(lock,portMAX_DELAY);scan_count=0;if(err==ESP_OK&&esp_wifi_scan_get_ap_records(&total,scan_records)==ESP_OK)scan_count=total;else error_code=6;xSemaphoreGive(lock);
   }else if(r.op==NET_CLEAR){
    if(save_credentials(NULL)==ESP_OK){xSemaphoreTake(lock,portMAX_DELAY);memset(&saved,0,sizeof(saved));xSemaphoreGive(lock);credentials_ok=false;esp_wifi_disconnect();atomic_store(&online,false);atomic_store(&station_ip,0);set_status(0,0);}else set_status(4,5);
   }else if(r.op==NET_APPLY){
    set_status(1,0);bool begun=begin_station(&r.credentials,true);uint32_t until=millis()+20000;
    while(begun&&!network_dual_mode()&&!network_usb_mode()&&!atomic_load(&online)&&(int32_t)(until-millis())>0){if(atomic_load(&disconnect_reason))break;vTaskDelay(pdMS_TO_TICKS(100));}
    bool success=false,save_failed=false;
    if(begun&&!network_dual_mode()&&!network_usb_mode()&&atomic_load(&online)){
     station_api(true,NET_STAGE_SAVE,ESP_OK);esp_err_t err=save_credentials(&r.credentials);
     if(err==ESP_OK){
      // Reuse the settings owner: only network changes; UART/baud remain intact.
      uint8_t p[20]={1,SETTINGS_COMMAND,1,SETTINGS_NETWORK,1};bridge_packet_t reply;
      wire_put_u32(p+16,1);
      if(device_settings_command(p,&reply)||reply.bytes[2]!=1)err=ESP_FAIL;
     }
     station_api(true,NET_STAGE_SAVE,err);
     success=err==ESP_OK;save_failed=!success;
    }
    // Sample before cleanup, then freeze; STATUS/scans never refresh this snapshot.
    capture_radio();
    xSemaphoreTake(lock,portMAX_DELAY);network_diagnostics_finish(&diagnostics);int32_t reason=diagnostics.reason;xSemaphoreGive(lock);
    atomic_store(&apply_pending,false);
    if(success){xSemaphoreTake(lock,portMAX_DELAY);saved=r.credentials;xSemaphoreGive(lock);credentials_ok=true;keep_until=millis()+60000;network_set_mode(1);set_status(2,0);}
    else{set_status(4,save_failed?5:reason==WIFI_REASON_AUTH_FAIL||reason==WIFI_REASON_HANDSHAKE_TIMEOUT?2:reason==WIFI_REASON_NO_AP_FOUND?3:4);esp_wifi_disconnect();if(atomic_load(&radio_on))esp_wifi_stop();atomic_store(&radio_on,false);atomic_store(&online,false);atomic_store(&station_ip,0);retry=millis()+10000;ble_bridge_wifi_resume();}
   }
   memset(&r,0,sizeof(r));xSemaphoreTake(lock,portMAX_DELAY);busy=false;xSemaphoreGive(lock);
  }
  if(network_wifi_mode()&&credentials_ok){
   if(!atomic_load(&online)&&(int32_t)(millis()-retry)>=0){begin_station(&saved,false);retry=millis()+10000;set_status(1,0);}
   if(atomic_load(&online))set_status(2,0);
  }else if(atomic_load(&radio_on)){
   // Leave radio up briefly after provisioning so the App can read the DHCP address.
   xSemaphoreTake(lock,portMAX_DELAY);bool keep=state==2&&(int32_t)(keep_until-millis())>0;xSemaphoreGive(lock);
   if(!keep){esp_wifi_stop();atomic_store(&radio_on,false);atomic_store(&online,false);atomic_store(&station_ip,0);if(state==2)set_status(0,0);}
  }
 }
}
void network_init(void){
 wifi_diag_boot(esp_random()); // Public boot correlation ID, not an authentication secret.
 lock=xSemaphoreCreateMutex();requests=xQueueCreate(2,sizeof(request_t));raw_out=xQueueCreate(128,sizeof(bridge_packet_t));control_out=xQueueCreate(32,sizeof(bridge_packet_t));
 if(!lock||!requests||!raw_out||!control_out)return;
 uint8_t mac[6];esp_read_mac(mac,ESP_MAC_WIFI_STA);hex_bytes(device_id,mac,6);
 nvs_handle_t h;if(nvs_open("can_network",NVS_READONLY,&h)==ESP_OK){size_t size=sizeof(saved);if(nvs_get_blob(h,"wifi",&saved,&size)!=ESP_OK||size!=sizeof(saved)||!network_credentials_valid(&saved))memset(&saved,0,sizeof(saved));nvs_close(h);}
 if(esp_netif_init()!=ESP_OK)return;
 esp_err_t err=esp_event_loop_create_default();if(err!=ESP_OK&&err!=ESP_ERR_INVALID_STATE)return;
 if(!esp_netif_create_default_wifi_sta())return;
 wifi_init_config_t cfg=WIFI_INIT_CONFIG_DEFAULT();if(esp_wifi_init(&cfg)!=ESP_OK)return;
 esp_wifi_set_storage(WIFI_STORAGE_RAM);esp_wifi_set_mode(WIFI_MODE_STA);
 if(!dual_link_init())set_status(4,9);
 esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_START,wifi_event,NULL);esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_CONNECTED,wifi_event,NULL);
 esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,wifi_event,NULL);esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL);
 if(xTaskCreate(manager_task,"wifi_config",6144,NULL,2,NULL)!=pdPASS||xTaskCreate(server_task,"wifi_tls",12288,NULL,3,NULL)!=pdPASS){set_status(4,9);return;}atomic_store(&started,true);
}
