#!/usr/bin/env bash
# Standalone build only; never flash or use the main project's build directory.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
action="${1:-build}"
case "$action" in build|app) ;; *) printf 'Use build.sh [build|app]\n' >&2; exit 2;; esac
if [[ $# -gt 1 ]]; then printf 'Unexpected arguments\n' >&2; exit 2; fi
power_qdbm="${WIFI_TXTEST_POWER_QDBM:-84}"
if [[ ! "$power_qdbm" =~ ^[0-9]{1,2}$ ]] || (( 10#$power_qdbm < 8 || 10#$power_qdbm > 84 )); then
    printf 'WIFI_TXTEST_POWER_QDBM must be an integer in 8..84\n' >&2; exit 2
fi
power_qdbm="$((10#$power_qdbm))"
if [[ -z "${IDF_PATH:-}" || -z "${IDF_PYTHON_ENV_PATH:-}" ]]; then
    printf 'First source /home/qianmo/.espressif/tools/activate_idf_v6.0.2.sh\n' >&2
    exit 1
fi
project_key="$(printf '%s' "$project_dir" | sha256sum | cut -c1-16)"
build_dir="${WIFI_TXTEST_BUILD_DIR:-$HOME/.cache/can-scope-wifi-txtest/$project_key/esp32s3}"
mkdir -p "$build_dir"
exec 9>"$build_dir.lock"
flock -n 9 || { printf 'Another TX test build is active\n' >&2; exit 1; }
cd "$project_dir"
"$IDF_PYTHON_ENV_PATH/bin/python" "$IDF_PATH/tools/idf.py" \
    -B "$build_dir" -D "IDF_TARGET=esp32s3" -D "SDKCONFIG=$build_dir.sdkconfig" \
    -D "SDKCONFIG_DEFAULTS=$project_dir/sdkconfig.defaults" \
    -D "WIFI_TXTEST_POWER_QDBM=$power_qdbm" "$action"
