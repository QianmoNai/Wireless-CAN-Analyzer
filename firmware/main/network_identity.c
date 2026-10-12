#include "network_identity.h"
#include "nvs.h"
#include "esp_random.h"
#include "mbedtls/pk.h"
#include "mbedtls/x509_crt.h"
#include "psa/crypto.h"
#include <string.h>
char network_cert[1200],network_key[400],network_fingerprint[65];
uint8_t network_token[32];
bool network_identity_init(void){
 nvs_handle_t nvs;if(nvs_open("can_network",NVS_READWRITE,&nvs)!=ESP_OK)return false;
 size_t cert_len=sizeof(network_cert),key_len=sizeof(network_key),token_len=sizeof(network_token);
 bool loaded=nvs_get_str(nvs,"cert",network_cert,&cert_len)==ESP_OK&&nvs_get_str(nvs,"key",network_key,&key_len)==ESP_OK&&nvs_get_blob(nvs,"token",network_token,&token_len)==ESP_OK&&token_len==32;
 bool ok=false;mbedtls_pk_context key;mbedtls_pk_init(&key);mbedtls_x509write_cert crt;mbedtls_x509write_crt_init(&crt);mbedtls_x509_crt parsed;mbedtls_x509_crt_init(&parsed);
 psa_key_id_t id=0;psa_key_attributes_t attr=PSA_KEY_ATTRIBUTES_INIT;
 if(psa_crypto_init()!=PSA_SUCCESS)goto done;
 if(!loaded){
  psa_set_key_type(&attr,PSA_KEY_TYPE_ECC_KEY_PAIR(PSA_ECC_FAMILY_SECP_R1));psa_set_key_bits(&attr,256);
  psa_set_key_usage_flags(&attr,PSA_KEY_USAGE_EXPORT|PSA_KEY_USAGE_SIGN_HASH);psa_set_key_algorithm(&attr,PSA_ALG_ECDSA(PSA_ALG_SHA_256));
  if(psa_generate_key(&attr,&id)!=PSA_SUCCESS||mbedtls_pk_wrap_psa(&key,id))goto done;
  uint8_t serial[16];esp_fill_random(serial,sizeof(serial));serial[0]&=0x7f;serial[0]|=1;
  mbedtls_x509write_crt_set_version(&crt,MBEDTLS_X509_CRT_VERSION_3);mbedtls_x509write_crt_set_md_alg(&crt,MBEDTLS_MD_SHA256);
  mbedtls_x509write_crt_set_subject_key(&crt,&key);mbedtls_x509write_crt_set_issuer_key(&crt,&key);
  if(mbedtls_x509write_crt_set_serial_raw(&crt,serial,sizeof(serial))||mbedtls_x509write_crt_set_subject_name(&crt,"CN=CAN Scope")||mbedtls_x509write_crt_set_issuer_name(&crt,"CN=CAN Scope")||mbedtls_x509write_crt_set_validity(&crt,"20200101000000","20991231235959")||mbedtls_x509write_crt_set_basic_constraints(&crt,0,-1)||mbedtls_x509write_crt_set_key_usage(&crt,MBEDTLS_X509_KU_DIGITAL_SIGNATURE)||mbedtls_x509write_crt_pem(&crt,(unsigned char*)network_cert,sizeof(network_cert))||mbedtls_pk_write_key_pem(&key,(unsigned char*)network_key,sizeof(network_key)))goto done;
  esp_fill_random(network_token,sizeof(network_token));
  if(nvs_set_str(nvs,"cert",network_cert)!=ESP_OK||nvs_set_str(nvs,"key",network_key)!=ESP_OK||nvs_set_blob(nvs,"token",network_token,32)!=ESP_OK||nvs_commit(nvs)!=ESP_OK)goto done;
 }
 if(mbedtls_x509_crt_parse(&parsed,(unsigned char*)network_cert,strlen(network_cert)+1))goto done;
 uint8_t digest[32];size_t length=0;
 if(psa_hash_compute(PSA_ALG_SHA_256,parsed.raw.p,parsed.raw.len,digest,sizeof(digest),&length)!=PSA_SUCCESS||length!=32)goto done;
 static const char hex[]="0123456789abcdef";for(unsigned i=0;i<32;i++){network_fingerprint[2*i]=hex[digest[i]>>4];network_fingerprint[2*i+1]=hex[digest[i]&15];}network_fingerprint[64]=0;ok=true;
 done:mbedtls_x509_crt_free(&parsed);mbedtls_x509write_crt_free(&crt);mbedtls_pk_free(&key);if(id)psa_destroy_key(id);psa_reset_key_attributes(&attr);nvs_close(nvs);return ok;
}
