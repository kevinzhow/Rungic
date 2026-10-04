"""Follow the Android host's state through its platform bridge without polling it (docs/49).

watch(topics, on_change) calls on_change() from a background thread whenever the host reports
that one of `topics` changed (op "watch": the app answers when a version it keeps for the topic
moves, bumped by Android's own callbacks), and at least every `fallback` seconds. An app from
before the op makes it poll every `legacy` seconds instead, as the services did.

Topics: network, telephony, bluetooth, capture (see HostEvents.java in the app).
Installed as rungic_host_watch.
"""
import os
import json
import socket
import threading
import time
import rungic_platform_transport

SOCKET = os.environ.get('RUNGIC_PLATFORM_SOCKET', '/mnt/android-wayland/platform.sock')


def _request(request, timeout):
    return rungic_platform_transport.request(request, timeout, ui_socket=SOCKET)


def watch(topics, on_change, fallback=60, legacy=5, name='host-watch'):
    """Start the watcher thread; on_change() runs on it (hand it to a main loop if needed)."""
    def run():
        epoch, seen = None, None
        while True:
            try:
                reply = _request({'op': 'watch', 'topics': list(topics), 'epoch': epoch, 'seen': seen,
                                  'timeout': fallback * 1000}, fallback + 10)
            except (OSError, ValueError):
                time.sleep(5)       # no host (yet): the app restarts, or the desktop is starting
                on_change()
                continue
            if 'error' in reply:    # an app without the op
                time.sleep(legacy)
                on_change()
                continue
            epoch, seen = reply['epoch'], reply['versions']
            on_change()             # a change, or the fallback interval passed
    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread
