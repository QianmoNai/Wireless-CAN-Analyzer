#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "nvs.h"
#include "../firmware/main/name_store.h"
static char disk[40], staged[40];
static int writes, commits, fail_open, fail_set, fail_commit;
esp_err_t nvs_open(const char*n,int mode,nvs_handle_t*h){assert(!strcmp(n,"can_scope"));*h=1;strcpy(staged,disk);return fail_open?ESP_FAIL:ESP_OK;}
esp_err_t nvs_get_str(nvs_handle_t h,const char*k,char*out,size_t*len){assert(!strcmp(k,"ble_name"));if(!disk[0]||strlen(disk)+1>*len)return ESP_FAIL;*len=strlen(disk)+1;strcpy(out,disk);return ESP_OK;}
esp_err_t nvs_set_str(nvs_handle_t h,const char*k,const char*s){writes++;if(fail_set)return ESP_FAIL;strcpy(staged,s);return ESP_OK;}
esp_err_t nvs_commit(nvs_handle_t h){commits++;if(fail_commit)return ESP_FAIL;strcpy(disk,staged);return ESP_OK;}
void nvs_close(nvs_handle_t h){staged[0]=0;}
int main(void){
 char loaded[17];bridge_name_load(loaded);assert(!strcmp(loaded,"ESP32-CAN"));
 assert(bridge_name_save("底盘-CAN")==ESP_OK);bridge_name_load(loaded);assert(!strcmp(loaded,"底盘-CAN"));assert(commits==1);
 assert(bridge_name_save("底盘-CAN")==ESP_OK);assert(commits==1&&writes==1);
 fail_set=1;assert(bridge_name_save("other")==ESP_FAIL);fail_set=0;bridge_name_load(loaded);assert(!strcmp(loaded,"底盘-CAN"));
 fail_commit=1;assert(bridge_name_save("other")==ESP_FAIL);fail_commit=0;bridge_name_load(loaded);assert(!strcmp(loaded,"底盘-CAN"));
 fail_open=1;assert(bridge_name_save("other")==ESP_FAIL);fail_open=0;
 assert(bridge_name_save("")==ESP_ERR_INVALID_ARG);assert(bridge_name_save("   ")==ESP_ERR_INVALID_ARG);
 assert(bridge_name_save("12345678901234567")==ESP_ERR_INVALID_ARG);assert(bridge_name_save("1234567890123456")==ESP_OK);
 assert(!bridge_name_valid((uint8_t*)"x\n",2));assert(!bridge_name_valid((uint8_t*)"\xc0\x80",2));assert(!bridge_name_valid((uint8_t*)"\xed\xa0\x80",3));assert(!bridge_name_valid((uint8_t*)"\xf4\x90\x80\x80",4));assert(!bridge_name_valid((uint8_t*)"\xe4\xb8",2));
 assert(bridge_name_valid((uint8_t*)"\xf0\x9f\x94\xa7",4));strcpy(disk,"\xed\xa0\x80");bridge_name_load(loaded);assert(!strcmp(loaded,"ESP32-CAN"));
 puts("PASS: UTF-8 validation, default/load, persistent name round trip, no-op saves, NVS open/set/commit failures, corrupt stored name fallback (mock NVS).");
}
