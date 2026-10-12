#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef uintptr_t dual_private_key_t;
void dual_zero(void *data, size_t size);
bool dual_equal(const void *a, const void *b, size_t size);
bool dual_random(void *out, size_t size);
bool dual_hash(const void *data, size_t size, uint8_t out[32]);
bool dual_mac(const uint8_t key[32], const void *data, size_t size,
              uint8_t out[32]);
bool dual_key_create(dual_private_key_t *key, uint8_t pub[65]);
void dual_key_free(dual_private_key_t *key);
bool dual_agree(dual_private_key_t key, const uint8_t pub[65],
                uint8_t secret[32]);
bool dual_seal(const uint8_t key[32], const uint8_t nonce[13], const void *aad,
               size_t aad_size, const void *plain, size_t size,
               uint8_t *cipher);
bool dual_open(const uint8_t key[32], const uint8_t nonce[13], const void *aad,
               size_t aad_size, const void *cipher, size_t size,
               uint8_t *plain);
