#include "network_protocol.h"
#include <string.h>
void network_assembly_reset(network_assembly_t *a){volatile uint8_t *p=(volatile uint8_t*)a;for(size_t i=0;i<sizeof(*a);i++)p[i]=0;}
bool network_credentials_valid(const network_credentials_t *c){
 if(!c->ssid_len||c->ssid_len>32||c->password_len>64||(c->password_len>0&&c->password_len<8))return false;
 for(unsigned i=0;i<c->ssid_len;i++)if(!c->ssid[i])return false;
 for(unsigned i=0;i<c->password_len;i++)if(c->password[i]<32||c->password[i]>126)return false;
 if(c->password_len==64)for(unsigned i=0;i<64;i++)if(!((c->password[i]>='0'&&c->password[i]<='9')||(c->password[i]>='a'&&c->password[i]<='f')||(c->password[i]>='A'&&c->password[i]<='F')))return false;
 return true;
}
bool network_assembly_packet(network_assembly_t *a,const uint8_t p[20],uint32_t now,network_credentials_t *out){
 if(p[0]==NET_BEGIN){network_assembly_reset(a);if(!p[2]||p[2]>32||p[3]>64)return false;a->active=true;a->transaction=p[1];a->started=now;a->credentials.ssid_len=p[2];a->credentials.password_len=p[3];return true;}
 if(!a->active||p[1]!=a->transaction||(uint32_t)(now-a->started)>10000){network_assembly_reset(a);return false;}
 unsigned total=a->credentials.ssid_len+a->credentials.password_len;
 if(p[0]==NET_DATA){
  if(p[2]!=a->received||!p[3]||p[3]>16||a->received+p[3]>total){network_assembly_reset(a);return false;}
  for(unsigned i=0;i<p[3];i++){unsigned at=a->received++;if(at<a->credentials.ssid_len)a->credentials.ssid[at]=p[4+i];else a->credentials.password[at-a->credentials.ssid_len]=p[4+i];}return true;
 }
 if(p[0]!=NET_APPLY||a->received!=total||!network_credentials_valid(&a->credentials)){network_assembly_reset(a);return false;}
 *out=a->credentials;network_assembly_reset(a);return true;
}
