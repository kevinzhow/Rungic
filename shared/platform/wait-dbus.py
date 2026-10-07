#!/usr/bin/python3
"""Wait for a desktop dependency to own its session-bus name before starting a client.

Shared by audio-follow and the voice overlay; replaces the overlay's inline startup loop.
NameHasOwner never activates the dependency. A failed/absent dependency remains a startup error.
"""
import argparse
import math
import sys
import time

from gi.repository import Gio, GLib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0 or not Gio.dbus_is_name(args.name):
        parser.error('requires a valid bus name and a finite positive timeout')
    deadline = time.monotonic() + args.timeout
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            owned = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                  'org.freedesktop.DBus', 'NameHasOwner',
                                  GLib.Variant('(s)', (args.name,)), None,
                                  Gio.DBusCallFlags.NO_AUTO_START,
                                  max(1, min(1000, int(remaining * 1000))), None).unpack()[0]
            if owned:
                return 0
            time.sleep(max(0, min(.1, deadline - time.monotonic())))
    except GLib.Error as error:
        print(f'{args.name}: session bus unavailable: {error.message}', file=sys.stderr)
        return 1
    print(f'{args.name}: not on the session bus after {args.timeout:g} s', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
