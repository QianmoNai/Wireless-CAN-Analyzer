#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sdk_dir="${ANDROID_HOME:-$HOME/Android/Sdk}"
build_dir="${CAN_ANDROID_BUILD_DIR:-$HOME/.cache/can-scope-android}"
key_dir="$HOME/.local/share/can-scope"
tools_dir="$sdk_dir/build-tools/35.0.0"
android_jar="$sdk_dir/platforms/android-35/android.jar"
mkdir -p "$build_dir/classes" "$build_dir/dex" "$project_dir/dist" "$key_dir"
"$tools_dir/aapt2" compile --dir "$project_dir/app/src/main/res" -o "$build_dir/resources.zip"
"$tools_dir/aapt2" link "$build_dir/resources.zip" -I "$android_jar" --manifest "$project_dir/app/src/main/AndroidManifest.xml" -A "$project_dir/app/src/main/assets" -o "$build_dir/base.apk"
mapfile -t sources < <(find "$project_dir/app/src/main/java" -name '*.java' -type f)
javac -encoding UTF-8 -source 8 -target 8 -Xlint:-options -classpath "$android_jar" -d "$build_dir/classes" "${sources[@]}"
jar cf "$build_dir/classes.jar" -C "$build_dir/classes" .
"$tools_dir/d8" --lib "$android_jar" --min-api 26 --output "$build_dir/dex" "$build_dir/classes.jar"
cp "$build_dir/base.apk" "$build_dir/unsigned.apk"
python3 - "$build_dir" <<'PYZIP'
import pathlib, sys, zipfile
build = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(build / 'unsigned.apk', 'a', compression=zipfile.ZIP_DEFLATED) as apk:
    for dex in (build / 'dex').glob('classes*.dex'):
        apk.write(dex, dex.name)
PYZIP
"$tools_dir/zipalign" -f -p 4 "$build_dir/unsigned.apk" "$build_dir/aligned.apk"
if [[ ! -f "$key_dir/debug.keystore" ]]; then
    keytool -genkeypair -keystore "$key_dir/debug.keystore" -storepass android -keypass android -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 -dname 'CN=CAN Scope Development,O=Local Development,C=CN'
fi
"$tools_dir/apksigner" sign --ks "$key_dir/debug.keystore" --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out "$project_dir/dist/CAN-Scope-debug.apk" "$build_dir/aligned.apk"
"$tools_dir/apksigner" verify --verbose "$project_dir/dist/CAN-Scope-debug.apk"
printf '\nAPK: %s\n' "$project_dir/dist/CAN-Scope-debug.apk"
