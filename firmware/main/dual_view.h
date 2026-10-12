#pragma once
#include <stdbool.h>
#include <stdint.h>
enum {
  DUAL_UI_OFF,
  DUAL_UI_UNBOUND,
  DUAL_UI_OFFLINE,
  DUAL_UI_ONLINE,
  DUAL_UI_SEARCH,
  DUAL_UI_WAIT,
  DUAL_UI_HANDSHAKE,
  DUAL_UI_CHECK,
  DUAL_UI_ERROR,
  DUAL_UI_UNBIND
};
enum {
  DUAL_BUTTON_PREVIOUS = -1,
  DUAL_BUTTON_MIDDLE = 0,
  DUAL_BUTTON_NEXT = 1,
  DUAL_BUTTON_CANCEL = 2
};
typedef struct {
  uint32_t code, sent, received, dropped;
  uint8_t state, count, index, peer[6];
  bool bound, confirmed, clear_selected;
} dual_view_t;
