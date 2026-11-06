#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
// Native USB byte stream: CUSB | session LE32 | bridge payload[20] | CRC32 LE32.
enum { USB_FRAME_SIZE=32, USB_HELLO=0x7e, USB_PING=0x7f, USB_BYE=0x7d };
typedef struct { uint8_t bytes[USB_FRAME_SIZE]; unsigned used; } usb_decoder_t;
static inline uint32_t usb_get32(const uint8_t *p) {
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static inline void usb_put32(uint8_t *p,uint32_t v) { for(unsigned i=0;i<4;i++)p[i]=(uint8_t)(v>>(8*i)); }
static inline uint32_t usb_crc(const uint8_t *p,unsigned n) {
    uint32_t crc=UINT32_MAX;
    // IEEE CRC32 in two four-bit steps per byte; the wire format is unchanged.
    static const uint32_t nibble[16]={
        0x00000000u,0x1db71064u,0x3b6e20c8u,0x26d930acu,
        0x76dc4190u,0x6b6b51f4u,0x4db26158u,0x5005713cu,
        0xedb88320u,0xf00f9344u,0xd6d6a3e8u,0xcb61b38cu,
        0x9b64c2b0u,0x86d3d2d4u,0xa00ae278u,0xbdbdf21cu};
    for(unsigned i=0;i<n;i++){
        crc^=p[i];crc=(crc>>4)^nibble[crc&15];crc=(crc>>4)^nibble[crc&15];
    }
    return ~crc;
}
static inline void usb_encode(uint8_t out[32],uint32_t session,const uint8_t payload[20]) {
    memcpy(out,"CUSB",4);usb_put32(out+4,session);memcpy(out+8,payload,20);usb_put32(out+28,usb_crc(out,28));
}
static inline bool usb_decode(usb_decoder_t *d,uint8_t byte,uint32_t *session,uint8_t payload[20]) {
    d->bytes[d->used++]=byte;
    if(d->used<32)return false;
    if(!memcmp(d->bytes,"CUSB",4)&&usb_get32(d->bytes+28)==usb_crc(d->bytes,28)){
        *session=usb_get32(d->bytes+4);memcpy(payload,d->bytes+8,20);d->used=0;return true;
    }
    memmove(d->bytes,d->bytes+1,31);d->used=31;return false;
}
