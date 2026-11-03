#pragma once
#include <stdint.h>
#include <stdatomic.h>

// Test-only UUID c0de0005. Little-endian uint32 words; no v1 event changes.
// Counters are lifetime, wrapping, independently sampled (not a transaction).
enum { BD_VERSION, BD_WORDS, BD_UPTIME_MS, BD_GENERATION, BD_MTU,
    BD_INTERVAL, BD_LATENCY, BD_TIMEOUT, BD_PARAM_CHANGES, BD_PARAM_STATUS,
    BD_DISCONNECTS, BD_DISCONNECT_REASON, BD_NOTIFY_EVENTS, BD_NOTIFY_ERRORS,
    BD_NOTIFY_RC, BD_POOL_MIN, BD_HEAP_FREE, BD_HEAP_MIN, BD_TYPE17_BYPASS,
    BD_CONNECTS, BD_PHY_TX, BD_PHY_RX, BD_PARAM_REQUESTS, BD_PARAM_REQUEST_RC,
    BD_POOL1_COUNT, BD_POOL2_COUNT, BD_RESOURCE_WAITS, BD_LAST_ERROR_RC,
    BD_RC_ENOMEM, BD_RC_ENOTCONN, BD_RC_EAGAIN, BD_RC_OTHER, BD_HEADER_WORDS };
enum { BD_ENQUEUED, BD_FULL_PACKETS, BD_FULL_BYTES, BD_HIGH_WATER,
    BD_MAX_AGE_MS, BD_ALLOC_FAIL, BD_RESERVE_REJECT, BD_SUBMIT_ERROR,
    BD_SUBMITTED, BD_DROP_ALLOC_BYTES, BD_DROP_RESERVE_BYTES,
    BD_DROP_SUBMIT_BYTES, BD_RESET_BYTES, BD_OTHER_BYTES,
    BD_FAILED_PACKETS, BD_DEPTH, BD_CLASS_WORDS };
enum { BD_RAW, BD_CONTROL, BD_ACK, BD_CLASSES };
#define BD_TOTAL_WORDS (BD_HEADER_WORDS + BD_CLASSES * BD_CLASS_WORDS)
_Static_assert(BD_TOTAL_WORDS * 4 <= 512, "bounded ATT diagnostic value");
#if CONFIG_BLE_TEST_DIAGNOSTICS
static atomic_uint bd[BD_TOTAL_WORDS];
static inline unsigned bd_index(unsigned cls,unsigned field){return BD_HEADER_WORDS+cls*BD_CLASS_WORDS+field;}
static inline void bd_add(unsigned word,unsigned n){atomic_fetch_add(&bd[word],n);}
static inline void bd_set(unsigned word,unsigned n){atomic_store(&bd[word],n);}
static inline void bd_max(unsigned word,unsigned n){unsigned old=atomic_load(&bd[word]);while(old<n&&!atomic_compare_exchange_weak(&bd[word],&old,n)){} }
static inline void bd_min(unsigned word,unsigned n){unsigned old=atomic_load(&bd[word]);while(old>n&&!atomic_compare_exchange_weak(&bd[word],&old,n)){} }
#else
#define bd_add(word,n) ((void)0)
#define bd_set(word,n) ((void)0)
#define bd_max(word,n) ((void)0)
#define bd_min(word,n) ((void)0)
#define bd_index(cls,field) (0u)
#endif
