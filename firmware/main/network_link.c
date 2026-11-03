#include "network_link.h"
#include "usb_link.h"
#include "uart_bridge.h"
#include "dual_link.h"
#include "network_protocol.h"
#include "network_identity.h"
#include "ack_delivery.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_timer.h"
#include "esp_mac.h"
#include "esp_tls.h"
#include "nvs.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "lwip/sockets.h"
#include <stdatomic.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <fcntl.h>
#define NET_PORT 32323
#define SCAN_MAX 12
static atomic_uint transport_mode;
static atomic_bool linked, online, identity_ready, started, radio_on;
static atomic_uint pairing=UINT32_MAX,pairing_until,station_ip,disconnect_reason,network_epoch,disconnect_events;
static SemaphoreHandle_t lock;
static QueueHandle_t requests,raw_out,control_out;
static network_assembly_t assembly;
static network_credentials_t saved;
static wifi_ap_record_t scan_records[SCAN_MAX];
static unsigned scan_count,scan_page;
static bool scan_reply,busy;
static uint8_t state,error_code;
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
 if(atomic_exchange(&transport_mode,mode)!=mode){usb_link_reset();dual_link_request_mode(mode);atomic_fetch_add(&network_epoch,1);ble_bridge_reset_session();}
}
bool network_send(const bridge_packet_t *p){return network_connected()&&xQueueSend(bridge_packet_is_raw(p)?raw_out:control_out,p,0)==pdTRUE;}
static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data){
 if(base==IP_EVENT&&id==IP_EVENT_STA_GOT_IP&&!network_dual_mode()){ip_event_got_ip_t *e=data;atomic_store(&station_ip,e->ip_info.ip.addr);atomic_store(&online,true);}
 if(base==WIFI_EVENT&&id==WIFI_EVENT_STA_DISCONNECTED){wifi_event_sta_disconnected_t *e=data;atomic_fetch_add(&disconnect_events,1);atomic_store(&disconnect_reason,e->reason);atomic_store(&online,false);atomic_store(&station_ip,0);}
}
static bool save_credentials(const network_credentials_t *c){nvs_handle_t h;if(nvs_open("can_network",NVS_READWRITE,&h)!=ESP_OK)return false;esp_err_t e=c?nvs_set_blob(h,"wifi",c,sizeof(*c)):nvs_erase_key(h,"wifi");if(e==ESP_ERR_NVS_NOT_FOUND)e=ESP_OK;if(e==ESP_OK)e=nvs_commit(h);nvs_close(h);return e==ESP_OK;}
static bool begin_station(const network_credentials_t *c){
 unsigned before=atomic_load(&disconnect_events);
 esp_err_t disconnected=esp_wifi_disconnect();
 // Drain the previous association's event before beginning another attempt.
 if(disconnected==ESP_OK){uint32_t until=millis()+1000;while(before==atomic_load(&disconnect_events)&&(int32_t)(until-millis())>0)vTaskDelay(pdMS_TO_TICKS(10));}
 atomic_store(&online,false);atomic_store(&station_ip,0);atomic_store(&disconnect_reason,0);
 wifi_config_t cfg={0};memcpy(cfg.sta.ssid,c->ssid,c->ssid_len);memcpy(cfg.sta.password,c->password,c->password_len);cfg.sta.pmf_cfg.capable=true;
 esp_err_t configured=esp_wifi_set_config(WIFI_IF_STA,&cfg);memset(&cfg,0,sizeof(cfg));if(configured!=ESP_OK)return false;
 if(!atomic_load(&radio_on)){if(esp_wifi_start()!=ESP_OK)return false;atomic_store(&radio_on,true);}
 return esp_wifi_connect()==ESP_OK;
}
int network_config_write(const uint8_t p[20]){
 if(network_dual_mode()||network_usb_mode())return 3;
 if(!atomic_load(&started)||!atomic_load(&identity_ready))return 14;
 request_t r={.op=p[0]};int result=0;
 xSemaphoreTake(lock,portMAX_DELAY);
 if(p[0]==NET_STATUS){scan_reply=false;}
 else if(p[0]==NET_SCAN_PAGE){scan_reply=true;scan_page=p[2]<SCAN_MAX?p[2]:0;}
 else if(busy){result=17;}
 else if(p[0]==NET_BEGIN||p[0]==NET_DATA){if(!network_assembly_packet(&assembly,p,millis(),&r.credentials))result=19;}
 else if(p[0]==NET_APPLY){if(!network_assembly_packet(&assembly,p,millis(),&r.credentials))result=19;else if(xQueueSend(requests,&r,0)!=pdTRUE)result=17;else{busy=true;scan_reply=false;state=1;error_code=0;}}
 else if(p[0]==NET_SCAN||p[0]==NET_CLEAR){if(xQueueSend(requests,&r,0)!=pdTRUE)result=17;else{busy=true;scan_reply=false;error_code=0;}}
 else result=19;
 xSemaphoreGive(lock);memset(&r,0,sizeof(r));return result;
}
void network_config_disconnect(void){if(!lock)return;xSemaphoreTake(lock,portMAX_DELAY);network_assembly_reset(&assembly);scan_reply=false;xSemaphoreGive(lock);network_pairing_end();}
size_t network_config_read(char *out,size_t size){
 if(size<512)return 0;
 if(!lock)return snprintf(out,size,"{\"state\":4,\"error\":9}");
 xSemaphoreTake(lock,portMAX_DELAY);
 int n=0;
 if(scan_reply){
  n=snprintf(out,size,"{\"scan\":[");unsigned end=scan_page+3;if(end>scan_count)end=scan_count;
  for(unsigned i=scan_page;i<end;i++){char ssid[65];hex_bytes(ssid,scan_records[i].ssid,strnlen((char*)scan_records[i].ssid,32));n+=snprintf(out+n,size-n,"%s[\"%s\",%d,%d]",i==scan_page?"":",",ssid,scan_records[i].rssi,scan_records[i].authmode);}
  n+=snprintf(out+n,size-n,"],\"total\":%u,\"offset\":%u}",scan_count,scan_page);
 }else{
  char token[65],ssid[65],ip[16]="";hex_bytes(token,network_token,32);hex_bytes(ssid,saved.ssid,saved.ssid_len);
  struct in_addr a={.s_addr=atomic_load(&station_ip)};if(a.s_addr)inet_ntoa_r(a,ip,sizeof(ip));
  n=snprintf(out,size,"{\"state\":%u,\"error\":%u,\"busy\":%s,\"mode\":%u,\"ip\":\"%s\",\"ssid\":\"%s\",\"id\":\"%s\",\"fp\":\"%s\",\"token\":\"%s\",\"port\":%d}",state,error_code,busy?"true":"false",network_wifi_mode(),ip,ssid,device_id,network_fingerprint,token,NET_PORT);
 }
 xSemaphoreGive(lock);return n>0&&(size_t)n<size?(size_t)n:0;
}
static int bound_socket(int type){int s=socket(AF_INET,type,0);if(s<0)return -1;int on=1;setsockopt(s,SOL_SOCKET,SO_REUSEADDR,&on,sizeof(on));struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(NET_PORT),.sin_addr.s_addr=htonl(INADDR_ANY)};if(bind(s,(struct sockaddr*)&a,sizeof(a))){close(s);return -1;}fcntl(s,F_SETFL,O_NONBLOCK);return s;}
static void discovery(int fd){
 char message[64];struct sockaddr_in peer;socklen_t len=sizeof(peer);int n=recvfrom(fd,message,sizeof(message),0,(struct sockaddr*)&peer,&len);
 // Reply only to a small, versioned discovery request; never expose credentials.
 if(n==19&&!memcmp(message,"CAN_SCOPE_DISCOVER_1",19)){char reply[100];int count=snprintf(reply,sizeof(reply),"CAN_SCOPE_1 %s %d",device_id,NET_PORT);sendto(fd,reply,count,0,(struct sockaddr*)&peer,len);}
}
static bool tls_write_count(esp_tls_t *tls,const uint8_t *p,size_t n,size_t *completed){
 *completed=0;uint32_t deadline=millis()+1500;
 while(n){int r=esp_tls_conn_write(tls,p,n);
  if(r>0){p+=r;n-=r;*completed+=r;}
  else if(r!=ESP_TLS_ERR_SSL_WANT_READ&&r!=ESP_TLS_ERR_SSL_WANT_WRITE)return false;
  if((int32_t)(millis()-deadline)>=0)return false;
  if(r<=0)vTaskDelay(pdMS_TO_TICKS(5));
 }return true;
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
  uart_bridge_drop_packet(&packet);
 }
 return false;
}
static void discard_network_queue(QueueHandle_t queue){
 bridge_packet_t packet;
 while(xQueueReceive(queue,&packet,0)==pdTRUE)uart_bridge_drop_packet(&packet);
}
static void serve_client(int fd){
 unsigned epoch=atomic_load(&network_epoch);struct timeval timeout={.tv_sec=4};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
 esp_tls_t *tls=esp_tls_init();if(!tls){close(fd);return;}
 esp_tls_cfg_server_t cfg={.servercert_buf=(uint8_t*)network_cert,.servercert_bytes=strlen(network_cert)+1,.serverkey_buf=(uint8_t*)network_key,.serverkey_bytes=strlen(network_key)+1,.tls_handshake_timeout_ms=4000};
 if(esp_tls_server_session_create(&cfg,fd,tls))goto done;
 uint8_t auth[36];unsigned received=0;uint32_t deadline=millis()+4000;
 while(received<sizeof(auth)){int n=esp_tls_conn_read(tls,auth+received,sizeof(auth)-received);if(n<=0)goto done;received+=n;if((int32_t)(millis()-deadline)>=0)goto done;}
 unsigned mismatch=0;for(unsigned i=0;i<32;i++)mismatch|=auth[4+i]^network_token[i];
 if(memcmp(auth,"CAN1",4)||mismatch||!network_wifi_mode()||epoch!=atomic_load(&network_epoch))goto done;
 if(!tls_write_all(tls,(uint8_t*)"OK01",4))goto done;
 fcntl(fd,F_SETFL,O_NONBLOCK);discard_network_queue(raw_out);discard_network_queue(control_out);ble_bridge_reset_session();atomic_store(&linked,true);
 uint8_t incoming[20];unsigned at=0;uint32_t last_rx=millis();
 while(network_connected()&&epoch==atomic_load(&network_epoch)){
  // Limit each batch so commands and mode changes are serviced under full CAN load.
  uint8_t batch[20*16];unsigned count=0;bridge_packet_t p;
  while(count<16&&(xQueueReceive(control_out,&p,0)==pdTRUE||xQueueReceive(raw_out,&p,0)==pdTRUE)){memcpy(batch+20*count++,p.bytes,20);}
  if(count&&!tls_write_batch(tls,batch,count))break;
  int n=esp_tls_conn_read(tls,incoming+at,20-at);
  if(n>0){at+=n;last_rx=millis();if(at==20){at=0;if(incoming[0]==1&&incoming[1]==0x7f){if(!tls_write_all(tls,incoming,20))break;continue;}if(epoch!=atomic_load(&network_epoch))break;int status=ble_bridge_command(incoming);bridge_packet_t ack={.bytes={1,18,status,0}};memcpy(ack.bytes+4,incoming+16,4);if(!tls_write_all(tls,ack.bytes,20))break;}}
  else if(n==0||(n!=ESP_TLS_ERR_SSL_WANT_READ&&n!=ESP_TLS_ERR_SSL_WANT_WRITE))break;
  if((uint32_t)(millis()-last_rx)>12000)break;
  vTaskDelay(pdMS_TO_TICKS(2));
 }
 atomic_store(&linked,false);discard_network_queue(raw_out);discard_network_queue(control_out);if(epoch==atomic_load(&network_epoch))ble_bridge_reset_session();
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
    if(!atomic_load(&radio_on)){if(esp_wifi_start()==ESP_OK)atomic_store(&radio_on,true);}
    wifi_scan_config_t config={.show_hidden=false,.scan_type=WIFI_SCAN_TYPE_ACTIVE,.scan_time.active={.min=80,.max=180}};
    esp_err_t err=esp_wifi_scan_start(&config,true);uint16_t total=SCAN_MAX;
    xSemaphoreTake(lock,portMAX_DELAY);scan_count=0;if(err==ESP_OK&&esp_wifi_scan_get_ap_records(&total,scan_records)==ESP_OK)scan_count=total;else error_code=6;xSemaphoreGive(lock);
   }else if(r.op==NET_CLEAR){
    if(save_credentials(NULL)){xSemaphoreTake(lock,portMAX_DELAY);memset(&saved,0,sizeof(saved));xSemaphoreGive(lock);credentials_ok=false;esp_wifi_disconnect();atomic_store(&online,false);atomic_store(&station_ip,0);set_status(0,0);}else set_status(4,5);
   }else if(r.op==NET_APPLY){
    set_status(1,0);bool begun=begin_station(&r.credentials);uint32_t until=millis()+20000;
    while(begun&&!network_dual_mode()&&!network_usb_mode()&&!atomic_load(&online)&&(int32_t)(until-millis())>0){if(atomic_load(&disconnect_reason))break;vTaskDelay(pdMS_TO_TICKS(100));}
    if(begun&&!network_dual_mode()&&!network_usb_mode()&&atomic_load(&online)&&save_credentials(&r.credentials)){xSemaphoreTake(lock,portMAX_DELAY);saved=r.credentials;xSemaphoreGive(lock);credentials_ok=true;keep_until=millis()+60000;set_status(2,0);}
    else{uint32_t reason=atomic_load(&disconnect_reason);set_status(4,atomic_load(&online)?5:reason==WIFI_REASON_AUTH_FAIL||reason==WIFI_REASON_HANDSHAKE_TIMEOUT?2:reason==WIFI_REASON_NO_AP_FOUND?3:4);esp_wifi_disconnect();atomic_store(&online,false);atomic_store(&station_ip,0);retry=millis()+10000;}
   }
   memset(&r,0,sizeof(r));xSemaphoreTake(lock,portMAX_DELAY);busy=false;xSemaphoreGive(lock);
  }
  if(network_wifi_mode()&&credentials_ok){
   if(!atomic_load(&online)&&(int32_t)(millis()-retry)>=0){begin_station(&saved);retry=millis()+10000;set_status(1,0);}
   if(atomic_load(&online))set_status(2,0);
  }else if(atomic_load(&radio_on)){
   // Leave radio up briefly after provisioning so the App can read the DHCP address.
   xSemaphoreTake(lock,portMAX_DELAY);bool keep=state==2&&(int32_t)(keep_until-millis())>0;xSemaphoreGive(lock);
   if(!keep){esp_wifi_stop();atomic_store(&radio_on,false);atomic_store(&online,false);atomic_store(&station_ip,0);if(state==2)set_status(0,0);}
  }
 }
}
void network_init(void){
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
 esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,wifi_event,NULL);esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL);
 if(xTaskCreate(manager_task,"wifi_config",6144,NULL,2,NULL)!=pdPASS||xTaskCreate(server_task,"wifi_tls",12288,NULL,3,NULL)!=pdPASS){set_status(4,9);return;}atomic_store(&started,true);
}
