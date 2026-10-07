#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Hear the phone play the checklist's test video (docs/121 E2E-05) through this computer's built-in
microphone: the video beeps 880 Hz for one second, then is silent for one second, over and over.

Run on the computer next to the phone (mibook), while the video plays:

  listen.py --seconds 8 [--source NAME]

The sound stays in memory; only the per-window measure is printed, never written. Prints one line per
0.5 s window (880 Hz share of the window's energy) and a verdict: heard when windows with a strong
880 Hz share alternate with quiet ones, one second apart, as the video does.
"""
import argparse
import array
import json
import math
import shutil
import subprocess
import sys

RATE = 16000
WINDOW = RATE // 2          # 0.5 s
TONE = 880.0


def goertzel(samples, freq, rate=RATE):
    """Power of one frequency in the samples."""
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in samples:
        s1, s2 = x + k * s1 - s2, s1
    return s1 * s1 + s2 * s2 - k * s1 * s2


def levels(samples):
    """Each window's RMS in dBFS: whether the microphone hears anything at all."""
    out = []
    for start in range(0, len(samples) - WINDOW + 1, WINDOW):
        window = samples[start:start + WINDOW]
        rms = math.sqrt(sum(x * x for x in window) / len(window)) or 1e-9
        out.append(round(20 * math.log10(rms / 32768), 1))
    return out


def shares(samples):
    """The 880 Hz share of each window's energy (0..1)."""
    out = []
    for start in range(0, len(samples) - WINDOW + 1, WINDOW):
        window = samples[start:start + WINDOW]
        energy = sum(x * x for x in window) * len(window) / 2 or 1.0
        out.append(min(1.0, goertzel(window, TONE) / energy))
    return out


def verdict(values, strong=0.2, quiet=0.05):
    """Heard: at least two strong and two quiet windows, and strong windows recur every 2 s (4 windows)."""
    strong_at = [i for i, v in enumerate(values) if v >= strong]
    quiet_n = sum(1 for v in values if v <= quiet)
    periodic = any(i + 4 in strong_at for i in strong_at) or any(i + 3 in strong_at or i + 5 in strong_at
                                                                   for i in strong_at)
    return len(strong_at) >= 2 and quiet_n >= 2 and periodic


def record(seconds, source):
    # pw-record where PipeWire runs: on mibook (Fedora) parecord over ssh delivered only zeros.
    if shutil.which('pw-record'):
        argv = ['pw-record', f'--rate={RATE}', '--channels=1', '--format=s16'] + \
            ([f'--target={source}'] if source else []) + ['-']
    else:
        argv = ['parecord', '--raw', '--format=s16le', '--channels=1', f'--rate={RATE}'] + \
            ([f'--device={source}'] if source else [])
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE)
    try:
        data = proc.stdout.read(RATE * 2 * seconds)
    finally:
        proc.terminate()
        proc.wait(5)
    return array.array('h', data[:len(data) // 2 * 2])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seconds', type=int, default=8)
    parser.add_argument('--source', help='PulseAudio source (default: the default source, the built-in mic)')
    a = parser.parse_args()
    samples = record(a.seconds, a.source)
    values = shares(samples)
    heard = verdict(values)
    print(json.dumps({'windows_880hz_share': [round(v, 3) for v in values], 'windows_dbfs': levels(samples),
                      'heard': heard}))
    sys.exit(0 if heard else 1)


if __name__ == '__main__':
    main()
