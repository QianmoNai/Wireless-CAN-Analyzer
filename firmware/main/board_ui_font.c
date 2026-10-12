#include "board_ui_font.h"
#include <stddef.h>

typedef struct { uint32_t codepoint; uint16_t rows[16]; } ui_cn_glyph_t;
#include "board_ui_font_data.h"

uint32_t ui_utf8_next(const char **text)
{
    const unsigned char *p = (const unsigned char *)*text;
    uint32_t value = p[0], minimum;
    unsigned count;
    if (value < 0x80) { if (value) ++*text; return value; }
    if (value >= 0xc2 && value <= 0xdf) { count=2; minimum=0x80; value&=0x1f; }
    else if (value >= 0xe0 && value <= 0xef) { count=3; minimum=0x800; value&=0x0f; }
    else if (value >= 0xf0 && value <= 0xf4) { count=4; minimum=0x10000; value&=7; }
    else { ++*text; return 0xfffd; }
    for (unsigned i=1; i<count; ++i) {
        if ((p[i] & 0xc0) != 0x80) { ++*text; return 0xfffd; }
        value = (value << 6) | (p[i] & 0x3f);
    }
    if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) {
        ++*text; return 0xfffd;
    }
    *text += count;
    return value;
}

const uint16_t *ui_cn_glyph(uint32_t codepoint)
{
    size_t low=0, high=sizeof(ui_cn_glyphs)/sizeof(ui_cn_glyphs[0]);
    while (low < high) {
        size_t mid=low+(high-low)/2;
        if (ui_cn_glyphs[mid].codepoint < codepoint) low=mid+1;
        else if (ui_cn_glyphs[mid].codepoint > codepoint) high=mid;
        else return ui_cn_glyphs[mid].rows;
    }
    return NULL;
}
