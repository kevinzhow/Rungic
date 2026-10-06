#!/bin/sh
# Offline tests in one run (docs/95): Python and shell-sandbox tests, the account setup, the feature
# inventory (quality/README.md: tools/test_feature_inventory.py), the patch queues' headers, the APK's
# plain-Java logic and the syntax of the Android/system shell scripts.
# Device use cases are separate: tools/rungic_acceptance.py (smoke, full, install).
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
export PYTHONPYCACHEPREFIX="$root/.work/cache/python"
python=python3
[ ! -x .work/venv/bin/python ] || python=.work/venv/bin/python
failed=

# QML card tests need PySide6 from the development venv (sh tools/dev-setup.sh).
ignore=
if ! "$python" -c 'import PySide6' 2>/dev/null; then
    for f in $(grep -l 'PySide6' tools/tests/*.py tools/test_*.py); do
        ignore="$ignore --ignore=$f"
        echo "skipped $f: no PySide6 (sh tools/dev-setup.sh)"
    done
fi
# shellcheck disable=SC2086
"$python" -m pytest -q -p no:cacheprovider $ignore tools/ci tools/test_*.py tools/tests system/account || failed="$failed python"
# Patch headers of the patch queues (docs/71): fields, series, and the acceptance scenarios they name.
"$python" tools/pq.py lint || failed="$failed pq-lint"

java_out=$root/.work/build/tests-java
rm -rf "$java_out"
mkdir -p "$java_out/tmp"
if javac -encoding UTF-8 -d "$java_out" android/app/src/com/rungic/plasma/FirstBootState.java \
        android/app/src/com/rungic/plasma/ControlException.java android/app/src/com/rungic/plasma/RemovalState.java android/app/tests/*.java; then
    java -cp "$java_out" com.rungic.plasma.FirstBootStateTest "$java_out/tmp" || failed="$failed FirstBootStateTest"
    java -cp "$java_out" com.rungic.plasma.ControlExceptionTest system/account/setup.py || failed="$failed ControlExceptionTest"
    java -cp "$java_out" com.rungic.plasma.RemovalStateTest || failed="$failed RemovalStateTest"
else
    failed="$failed javac"
fi

for f in $(git ls-files system tools/ci android); do
    case $(head -n 1 "$f" 2>/dev/null) in
        '#!/system/bin/sh'*|'#!/bin/sh'*) sh -n "$f" || failed="$failed sh:$f" ;;
    esac
done

if [ -n "$failed" ]; then
    echo "FAILED:$failed"
    exit 1
fi
echo 'All offline tests passed'
