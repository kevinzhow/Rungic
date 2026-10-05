#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Krita ready for pixel art (rungic-krita-pixel-art): its Scripter plugin on, which pixel.py runs
in. Krita keeps the setting in ~/.config/kritarc, group [python], key enable_scripter; it reads it
at start, so this runs before Krita starts and changes nothing while Krita runs.

  setup.py   -> JSON: {"scripter": true, "changed": bool} or {"error": ...}
"""
import configparser
import json
import os
import subprocess
import sys
from pathlib import Path

RC = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'kritarc'


def running():
    return subprocess.run(['pgrep', '-x', 'krita'], capture_output=True).returncode == 0


def main():
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        parser.read(RC, encoding='utf-8')
    except configparser.Error as error:
        print(json.dumps({'error': f'kritarc unreadable: {error}'}))
        return 1
    if parser.has_section('python') and parser.get('python', 'enable_scripter', fallback='') == 'true':
        print(json.dumps({'scripter': True, 'changed': False}))
        return 0
    if running():
        print(json.dumps({'error': 'Krita runs: close it (desktop_window close), then run setup.py again'}))
        return 1
    # Only this one line changes: kritarc is Krita's own file, written by KConfig.
    text = RC.read_text(encoding='utf-8') if RC.exists() else ''
    if '[python]' in text.split('\n'):
        lines = text.split('\n')
        lines = [l for l in lines if not l.startswith('enable_scripter=')]
        lines.insert(lines.index('[python]') + 1, 'enable_scripter=true')
        text = '\n'.join(lines)
    else:
        text = text.rstrip('\n') + ('\n\n' if text else '') + '[python]\nenable_scripter=true\n'
    RC.parent.mkdir(parents=True, exist_ok=True)
    temporary = RC.with_name('kritarc.rungic-part')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, RC)
    print(json.dumps({'scripter': True, 'changed': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
