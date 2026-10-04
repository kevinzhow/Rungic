#!/bin/bash
# Real Android IntentFilter and SMS PDU regressions, without using a SIM or accessing the inbox.
# Build on a host with the APK SDK/JDK; --run-only executes the dex on one explicit device.
set -euo pipefail
task_root=$(cd "$(dirname "$0")/.." && pwd)
task_out=${RUNGIC_SMS_TEST_OUT:-$task_root/.work/build/sms-intents}
task_mode=${1:-all}
case "$task_mode" in all|--build-only|--run-only) ;; *) echo 'Use --build-only or --run-only' >&2; exit 2 ;; esac
if [ "$task_mode" != --run-only ]; then
    task_sdk=${ANDROID_HOME:-$HOME/android-sdk}
    task_jar=${RUNGIC_ANDROID_JAR:-$task_sdk/platforms/android-36/android.jar}
    task_bt=${RUNGIC_ANDROID_BUILD_TOOLS:-$task_sdk/build-tools/36.0.0}
    mkdir -p "$task_out/classes" "$task_out/dex"
    javac -encoding UTF-8 -source 8 -target 8 -classpath "$task_jar" -d "$task_out/classes" \
        "$task_root/android/app/src/com/rungic/plasma/SmsIntents.java" \
        "$task_root/android/app/src/com/rungic/plasma/SmsState.java" \
        "$task_root/android/sms-tests/"*.java
    "$task_bt/d8" --lib "$task_jar" --min-api 30 --output "$task_out/dex" \
        "$task_out/classes/com/rungic/plasma/"*.class
fi
if [ "$task_mode" != --build-only ]; then
    : "${RUNGIC_SERIAL:?Set RUNGIC_SERIAL to the test phone serial}"
    task_remote=/data/local/tmp/rungic-sms-intents.$$
    trap 'adb -P 5037 -s "$RUNGIC_SERIAL" shell rm -rf "$task_remote" >/dev/null' EXIT
    adb -P 5037 -s "$RUNGIC_SERIAL" shell mkdir -p "$task_remote"
    adb -P 5037 -s "$RUNGIC_SERIAL" push -q "$task_out/dex/classes.dex" "$task_remote/classes.dex"
    adb -P 5037 -s "$RUNGIC_SERIAL" shell "CLASSPATH=$task_remote/classes.dex app_process /system/bin com.rungic.plasma.SmsIntentDriver"
    adb -P 5037 -s "$RUNGIC_SERIAL" shell "CLASSPATH=$task_remote/classes.dex app_process /system/bin com.rungic.plasma.SmsReceiptDriver"
fi
