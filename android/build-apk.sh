#!/bin/bash
set -euo pipefail
task_root=$(cd "$(dirname "$0")/.." && pwd)
# SDK locations differ per machine; override with RUNGIC_ANDROID_BUILD_TOOLS / RUNGIC_ANDROID_JAR.
task_sdk=${ANDROID_HOME:-$HOME/android-sdk}
task_bt=${RUNGIC_ANDROID_BUILD_TOOLS:-}
[ -n "$task_bt" ] || for task_bt in "$HOME/moto-android-sdk/android-16" $(ls -d "$task_sdk"/build-tools/* 2>/dev/null | sort -V -r); do [ -x "$task_bt/aapt2" ] && break; done
task_jar=${RUNGIC_ANDROID_JAR:-}
[ -n "$task_jar" ] || for task_jar in "$HOME/moto-android-sdk/android-36/android.jar" "$task_sdk/platforms/android-36/android.jar"; do [ -f "$task_jar" ] && break; done
task_out=${RUNGIC_APK_OUT:-$task_root/.work/refs/plasma-mobile-20260923}
# Prebuilt lib/arm64-v8a from the Rust build, or tools/pull_installed_native_libs.py.
task_native=${RUNGIC_NATIVE_LIBS:-$task_root/.work/refs/plasma-mobile-20260923/native-libs}
task_version=$(sed -n 's/.*android:versionName="\([^"]*\)".*/\1/p' "$task_root/android/app/AndroidManifest.xml")
task_key=${RUNGIC_APK_KEYSTORE:-$task_root/signing/development/launcher-signing.p12}
task_build=$task_out/apk-build
mkdir -p "$task_build/classes" "$task_build/dex"
task_ocr=$task_build/ocr
rm -rf "$task_ocr" "$task_build/assets"
cp -r "$task_root/android/app/assets" "$task_build/assets"
# On-device OCR (docs/64, docs/73), RUNGIC_APK_OCR:
#   runtime (default)  LiteRT and librungicocr.so in the APK; the APK downloads the PP-OCRv6 models
#                      (~77 MB, pinned in provenance/ocr-20260925) on first use
#   bundle             the models in the APK as well (~86 MB)
#   none               no OCR: the Linux side reads text on the CPU (RapidOCR)
# librungicocr.so is built with the NDK (RUNGIC_ANDROID_NDK, else the newest under $task_sdk/ndk).
task_apk_ocr=${RUNGIC_APK_OCR:-runtime}
case $task_apk_ocr in runtime|bundle|none) ;; *) echo "RUNGIC_APK_OCR: runtime, bundle or none" >&2; exit 2 ;; esac
if [ "$task_apk_ocr" != none ]; then
    task_ndk=${RUNGIC_ANDROID_NDK:-$(ls -d "$task_sdk"/ndk/* 2>/dev/null | sort -V | tail -1)}
    task_clang=$task_ndk/toolchains/llvm/prebuilt/linux-x86_64/bin
    python3 "$task_root/tools/fetch_ocr_assets.py" "$task_ocr" $([ "$task_apk_ocr" = bundle ] && echo --models)
    mkdir -p "$task_ocr/include/litert/build_common"
    # LiteRT's C API headers from its pinned source (packages/litert, docs/71).
    task_litert=$task_build/litert
    python3 "$task_root/tools/pq.py" source litert --output "$task_litert" >/dev/null
    cp "$task_litert/litert/build_common/config/build_config_gpu.h" "$task_ocr/include/litert/build_common/build_config.h"
    "$task_clang/aarch64-linux-android30-clang++" -std=c++17 -O2 -fPIC -shared \
        -Wall -Wextra -Wno-unused-parameter -I"$task_ocr/include" -I"$task_litert" \
        -o "$task_ocr/lib/arm64-v8a/librungicocr.so" "$task_root"/android/app/jni/ocr/{ppocr,jni}.cc \
        -L"$task_ocr/lib/arm64-v8a" -lLiteRt -llog -static-libstdc++ -Wl,--no-undefined
    "$task_clang/llvm-strip" --strip-unneeded "$task_ocr/lib/arm64-v8a/librungicocr.so"
    cp -r "$task_ocr/assets/ocr" "$task_build/assets/ocr"
fi
# librungicmedia.so (jni/media): decoded HardwareBuffers' DMA-BUFs for the codec bridge (docs/108).
# Without the NDK the APK still works: decoded frames then go through shared memory.
task_ndk=${RUNGIC_ANDROID_NDK:-$(ls -d "$task_sdk"/ndk/* 2>/dev/null | sort -V | tail -1 || true)}
task_media=$task_build/media
rm -rf "$task_media"
task_media_clang=$task_ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang
if [ -n "$task_ndk" ] && [ -x "$task_media_clang" ]; then
    mkdir -p "$task_media/lib/arm64-v8a"
    "$task_media_clang" -std=c11 -O2 -fPIC -shared -Wall -Wextra -o "$task_media/lib/arm64-v8a/librungicmedia.so" \
        "$task_root/android/app/jni/media/buffers.c" "$task_root/android/app/jni/media/codec_session.c" \
        -landroid -lnativewindow -lmediandk -llog -ldl -Wl,--no-undefined
    "$task_ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" --strip-unneeded "$task_media/lib/arm64-v8a/librungicmedia.so"
else
    echo "warning: no Android NDK: the APK goes without librungicmedia.so (decoded frames through shared memory)" >&2
fi
cd "$task_root/android/app"
"$task_bt/aapt2" compile --dir res -o "$task_build/resources.zip"
# res/values (English) and res/values-zh-rCN; --java writes R.java for getString(R.string.…).
rm -rf "$task_build/gen"; mkdir -p "$task_build/gen"
"$task_bt/aapt2" link -o "$task_build/resources.apk" -I "$task_jar" --manifest AndroidManifest.xml -A "$task_build/assets" -0 tflite \
    --java "$task_build/gen" "$task_build/resources.zip"
mapfile -t task_sources < <(find src "$task_build/gen" -name '*.java')
javac -encoding UTF-8 -source 8 -target 8 -classpath "$task_jar" -d "$task_build/classes" "${task_sources[@]}"
mapfile -t task_classes < <(find "$task_build/classes" -name '*.class')
"$task_bt/d8" --lib "$task_jar" --min-api 30 --output "$task_build/dex" "${task_classes[@]}"
cp "$task_build/resources.apk" "$task_build/unsigned.apk"
(cd "$task_build/dex" && zip -q "$task_build/unsigned.apk" classes.dex)
(cd "$task_native" && zip -qr "$task_build/unsigned.apk" lib)
[ ! -d "$task_ocr/lib" ] || (cd "$task_ocr" && zip -qr "$task_build/unsigned.apk" lib)
[ ! -d "$task_media/lib" ] || (cd "$task_media" && zip -qr "$task_build/unsigned.apk" lib)
"$task_bt/zipalign" -f 4 "$task_build/unsigned.apk" "$task_build/aligned.apk"
"$task_bt/apksigner" sign --ks "$task_key" --ks-key-alias launcher --ks-pass pass:android --out "$task_out/Rungic-$task_version.apk" "$task_build/aligned.apk"
"$task_bt/apksigner" verify "$task_out/Rungic-$task_version.apk"
