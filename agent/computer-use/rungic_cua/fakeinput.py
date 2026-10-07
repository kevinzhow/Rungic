"""Pointer and keyboard in an agent workspace (docs/research/91).

A workspace is a KWin of the agent's own, with nobody there to allow a RemoteDesktop portal
session, so input goes straight to that KWin's fake-input protocol through the helper
rungic-workspace-input (granted it by its desktop file). The same interface as
portal.RemoteInput: the tools do not care which one they drive.
"""
from __future__ import annotations

import os
import random
import subprocess
import threading
import time

from .portal import BTN_LEFT, keysym

HELPER = os.environ.get('RUNGIC_WORKSPACE_INPUT', '/usr/libexec/rungic-workspace-input')
AXIS_NOTCH = 15.0       # pointer axis units a wheel notch moves


class WorkspaceInput:
    def __init__(self, cursor=None) -> None:
        """`cursor` returns the pointer's current global position (KWin)."""
        self.cursor = cursor
        self.position: tuple[float, float] | None = None
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def start(self, timeout: float = 10.0) -> None:
        if self.process and self.process.poll() is None:
            return
        self.process = subprocess.Popen([HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def close(self) -> None:
        if self.process:
            self.process.stdin.close()
            try:
                self.process.wait(2)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def _send(self, line: str) -> None:
        with self.lock:
            self.start()
            self.process.stdin.write(line + '\n')
            self.process.stdin.flush()
            answer = self.process.stdout.readline().strip()
        if answer != 'ok':
            raise RuntimeError(f'workspace input: {answer or "the helper stopped"}')

    # ---- input ----------------------------------------------------------------------------
    def glide(self, x: float, y: float) -> None:
        """Move there in a few steps (~0.1 s) instead of jumping, as a hand would."""
        start = self.cursor() if self.cursor else self.position
        if start is None:
            start = (x, y)
        steps = 6
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)  # ease in and out
            self._send(f'move {start[0] + (x - start[0]) * t:.2f} {start[1] + (y - start[1]) * t:.2f}')
            if i < steps:
                time.sleep(0.016 + random.random() * 0.008)
        self.position = (x, y)

    def _button(self, button: int, pressed: bool) -> None:
        self._send(f'button {button} {1 if pressed else 0}')

    def click(self, x: float, y: float, *, button: int = BTN_LEFT, count: int = 1) -> None:
        self.glide(x, y)
        time.sleep(0.04 + random.random() * 0.04)
        for i in range(count):
            self._button(button, True)
            time.sleep(0.05 + random.random() * 0.04)
            self._button(button, False)
            if i + 1 < count:
                time.sleep(0.08 + random.random() * 0.04)

    def press(self, x: float, y: float, *, button: int = BTN_LEFT) -> None:
        self.glide(x, y)
        time.sleep(0.04 + random.random() * 0.04)
        self._button(button, True)

    def release(self, *, button: int = BTN_LEFT) -> None:
        self._button(button, False)

    def type_text(self, text: str) -> None:
        """Use the helper's actual keymap, preserving case and shifted symbols.

        One framed request lets the helper reject unsupported text before typing
        a prefix. Key chords retain their explicit modifiers.
        """
        if text:
            self._send('text ' + text.encode('utf-8').hex())

    def scroll_notches(self, axis: int, notches: int) -> None:
        self._send(f'axis {axis} {notches * AXIS_NOTCH:.1f}')

    def scroll(self, x: float, y: float, direction: str, steps: int = 3) -> None:
        self.glide(x, y)
        axis = 0 if direction in ('UP', 'DOWN') else 1
        self.scroll_notches(axis, -steps if direction in ('UP', 'LEFT') else steps)

    def key(self, sym: int, pressed: bool) -> None:
        self._send(f'key {sym} {1 if pressed else 0}')

    def chord(self, keys: list[str]) -> None:
        syms = [keysym(k) for k in keys]
        for sym in syms:
            self.key(sym, True)
            time.sleep(0.02 + random.random() * 0.02)
        for sym in reversed(syms):
            self.key(sym, False)
            time.sleep(0.01 + random.random() * 0.02)
