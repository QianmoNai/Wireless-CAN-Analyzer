idf_component_register(SRCS "wifi_txtest.c" INCLUDE_DIRS "."
    REQUIRES esp_wifi esp_netif esp_event esp_timer esp_system)
set(WIFI_TXTEST_POWER_QDBM "84" CACHE STRING "Requested test TX cap in quarter-dBm (8..84)")
if(NOT WIFI_TXTEST_POWER_QDBM MATCHES "^[0-9]+$" OR
   WIFI_TXTEST_POWER_QDBM LESS 8 OR WIFI_TXTEST_POWER_QDBM GREATER 84)
    message(FATAL_ERROR "WIFI_TXTEST_POWER_QDBM must be an integer in 8..84")
endif()
target_compile_definitions(${COMPONENT_LIB} PRIVATE WIFI_TXTEST_POWER_QDBM=${WIFI_TXTEST_POWER_QDBM})
