#include "dual_session.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static unsigned mac_calls;
static bool fail_kdf;
bool real_dual_mac(const uint8_t key[32], const void *p, size_t n, uint8_t out[32]);
bool dual_mac(const uint8_t key[32], const void *p, size_t n, uint8_t out[32]) {
  ++mac_calls;
  if (n == 48 && fail_kdf) { memset(out, 0x5a, 32); return false; }
  return real_dual_mac(key,p,n,out);
}
static void connect(dual_session_t *a, dual_session_t *b) {
  uint8_t p[250];
  for(unsigned i=0;i<10;i++) {
    size_t n=dual_session_hello(a,p); assert(n==60); dual_session_receive_hello(b,p,n,i);
    n=dual_session_hello(b,p); assert(n==60); dual_session_receive_hello(a,p,n,i);
  }
  assert(a->online && b->online && a->traffic_ready && b->traffic_ready);
}
static void empty_key(const dual_session_t *s) {
  assert(!s->traffic_ready && !s->online);
  for(unsigned i=0;i<32;i++) assert(s->traffic_key[i]==0);
}
int main(void) {
  dual_session_t a={0},b={0}; uint8_t master[32],context[48],key[32];
  for(unsigned i=0;i<32;i++) master[i]=(uint8_t)i;
  assert(dual_session_start(&a,DUAL_A,master)); assert(dual_session_start(&b,DUAL_B,master));
  fail_kdf=true;
  uint8_t p[250],v[190],reference[250],iv[13]={0};
  size_t n=dual_session_hello(&a,p); assert(dual_session_receive_hello(&b,p,n,1));
  n=dual_session_hello(&b,p); assert(!dual_session_receive_hello(&a,p,n,2)); empty_key(&a); empty_key(&b);
  fail_kdf=false; connect(&a,&b);
  memcpy(context,"CAN-SESSION-v1",15);context[15]=0;memcpy(context+16,a.local,16);memcpy(context+32,b.local,16);
  assert(real_dual_mac(master,context,48,key));assert(!memcmp(key,a.traffic_key,32));assert(!memcmp(key,b.traffic_key,32));
  unsigned calls=mac_calls;
  unsigned types[]={DUAL_DATA,DUAL_ACK,DUAL_UART};
  for(unsigned role=0;role<2;role++)for(unsigned type=0;type<3;type++)for(unsigned len=0;len<=190;len++) {
    dual_session_t *sender=role?&b:&a,*receiver=role?&a:&b;
    for(unsigned i=0;i<len;i++)v[i]=(uint8_t)(i+len);
    uint32_t sequence=1000+len;size_t got=0;uint32_t seq=0;uint8_t plain[190];
    n=dual_session_encode(sender,types[type],sequence,v,len,p); assert(n==60+len);
    size_t expected=dual_header(reference,types[type],sender->role,36+len+16);assert(expected==n);
    memcpy(reference+8,sender->local,16);memcpy(reference+24,sender->remote,16);dual_put32(reference+40,sequence);
    memset(iv,0,13);iv[0]=sender->role;iv[1]=types[type];dual_put32(iv+9,sequence);
    assert(dual_seal(key,iv,reference,44,v,len,reference+44));assert(!memcmp(reference,p,n));
    assert(dual_session_decode(receiver,p,n,&seq,plain,&got));assert(seq==sequence&&got==len&&!memcmp(plain,v,len));
    p[n-1]^=1;assert(!dual_session_decode(receiver,p,n,&seq,plain,&got));
  }
  assert(mac_calls==calls); // No HMAC/KDF work on any data/ACK hot path.
  uint8_t old[32];memcpy(old,a.traffic_key,32);
  assert(dual_session_start(&a,DUAL_A,a.key));empty_key(&a);assert(!memcmp(a.key,master,32));
  connect(&a,&b);assert(memcmp(old,a.traffic_key,32));
  uint32_t seq;size_t len;assert(!dual_session_decode(&b,reference,n,&seq,v,&len));
  assert(dual_session_start(&b,DUAL_B,b.key));empty_key(&b);connect(&a,&b);
  puts("cache: 1146 exact legacy wire vectors; no hot-path HMAC; KDF failure zeroization; alias-safe reset; both reboots and stale packet rejection PASS");
}
