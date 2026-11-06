#include "dual_can_retry.h"
#include <assert.h>
#include <stdio.h>
int main(void) {
 dual_can_retry_t s;
 for(unsigned errors=0;errors<256;errors++)for(unsigned success=0;success<2;success++) {
  dual_can_retry_begin(&s,1000,9);
  assert(dual_can_retry_result(&s,success,errors,1001,true,9)==(!success&&errors==1));
 }
 dual_can_retry_begin(&s,1000,9);
 for(unsigned attempt=1;attempt<=3;attempt++) {
  bool retry=dual_can_retry_result(&s,false,1,1000+attempt,true,9);
  assert(retry==(attempt<3));assert(dual_can_retry_take(&s,1000+attempt,true,9)==retry);
 }
 for(unsigned scenario=0;scenario<4;scenario++) {
  dual_can_retry_begin(&s,1000,9);assert(dual_can_retry_result(&s,false,1,1001,true,9));
  assert(!dual_can_retry_take(&s,scenario==0?1100:1002,scenario!=1,scenario==2?10:scenario==3?8:9));
  assert(!s.waiting);
 }
 dual_can_retry_begin(&s,0xfffffff0,7);assert(dual_can_retry_result(&s,false,1,0x10,true,7));assert(dual_can_retry_take(&s,0x20,true,7));assert(!dual_can_retry_result(&s,false,1,0x54,true,7));
 dual_can_retry_begin(&s,1000,9);assert(!dual_can_retry_result(&s,false,1,999,true,9));
 puts("retry: all512 result/error cases; three total attempts; original100ms deadline; wrap, disconnect and generation cancellation PASS");
}
