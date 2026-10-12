#pragma once
#include <stdint.h>

// Invalid UTF-8 consumes one byte and produces U+FFFD; NUL leaves the cursor at NUL.
uint32_t ui_utf8_next(const char **text);
// Fixed 16x16 monochrome glyph, MSB is the leftmost pixel of each row.
const uint16_t *ui_cn_glyph(uint32_t codepoint);
