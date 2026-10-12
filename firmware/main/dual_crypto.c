#include "dual_crypto.h"
#include "psa/crypto.h"
void dual_zero(void *p, size_t n) {
  volatile uint8_t *v = p;
  while (n--)
    *v++ = 0;
}
bool dual_equal(const void *a, const void *b, size_t n) {
  const uint8_t *x = a, *y = b;
  unsigned d = 0;
  while (n--)
    d |= *x++ ^ *y++;
  return d == 0;
}
bool dual_random(void *out, size_t n) {
  return psa_crypto_init() == PSA_SUCCESS &&
         psa_generate_random(out, n) == PSA_SUCCESS;
}
bool dual_hash(const void *p, size_t n, uint8_t out[32]) {
  size_t got = 0;
  return psa_hash_compute(PSA_ALG_SHA_256, p, n, out, 32, &got) ==
             PSA_SUCCESS &&
         got == 32;
}
bool dual_mac(const uint8_t key[32], const void *p, size_t n, uint8_t out[32]) {
  psa_key_attributes_t a = PSA_KEY_ATTRIBUTES_INIT;
  psa_key_id_t id = 0;
  size_t got = 0;
  psa_set_key_type(&a, PSA_KEY_TYPE_HMAC);
  psa_set_key_bits(&a, 256);
  psa_set_key_usage_flags(&a, PSA_KEY_USAGE_SIGN_MESSAGE);
  psa_set_key_algorithm(&a, PSA_ALG_HMAC(PSA_ALG_SHA_256));
  bool ok = psa_import_key(&a, key, 32, &id) == PSA_SUCCESS &&
            psa_mac_compute(id, PSA_ALG_HMAC(PSA_ALG_SHA_256), p, n, out, 32,
                            &got) == PSA_SUCCESS &&
            got == 32;
  if (id)
    psa_destroy_key(id);
  psa_reset_key_attributes(&a);
  return ok;
}
bool dual_key_create(dual_private_key_t *key, uint8_t pub[65]) {
  psa_key_attributes_t a = PSA_KEY_ATTRIBUTES_INIT;
  psa_key_id_t id = 0;
  size_t got = 0;
  *key = 0;
  if (psa_crypto_init() != PSA_SUCCESS)
    return false;
  psa_set_key_type(&a, PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_SECP_R1));
  psa_set_key_bits(&a, 256);
  psa_set_key_usage_flags(&a, PSA_KEY_USAGE_DERIVE);
  psa_set_key_algorithm(&a, PSA_ALG_ECDH);
  bool ok = psa_generate_key(&a, &id) == PSA_SUCCESS &&
            psa_export_public_key(id, pub, 65, &got) == PSA_SUCCESS &&
            got == 65;
  if (ok)
    *key = id;
  else if (id)
    psa_destroy_key(id);
  psa_reset_key_attributes(&a);
  return ok;
}
void dual_key_free(dual_private_key_t *key) {
  if (*key)
    psa_destroy_key((psa_key_id_t)*key);
  *key = 0;
}
bool dual_agree(dual_private_key_t key, const uint8_t pub[65],
                uint8_t secret[32]) {
  size_t got = 0;
  return psa_raw_key_agreement(PSA_ALG_ECDH, (psa_key_id_t)key, pub, 65, secret,
                               32, &got) == PSA_SUCCESS &&
         got == 32;
}
static bool aead(bool encrypt, const uint8_t key[32], const uint8_t nonce[13],
                 const void *aad, size_t aad_n, const void *in, size_t n,
                 uint8_t *out) {
  psa_key_attributes_t a = PSA_KEY_ATTRIBUTES_INIT;
  psa_key_id_t id = 0;
  size_t got = 0;
  if (!encrypt && n < 16)
    return false;
  psa_set_key_type(&a, PSA_KEY_TYPE_AES);
  psa_set_key_bits(&a, 128);
  psa_set_key_usage_flags(&a, encrypt ? PSA_KEY_USAGE_ENCRYPT
                                      : PSA_KEY_USAGE_DECRYPT);
  psa_set_key_algorithm(&a, PSA_ALG_CCM);
  bool ok = psa_import_key(&a, key, 16, &id) == PSA_SUCCESS;
  if (ok)
    ok = (encrypt ? psa_aead_encrypt(id, PSA_ALG_CCM, nonce, 13, aad, aad_n, in,
                                     n, out, n + 16, &got)
                  : psa_aead_decrypt(id, PSA_ALG_CCM, nonce, 13, aad, aad_n, in,
                                     n, out, n - 16, &got)) == PSA_SUCCESS &&
         got == (encrypt ? n + 16 : n - 16);
  if (id)
    psa_destroy_key(id);
  psa_reset_key_attributes(&a);
  return ok;
}
bool dual_seal(const uint8_t k[32], const uint8_t n[13], const void *a,
               size_t an, const void *p, size_t pn, uint8_t *c) {
  return aead(true, k, n, a, an, p, pn, c);
}
bool dual_open(const uint8_t k[32], const uint8_t n[13], const void *a,
               size_t an, const void *c, size_t cn, uint8_t *p) {
  return aead(false, k, n, a, an, c, cn, p);
}
