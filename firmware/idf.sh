#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
target="${CAN_IDF_TARGET:-esp32s3}"
case "$target" in esp32|esp32s3) ;; *) printf 'Unsupported CAN_IDF_TARGET: %s\n' "$target" >&2; exit 2;; esac
if command -v idf.py >/dev/null 2>&1; then
    idf_command=(idf.py)
elif [[ -n "${IDF_PATH:-}" && -f "$IDF_PATH/tools/idf.py" && -n "${IDF_PYTHON_ENV_PATH:-}" ]]; then
    # EIM may expose idf.py as a shell function, unavailable to this child shell.
    idf_command=("$IDF_PYTHON_ENV_PATH/bin/python" "$IDF_PATH/tools/idf.py")
else
    printf 'Activate ESP-IDF first: source /path/to/esp-idf/export.sh (or EIM activation script)\n' >&2
    exit 1
fi
project_key="$(printf '%s' "$project_dir" | sha256sum | cut -c1-16)"
cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}"
cache_dir="$(mkdir -p "$cache_dir" && cd "$cache_dir" && pwd -P)"
# GCC specs parsing can corrupt non-ASCII build paths even when CMake succeeds.
if [[ -n "$(printf '%s' "$cache_dir" | LC_ALL=C tr -d '\000-\177')" ]]; then
    cache_dir="/tmp/can-scope-$UID"
fi
build_dir="${CAN_IDF_BUILD_DIR:-$cache_dir/can-scope-firmware/$project_key/$target}"
mkdir -p "$build_dir"
# Keep the lock outside the directory removed by idf.py fullclean.
exec 9>"$build_dir.lock"
flock -n 9 || { printf 'Another firmware build is using %s\n' "$build_dir" >&2; exit 1; }
# Preserve existing menuconfig settings when moving from the old in-tree build.
legacy_config="$project_dir/build/$target.sdkconfig"
if [[ -z "${CAN_IDF_BUILD_DIR:-}" && ! -f "$build_dir.sdkconfig" && -f "$legacy_config" ]]; then
    cp -- "$legacy_config" "$build_dir.sdkconfig"
fi
cd "$project_dir"
"${idf_command[@]}" -B "$build_dir" -D "SDKCONFIG=$build_dir.sdkconfig" -D "IDF_TARGET=$target" \
    -D "SDKCONFIG_DEFAULTS=$project_dir/sdkconfig.defaults;$project_dir/sdkconfig.defaults.$target" "$@"
