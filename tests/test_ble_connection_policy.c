#include <assert.h>
#include <stdio.h>
#include "ble_connection_policy.h"
int main(void){
 bridge_ble_policy_t s;bridge_ble_params_t p;
 bridge_ble_policy_start(&s,10,0);
 assert(!bridge_ble_policy_next(&s,10,999,1,true,36,0,42,&p));
 assert(bridge_ble_policy_next(&s,10,1000,1,true,36,0,42,&p));
 assert(p.min_interval==12&&p.max_interval==24&&p.latency==0&&p.timeout==42);
 bridge_ble_policy_result(&s,0);
 assert(!bridge_ble_policy_next(&s,10,10000,1,true,36,0,42,&p));
 bridge_ble_policy_start(&s,11,0);
 assert(bridge_ble_policy_next(&s,11,1000,2,true,36,0,42,&p));
 assert(p.min_interval==6&&p.max_interval==12&&p.timeout==42);
 bridge_ble_policy_result(&s,7);
 assert(!bridge_ble_policy_next(&s,11,3999,2,true,36,0,42,&p));
 assert(bridge_ble_policy_next(&s,11,4000,2,true,36,0,42,&p));
 bridge_ble_policy_result(&s,7);
 assert(!bridge_ble_policy_next(&s,11,20000,2,true,36,0,42,&p));
 for(unsigned t=0;t<3;t++){
  bridge_ble_policy_start(&s,12,0);
  assert(!bridge_ble_policy_next(&s,t==0?11:12,1000,t==2?0:1,t!=1,36,0,42,&p));
 }
 bridge_ble_policy_start(&s,13,0);
 assert(!bridge_ble_policy_next(&s,13,1000,1,true,24,0,42,&p));
 bridge_ble_policy_start(&s,14,0);
 assert(bridge_ble_policy_next(&s,14,1000,1,true,24,1,9,&p));assert(p.timeout==200&&p.latency==0);
 for(unsigned timeout=10;timeout<=3200;timeout++){
  bridge_ble_policy_start(&s,15,0);
  assert(bridge_ble_policy_next(&s,15,1000,1,true,36,0,timeout,&p));assert(p.timeout==timeout);
 }
 puts("PASS: connection profiles, observation delay, finite rejection retry, accepted no-repeat, generation/readiness, timeout legality");
}
