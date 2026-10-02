#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
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
build_dir="${CAN_IDF_BUILD_DIR:-$project_dir/build/$target}"
cd "$project_dir"
"${idf_command[@]}" -B "$build_dir" -D "SDKCONFIG=$build_dir.sdkconfig" -D "IDF_TARGET=$target" \
    -D "SDKCONFIG_DEFAULTS=$project_dir/sdkconfig.defaults;$project_dir/sdkconfig.defaults.$target" "$@"
