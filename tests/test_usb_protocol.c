#include <assert.h>
#include <stdio.h>
#include "usb_protocol.h"
int main(void){
    assert(usb_crc((const uint8_t*)"123456789",9)==0xcbf43926);
    uint8_t payload[20]={1,0x10,0,8},frame[32],out[20];uint32_t session=0;
    for(unsigned i=4;i<20;i++)payload[i]=(uint8_t)i;
    usb_encode(frame,0x12345678,payload);
    usb_decoder_t decoder={0};unsigned count=0;
    for(unsigned i=0;i<1000;i++)assert(!usb_decode(&decoder,'x',&session,out));
    uint8_t bad[32];memcpy(bad,frame,32);bad[11]^=1;
    for(unsigned i=0;i<32;i++)assert(!usb_decode(&decoder,bad[i],&session,out));
    for(unsigned repeat=0;repeat<10;repeat++)for(unsigned i=0;i<32;i++)
        if(usb_decode(&decoder,frame[i],&session,out)){count++;assert(session==0x12345678&&!memcmp(out,payload,20));}
    assert(count==10&&decoder.used==0);
    puts("USB codec: CRC32, corruption recovery, junk and consecutive frames PASS");
}
