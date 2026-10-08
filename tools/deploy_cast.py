#!/usr/bin/env python3
"""Upgrade only casting on an explicitly selected Android device; no account/rootfs changes."""
import argparse
from pathlib import Path
import shlex
import subprocess
import time
import cast_payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--port', type=int, default=5037)
    parser.add_argument('--jar', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    adb = ['adb', '-P', str(args.port), '-s', args.serial]

    def root(script):
        return subprocess.check_output(adb + ['shell', 'su', '-c', 'sh'], input=script.encode(), timeout=60).decode()

    before = root('set -e\nid\ngetprop ro.build.fingerprint\ngetprop ro.product.device\ngetenforce\n')
    (args.output / 'identity.txt').write_text(before)
    if 'uid=0(' not in before:
        raise RuntimeError('root required')
    payload = args.output / 'payload'
    cast_payload.stage(payload, args.jar)
    remote = '/data/local/tmp/rungic-cast-deploy-' + str(time.time_ns())
    root('set -e\nmkdir -p ' + shlex.quote(remote) + '\nchown 2000:2000 ' + remote + '\nchmod 700 ' + remote + '\n'
         'if [ -d /data/adb/rungic-wfd ]; then tar -czf ' + remote + '/before.tar.gz -C /data/adb rungic-wfd; fi\n'
         'tar -czf ' + remote + '/services-before.tar.gz -C /data/adb service.d\n')
    subprocess.run(adb + ['pull', remote, str(args.output / 'backup')], check=True, capture_output=True)
    subprocess.run(adb + ['push', str(payload), remote + '/payload'], check=True, capture_output=True)
    script = 'set -e\n/system/bin/sh ' + remote + '/payload/install.sh ' + remote + '/payload\n'
    # Dedicated lock prevents concurrent boot/manual upgrades. No lock is inherited by the watcher.
    # The root provider's BusyBox (Magisk or KernelSU, system/root-provider).
    provider = '. /data/adb/rungic-plasma/root-provider\n'
    result = root('printf %s ' + shlex.quote(script) + ' > ' + remote + '/install.sh\n' + provider +
                  '"$RUNGIC_BUSYBOX" flock /data/adb/rungic-cast-install.lock sh ' + remote + '/install.sh\n')
    result += root('if [ -f /data/adb/rungic-wfd/watch.pid ]; then\n'
                   'p=$(cat /data/adb/rungic-wfd/watch.pid)\n'
                   'case "$p" in ""|*[!0-9]*) ;; *) '
                   'if [ "$(tr "\\000" " " < /proc/$p/cmdline 2>/dev/null)" = "app_process /system/bin com.rungic.cast.Main watch " ]; then kill "$p"; fi ;; esac\nfi\n')
    result += root(provider + '/system/bin/sh /data/adb/service.d/rungic-wfd-sepolicy.sh\n'
                   '"$RUNGIC_BUSYBOX" setsid /system/bin/sh /data/adb/service.d/rungic-cast-watch.sh </dev/null >/dev/null 2>&1 &\n')
    result += root('/data/adb/rungic-wfd/rungic-cast capabilities\n')
    (args.output / 'install.log').write_text(result)
    root('rm -rf ' + shlex.quote(remote))
    print(result)


if __name__ == '__main__':
    main()
