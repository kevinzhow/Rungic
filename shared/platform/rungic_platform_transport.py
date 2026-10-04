# SPDX-License-Identifier: MIT
"""Device services and presentation use separate endpoints, with no retry or UI fallback.

A future native backend may implement the same device contract. RUNGIC_DEVICE_SOCKET
selects that backend; RUNGIC_PLATFORM_SOCKET remains an explicit legacy/test override.
"""
import json
import os
import socket
import struct
import time

UI_DEFAULT = '/mnt/android-wayland/platform.sock'
DEVICE_DEFAULT = '\0com.rungic.device.v1'
DEVICE_OPS = frozenset(('network-get', 'wifi', 'network-wifi', 'bluetooth', 'telephony',
                        'sms', 'container-memory', 'screen-timeout', 'desktop-boost', 'device-status'))
DEVICE_TOPICS = frozenset(('network', 'bluetooth', 'telephony'))


def endpoint(data, ui_socket=None):
    ui = ui_socket or os.environ.get('RUNGIC_PLATFORM_SOCKET', UI_DEFAULT)
    device = data.get('op') in DEVICE_OPS
    if data.get('op') == 'watch':
        topics = data.get('topics', ())
        device = bool(topics) and set(topics).issubset(DEVICE_TOPICS)
    if not device:
        return ui
    # An explicit old endpoint is useful for older installations and contract stand-ins.
    # It is a configured destination, never a fallback after an ambiguous submission.
    return os.environ.get('RUNGIC_DEVICE_SOCKET') or (ui if ui != UI_DEFAULT else DEVICE_DEFAULT)


def request(data, timeout=4, *, ui_socket=None, limit=524288):
    path = endpoint(data, ui_socket)
    payload = json.dumps(data, ensure_ascii=False).encode() + b'\n'
    if len(payload) > 524288:
        raise ValueError('Device request too large')
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(path)
        if path == DEVICE_DEFAULT:
            _, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 0:
                raise PermissionError('Unexpected device backend identity')
        conn.sendall(payload)
        raw = bytearray()
        while b'\n' not in raw:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Device backend timed out; outcome may be unknown, do not retry automatically')
            conn.settimeout(remaining)
            part = conn.recv(min(65536, limit + 1 - len(raw)))
            if not part:
                raise ConnectionError('Device backend disconnected; outcome may be unknown, do not retry automatically')
            raw.extend(part)
            if len(raw) > limit:
                raise ValueError('Device response too large')
    result = json.loads(raw.split(b'\n', 1)[0])
    if not isinstance(result, dict):
        raise ValueError('Invalid device response: expected an object')
    return result
