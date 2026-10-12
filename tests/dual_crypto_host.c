// Real host cryptography for the production pairing/session state machines.
// The ESP32 backend uses PSA; this independent backend uses OpenSSL 3.
#include "dual_crypto.h"
#include <openssl/core_names.h>
#include <openssl/evp.h>
#include <openssl/params.h>
#include <openssl/rand.h>
#include <string.h>
void dual_zero(void *p, size_t n) { OPENSSL_cleanse(p, n); }
bool dual_equal(const void *a, const void *b, size_t n) {
  return CRYPTO_memcmp(a, b, n) == 0;
}
bool dual_random(void *p, size_t n) { return RAND_bytes(p, n) == 1; }
bool dual_hash(const void *p, size_t n, uint8_t out[32]) {
  size_t got = 0;
  return EVP_Q_digest(NULL, "SHA256", NULL, p, n, out, &got) == 1 && got == 32;
}
bool dual_mac(const uint8_t key[32], const void *p, size_t n, uint8_t out[32]) {
  size_t got = 0;
  return EVP_Q_mac(NULL, "HMAC", NULL, "SHA256", NULL, key, 32, p, n, out, 32,
                   &got) != NULL &&
         got == 32;
}
bool dual_key_create(dual_private_key_t *key, uint8_t pub[65]) {
  EVP_PKEY *p = EVP_PKEY_Q_keygen(NULL, NULL, "EC", "prime256v1");
  size_t n = 0;
  *key = (uintptr_t)p;
  if (!p)
    return false;
  return EVP_PKEY_get_octet_string_param(p, OSSL_PKEY_PARAM_PUB_KEY, pub, 65,
                                         &n) == 1 &&
         n == 65;
}
void dual_key_free(dual_private_key_t *key) {
  EVP_PKEY_free((EVP_PKEY *)*key);
  *key = 0;
}
bool dual_agree(dual_private_key_t key, const uint8_t pub[65],
                uint8_t secret[32]) {
  EVP_PKEY_CTX *import = EVP_PKEY_CTX_new_from_name(NULL, "EC", NULL),
               *ctx = NULL;
  EVP_PKEY *peer = NULL;
  size_t n = 32;
  OSSL_PARAM params[] = {OSSL_PARAM_construct_utf8_string(
                             OSSL_PKEY_PARAM_GROUP_NAME, "prime256v1", 0),
                         OSSL_PARAM_construct_octet_string(
                             OSSL_PKEY_PARAM_PUB_KEY, (void *)pub, 65),
                         OSSL_PARAM_construct_end()};
  bool ok = import && EVP_PKEY_fromdata_init(import) == 1 &&
            EVP_PKEY_fromdata(import, &peer, EVP_PKEY_PUBLIC_KEY, params) == 1;
  if (ok) {
    ctx = EVP_PKEY_CTX_new((EVP_PKEY *)key, NULL);
    ok = ctx && EVP_PKEY_derive_init(ctx) == 1 &&
         EVP_PKEY_derive_set_peer(ctx, peer) == 1 &&
         EVP_PKEY_derive(ctx, secret, &n) == 1 && n == 32;
  }
  EVP_PKEY_CTX_free(ctx);
  EVP_PKEY_CTX_free(import);
  EVP_PKEY_free(peer);
  return ok;
}
bool dual_seal(const uint8_t k[32], const uint8_t iv[13], const void *a,
               size_t an, const void *p, size_t n, uint8_t *out) {
  EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
  int got = 0, last = 0;
  if (!p)
    p = "";
  bool ok = c &&
            EVP_EncryptInit_ex(c, EVP_aes_128_ccm(), NULL, NULL, NULL) == 1 &&
            EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_CCM_SET_IVLEN, 13, NULL) == 1 &&
            EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_CCM_SET_TAG, 16, NULL) == 1 &&
            EVP_EncryptInit_ex(c, NULL, NULL, k, iv) == 1 &&
            EVP_EncryptUpdate(c, NULL, &got, NULL, n) == 1 &&
            EVP_EncryptUpdate(c, NULL, &got, a, an) == 1 &&
            EVP_EncryptUpdate(c, out, &got, p, n) == 1 && got == (int)n &&
            EVP_EncryptFinal_ex(c, out + got, &last) == 1 &&
            EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_CCM_GET_TAG, 16, out + n) == 1;
  EVP_CIPHER_CTX_free(c);
  return ok;
}
bool dual_open(const uint8_t k[32], const uint8_t iv[13], const void *a,
               size_t an, const void *p, size_t n, uint8_t *out) {
  if (n < 16)
    return false;
  EVP_CIPHER_CTX *c = EVP_CIPHER_CTX_new();
  int got = 0;
  bool ok =
      c && EVP_DecryptInit_ex(c, EVP_aes_128_ccm(), NULL, NULL, NULL) == 1 &&
      EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_CCM_SET_IVLEN, 13, NULL) == 1 &&
      EVP_CIPHER_CTX_ctrl(c, EVP_CTRL_CCM_SET_TAG, 16, (uint8_t *)p + n - 16) ==
          1 &&
      EVP_DecryptInit_ex(c, NULL, NULL, k, iv) == 1 &&
      EVP_DecryptUpdate(c, NULL, &got, NULL, n - 16) == 1 &&
      EVP_DecryptUpdate(c, NULL, &got, a, an) == 1 &&
      EVP_DecryptUpdate(c, out, &got, p, n - 16) == 1 && got == (int)n - 16;
  EVP_CIPHER_CTX_free(c);
  return ok;
}
