#!/usr/bin/python3
"""rungic-sms: text messages through Android's SIM (the platform bridge's op sms, AndroidSmsBridge).

  rungic-sms send NUMBER TEXT...        send; exit 0 once the radio reports it sent
  rungic-sms list [--from NUMBER] [--box inbox|sent|all] [--since SECONDS_AGO] [--limit N] [--wait SECONDS]

Output is one JSON object. `list --wait` waits (up to SECONDS) for a message at or after --after (send result submittedAt), or the start of
the wait, for example a service number's reply. Android keeps the messages; nothing is stored here.
"""
import argparse
import json
import os
import math
import re
import socket
import sys
import time

SOCKET = os.environ.get('RUNGIC_PLATFORM_SOCKET', '/mnt/android-wayland/platform.sock')
MAX_TEXT = 1000  # AndroidSmsBridge.MAX_TEXT


def request(data, timeout):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(SOCKET)
        conn.sendall(json.dumps(data, ensure_ascii=False).encode() + b'\n')
        with conn.makefile('rb') as stream:
            raw = stream.readline(524289)
    if len(raw) > 524288:
        raise ValueError('The Android side sent a response that is too large')
    if not raw:
        raise ConnectionError('The Android side closed the connection (Rungic APK older than 2.31?)')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('Invalid SMS response: expected an object')
    if 'error' in result and result.get('status') not in ('failed', 'pending'):
        raise RuntimeError(result['error'])
    return result


def number(value):
    value = re.sub(r'[\s-]', '', value)
    if not re.fullmatch(r'\+?[0-9]{3,20}', value):
        raise ValueError('Invalid phone number')
    return value


def send(recipient, text, subscription=None):
    if not text or len(text) > MAX_TEXT:
        raise ValueError(f'The text must be 1 to {MAX_TEXT} characters')
    # The bridge waits up to 45 s for the radio and 15 s for a delivery report.
    data = {'op': 'sms', 'action': 'send', 'to': number(recipient), 'text': text}
    if subscription is not None:
        data['subscription'] = subscription
    result = request(data, timeout=75)
    if result.get('status') not in ('sent', 'failed', 'pending'):
        raise ValueError('Invalid SMS send status')
    return result


def list_messages(box='inbox', sender=None, since_ms=None, limit=20):
    data = {'op': 'sms', 'action': 'list', 'box': box, 'limit': limit}
    if sender:
        data['from'] = number(sender)
    if since_ms is not None:
        data['since'] = int(since_ms)
    result = request(data, timeout=10)
    if not isinstance(result.get('messages'), list):
        raise ValueError('Invalid SMS list response')
    return result


def wait_for(sender, box, since_ms, limit, seconds, clock=time.monotonic, sleep=time.sleep):
    """Poll until a message newer than `since_ms` arrives (or `seconds` pass)."""
    deadline = clock() + seconds
    while True:
        result = list_messages(box, sender, since_ms, limit)
        if result.get('messages'):
            return result
        if clock() >= deadline:
            return {**result, 'timedOut': True}
        sleep(min(3, max(0, deadline - clock())))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='rungic-sms', description="Text messages through Android's SIM.")
    commands = parser.add_subparsers(dest='command', required=True)
    sending = commands.add_parser('send', help='send a text message')
    sending.add_argument('number')
    sending.add_argument('text', nargs='+')
    sending.add_argument('--subscription', type=int, help='active SMS SIM subscription ID')
    listing = commands.add_parser('list', help='list messages, newest first')
    listing.add_argument('--from', dest='sender')
    listing.add_argument('--box', choices=['inbox', 'sent', 'all'], default='inbox')
    since = listing.add_mutually_exclusive_group()
    since.add_argument('--since', type=float, metavar='SECONDS_AGO')
    since.add_argument('--after', type=int, metavar='TIMESTAMP_MS', help='start at send result submittedAt')
    listing.add_argument('--limit', type=int, default=20)
    listing.add_argument('--wait', type=float, metavar='SECONDS')
    args = parser.parse_args(argv)
    try:
        if args.command == 'send':
            result = send(args.number, ' '.join(args.text), args.subscription)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get('status') == 'sent' else 1
        for value in (args.since, args.wait):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError('Time arguments must be finite and non-negative')
        if args.wait is not None and args.wait > 300:
            raise ValueError('Wait must be at most 300 seconds')
        if args.after is not None and args.after < 0:
            raise ValueError('Timestamp must be non-negative')
        since_ms = args.after
        if since_ms is None and (args.since is not None or args.wait is not None):
            since_ms = (time.time() - (args.since or 0)) * 1000
        limit = max(1, min(100, args.limit))
        if args.wait is not None:
            result = wait_for(args.sender, args.box, since_ms, limit, args.wait)
        else:
            result = list_messages(args.box, args.sender, since_ms, limit)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get('timedOut') else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
