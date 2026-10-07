#!/bin/sh
# In the system test container (tools/system_test.py): build the Linux programs the tests use from
# the working tree in /src, install them as their packages do, then run each test as an ordinary
# user in a session bus of its own (or as root, the container being a fresh system, for a test that
# says "# system-test: as root"). One line of JSON per test on stdout ({"test", "passed", ...}).
set -eu
export LANG=C.UTF-8
for dir in agent/screen agent/workspace; do
    cmake -S /src/$dir -B /build/$dir -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=RelWithDebInfo >/build.log 2>&1 \
        && cmake --build /build/$dir -j"$(nproc)" >>/build.log 2>&1 \
        && cmake --install /build/$dir >>/build.log 2>&1 \
        || { cat /build.log >&2; echo "{\"test\": \"build $dir\", \"passed\": false, \"log\": \"$(tail -5 /build.log | tr '\"\n' "' ")\"}"; exit 1; }
done
sh /src/tools/system/build-media.sh >/build.log 2>&1 \
    || { echo "{\"test\": \"build media\", \"passed\": false, \"log\": \"$(tail -5 /build.log | tr '\"\n' "' ")\"}"; exit 1; }
failed=0
for test in "$@"; do
    path=/src/tools/system:/src/tools:/src/agent/computer-use
    if grep -q '^# system-test: as root' "/src/tools/system/tests/$test.py"; then
        out=$(cd /tmp && PYTHONPATH=$path python3 "/src/tools/system/tests/$test.py" 2>/tmp/$test.err) || failed=1
    else
        out=$(su tester -c "cd /tmp && PYTHONPATH=$path dbus-run-session -- python3 /src/tools/system/tests/$test.py" 2>/tmp/$test.err) || failed=1
    fi
    printf '%s\n' "$out" | grep '^{' || { echo "{\"test\": \"$test\", \"passed\": false, \"log\": \"$(tail -8 /tmp/$test.err | tr '\"\n' "' ")\"}"; failed=1; }
done
exit $failed
