#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define BRIDGE_NAME_MAX 16
// Strict UTF-8, no control characters; a name must contain a non-space codepoint.
static inline bool bridge_name_valid(const uint8_t *s, size_t len) {
    if (!len || len > BRIDGE_NAME_MAX) return false;
    bool visible = false;
    for (size_t i = 0; i < len;) {
        uint32_t cp = s[i++], min = 0; unsigned extra = 0;
        if (cp < 0x80) { if (cp < 0x20 || cp == 0x7f) return false; }
        else if (cp >= 0xc2 && cp <= 0xdf) { cp &= 31; extra = 1; min = 0x80; }
        else if (cp >= 0xe0 && cp <= 0xef) { cp &= 15; extra = 2; min = 0x800; }
        else if (cp >= 0xf0 && cp <= 0xf4) { cp &= 7; extra = 3; min = 0x10000; }
        else return false;
        if (i + extra > len) return false;
        for (unsigned j = 0; j < extra; j++) {
            uint8_t c = s[i++]; if ((c & 0xc0) != 0x80) return false;
            cp = (cp << 6) | (c & 63);
        }
        if (cp < min || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff) || (cp >= 0x80 && cp <= 0x9f)) return false;
        if (cp != 0x20 && cp != 0x3000 && cp != 0xa0) visible = true;
    }
    return visible;
}
