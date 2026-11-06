#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "dual_protocol.h"

/* Only an explicit, pure arbitration loss proves that this attempt did not
 * deliver the frame. Never retry mixed, unknown or communication failures. */
enum { DUAL_CAN_RETRY_ARB=1, DUAL_CAN_RETRY_ATTEMPTS=3 };
typedef struct {
    uint32_t created, generation;
    unsigned attempts;
    bool waiting;
} dual_can_retry_t;

static inline void dual_can_retry_begin(dual_can_retry_t *s,
                                        uint32_t created,uint32_t generation) {
    *s=(dual_can_retry_t){.created=created,.generation=generation,.attempts=1};
}
static inline bool dual_can_retry_live(const dual_can_retry_t *s,uint32_t now,
                                       bool connected,uint32_t generation) {
    return connected && generation==s->generation &&
           (uint32_t)(now-s->created)<DUAL_FRAME_TTL_MS;
}
static inline bool dual_can_retry_result(dual_can_retry_t *s,bool success,
                                         uint8_t errors,uint32_t now,
                                         bool connected,uint32_t generation) {
    s->waiting=!success && errors==DUAL_CAN_RETRY_ARB &&
               s->attempts<DUAL_CAN_RETRY_ATTEMPTS &&
               dual_can_retry_live(s,now,connected,generation);
    return s->waiting;
}
static inline bool dual_can_retry_take(dual_can_retry_t *s,uint32_t now,
                                       bool connected,uint32_t generation) {
    if(!s->waiting)return false;
    s->waiting=false;
    if(!dual_can_retry_live(s,now,connected,generation))return false;
    ++s->attempts;
    return true;
}
