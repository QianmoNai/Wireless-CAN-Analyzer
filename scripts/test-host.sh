#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
test_build_dir="$(mktemp -d)"
trap 'rm -rf -- "$test_build_dir"' EXIT
compile_run() {
    local name="$1"; shift
    cc -std=c11 -Ifirmware/main -Itests/stubs "tests/$name.c" "$@" -o "$test_build_dir/$name"
    "$test_build_dir/$name"
}
compile_run test_board_ui firmware/main/board_ui_model.c firmware/main/board_ui_render.c firmware/main/board_ui_font.c firmware/main/board_ui_data.c
compile_run test_board_ui_animation -Wall -Wextra -Werror firmware/main/board_ui_animation.c firmware/main/board_ui_model.c firmware/main/board_ui_render.c firmware/main/board_ui_font.c
for led_config in "1 1" "1 0" "0 1"; do
    read -r led_enabled led_s3 <<< "$led_config"
    cc -std=c11 -Wall -Wextra -Werror -DCONFIG_CAN_STATUS_LED="$led_enabled" -DCONFIG_IDF_TARGET_ESP32S3="$led_s3" \
        -Itests/status_led_stubs -Ifirmware/main tests/test_board_status_led.c firmware/main/board_status_led.c \
        -o "$test_build_dir/test_board_status_led-$led_enabled-$led_s3"
    "$test_build_dir/test_board_status_led-$led_enabled-$led_s3"
done
cc -std=c11 -Wall -Wextra -Werror -Itests/buzzer_stubs -Ifirmware/main tests/test_board_buzzer.c firmware/main/board_buzzer.c -o "$test_build_dir/test_board_buzzer"
"$test_build_dir/test_board_buzzer"
cc -std=c11 -Wall -Wextra -Werror -Itests/uart_stubs -Itests/dual_stubs -Ifirmware/main tests/test_uart_bridge.c firmware/main/wifi_uart_diag.c -o "$test_build_dir/test_uart_bridge"
cc -std=c11 -Wall -Wextra -Werror -DCONFIG_BLE_TEST_DIAGNOSTICS=1 -Itests/uart_stubs -Itests/dual_stubs -Ifirmware/main tests/test_uart_bridge.c firmware/main/wifi_uart_diag.c -o "$test_build_dir/test_uart_bridge_diag"
"$test_build_dir/test_uart_bridge_diag"
"$test_build_dir/test_uart_bridge"
cc -std=c11 -Wall -Wextra -Werror -Itests/usb_stubs -Ifirmware/main tests/test_usb_link.c -o "$test_build_dir/test_usb_link"
"$test_build_dir/test_usb_link"
compile_run test_usb_protocol -Wall -Wextra -Werror
cc -std=c11 -Wall -Wextra -Werror -Itests/settings_stubs -Itests/usb_stubs -Ifirmware/main tests/test_device_settings.c firmware/main/board_ui_model.c -o "$test_build_dir/test_device_settings"
"$test_build_dir/test_device_settings"
compile_run test_network_protocol firmware/main/network_protocol.c
compile_run test_network_diagnostics -Wall -Wextra -Werror firmware/main/network_diagnostics.c
compile_run test_wifi_uart_diag -Wall -Wextra -Werror firmware/main/wifi_uart_diag.c
python3 tests/test_wifi_can_options.py
python3 tests/test_wifi_can_power.py
python3 tests/test_can_hardware_bench.py
python3 tests/test_linux_tcp_info.py
cc -std=c11 -Wall -Wextra -Werror -Ifirmware/main tests/test_dual_protocol.c tests/dual_crypto_host.c firmware/main/dual_protocol.c firmware/main/dual_session.c -lcrypto -o "$test_build_dir/test_dual_protocol"
"$test_build_dir/test_dual_protocol"
cc -std=c11 -Wall -Wextra -Werror -Itests/dual_stubs -Ifirmware/main tests/test_dual_runtime.c tests/dual_runtime_a.c tests/dual_runtime_b.c tests/dual_crypto_host.c firmware/main/dual_protocol.c firmware/main/dual_session.c -lcrypto -o "$test_build_dir/test_dual_runtime"
"$test_build_dir/test_dual_runtime"
cc -std=c11 -Wall -Wextra -Werror -DCAN_DUAL_UART_LOOPBACK_TEST=1 -Itests/dual_stubs -Ifirmware/main tests/test_dual_runtime.c tests/dual_runtime_a.c tests/dual_runtime_b.c tests/dual_crypto_host.c firmware/main/dual_protocol.c firmware/main/dual_session.c -lcrypto -o "$test_build_dir/test_dual_uart_loopback"
"$test_build_dir/test_dual_uart_loopback"
compile_run test_firmware -DCONFIG_IDF_TARGET_ESP32S3=1
compile_run test_home_status firmware/main/home_status.c
bash fixtures/stm32-h723/tests/run.sh
compile_run test_home_temperatures firmware/main/home_status.c
compile_run test_can_load firmware/main/can_load.c
compile_run test_can_rates firmware/main/can_rates.c
compile_run test_ack_delivery firmware/main/ack_delivery.c
compile_run test_feedback_poll firmware/main/feedback_poll.c firmware/main/ack_delivery.c
compile_run test_notify_resource firmware/main/notify_resource.c firmware/main/ack_delivery.c
compile_run test_notify_schedule -Wall -Wextra -Werror firmware/main/notify_schedule.c
compile_run test_ble_connection_policy -Wall -Wextra -Werror firmware/main/ble_connection_policy.c
cc -std=c11 -Itests/name_stubs -Ifirmware/main tests/test_name_store.c firmware/main/name_store.c -o "$test_build_dir/test_name_store"
"$test_build_dir/test_name_store"
python3 tests/test_query_tx_filter.py
python3 tests/test_uart_downstream.py
python3 scripts/dual-uart-measure.py --self-test
python3 tests/test_build_scripts.py
python3 tests/test_asset_routes.py
python3 tests/test_panel_font.py
python3 tests/test_panel_dma.py
python3 tests/test_serial_hardware.py
python3 tests/test_ble_stress.py
python3 tests/test_ble_startup_hardware.py
python3 tests/test_fixture_requests.py
python3 tests/test_hardware_bench_lock.py
python3 tests/test_usb_management.py
python3 tests/test_ble_connection_glue.py
python3 tests/test_ble_can_batch.py
python3 tests/test_wifi_handoff.py
python3 tests/test_network_discovery.py
python3 tests/test_wifi_tx_power.py
python3 tests/test_wifi_socket_diag.py
python3 tests/test_wifi_batch.py
python3 tests/test_wifi_tls_flow.py
python3 tests/test_wifi_tls_check.py
node tests/test_imu.js
node tests/test_motor_catalog.js
node tests/test_history.js
node tests/test_can_monitor.cjs
node tests/test_motor_curve.cjs
node tests/test_motor_query.cjs
node tests/test_desktop_query_metrics.cjs
node tests/test_uart_ui.cjs
node tests/test_uart_render.cjs
java_src=android/app/src/main/java/dev/qianmo/canview
javac -encoding UTF-8 -d "$test_build_dir/java" \
    "$java_src/LanTransport.java" "$java_src/MonitorCommands.java" "$java_src/FeedbackQueries.java" "$java_src/HomeTelemetry.java" \
    "$java_src/PollingStatus.java" "$java_src/ReconnectHistory.java" "$java_src/TelemetryInbox.java" "$java_src/ConnectionPriority.java" "$java_src/BleNotifications.java" "$java_src/LanSubnet.java" tests/*Test.java
for name in FeedbackQueriesTest HomeTelemetryTest MonitorCommandsTest PollingStatusTest ReconnectHistoryTest TelemetryInboxTest ConnectionPriorityTest BleNotificationsTest LanSubnetTest LanDiscoveryTest; do
    java -cp "$test_build_dir/java" "dev.qianmo.canview.$name"
done

keytool -genkeypair -alias test -keyalg EC -groupname secp256r1 -dname "CN=localhost" -validity 1 -storetype PKCS12 -keystore "$test_build_dir/test.p12" -storepass testpass -keypass testpass >/dev/null 2>&1
java -cp "$test_build_dir/java" dev.qianmo.canview.LanTransportTest "$test_build_dir/test.p12"
