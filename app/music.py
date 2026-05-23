"""
Procedural background music — fully offline, royalty-free by construction.

Generates a soft lo-fi / ambient bed: a slow chord pad, a gentle sub pulse, and
optional soft percussion. Mixed low so it sits under the voiceover.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 22050

# Mood -> (root midi note, chord progressions, bpm, has_beat)
MOODS = {
    "calm":       (48, [[0, 4, 7, 11], [-3, 0, 4, 7], [-5, -1, 2, 7], [-7, -3, 0, 4]], 72, False),
    "uplifting":  (50, [[0, 4, 7], [5, 9, 12], [7, 11, 14], [2, 5, 9]], 90, True),
    "epic":       (45, [[0, 7, 12], [-2, 5, 10], [-4, 3, 8], [-5, 2, 7]], 80, True),
    "mysterious": (47, [[0, 3, 7, 10], [-2, 1, 5, 8], [-4, 0, 3, 7], [-1, 2, 6, 9]], 68, False),
    "dreamy":     (52, [[0, 4, 7, 9], [2, 5, 9, 12], [-3, 0, 4, 7], [5, 9, 12, 16]], 76, False),
    "tense":      (44, [[0, 3, 6], [-2, 1, 5], [-5, -2, 2], [-7, -4, 0]], 85, True),
}

DEFAULT_MOOD = "uplifting"

# Mapping from video themes to music moods
THEME_MOOD = {
    "midnight": "mysterious",
    "sunset":   "uplifting",
    "neon":     "tense",
    "emerald":  "dreamy",
    "mono":     "calm",
    "crimson":  "epic",
    "ocean":    "calm",
}


def _midi_to_freq(n: float) -> float:
    return 440.0 * (2 ** ((n - 69) / 12.0))


def _adsr(n: int, a: float, d: float, s: float, r: float, rate: int) -> np.ndarray:
    env = np.ones(n, dtype=np.float32) * s
    ai = int(a * rate)
    di = int(d * rate)
    ri = int(r * rate)
    if ai > 0:
        env[:ai] = np.linspace(0, 1, ai)
    if di > 0:
        env[ai:ai + di] = np.linspace(1, s, min(di, n - ai))
    if ri > 0:
        env[-ri:] = np.linspace(env[-ri], 0, ri)
    return env


def _pad_chord(notes: list[int], root: int, dur: float, rate: int, rng) -> np.ndarray:
    n = int(dur * rate)
    t = np.arange(n) / rate
    out = np.zeros(n, dtype=np.float32)
    # Slight random pitch drift per note for a "detuned tape" feel
    drift = rng.uniform(-0.003, 0.003, len(notes))
    for idx, semi in enumerate(notes):
        f = _midi_to_freq(root + semi) * (1.0 + drift[idx])
        for k, amp in [(1, 1.0), (2, 0.35), (3, 0.18), (4, 0.08)]:
            detune = 1.0 + (0.002 * ((semi % 3) - 1))
            out += amp * np.sin(2 * np.pi * f * k * detune * t)
    out /= max(1, len(notes)) * 1.6
    # slow tremolo — speed varied by seed
    tremolo_rate = 0.12 + rng.random() * 0.08
    out *= 0.85 + 0.15 * np.sin(2 * np.pi * tremolo_rate * t)
    env = _adsr(n, a=dur * 0.25, d=dur * 0.2, s=0.8, r=dur * 0.3, rate=rate)
    return out * env


def _soft_kick(rate: int, dur: float = 0.18) -> np.ndarray:
    n = int(dur * rate)
    t = np.arange(n) / rate
    freq = 110 * np.exp(-t * 28) + 45
    body = np.sin(2 * np.pi * np.cumsum(freq) / rate)
    env = np.exp(-t * 16)
    return (body * env * 0.5).astype(np.float32)


def _hat(rate: int, dur: float = 0.05) -> np.ndarray:
    n = int(dur * rate)
    noise = np.random.uniform(-1, 1, n).astype(np.float32)
    env = np.exp(-np.arange(n) / rate * 90)
    return noise * env * 0.12


def generate(
    duration: float,
    mood: str = DEFAULT_MOOD,
    seed: int = 7,
    theme: str | None = None,
) -> np.ndarray:
    """Return float32 mono music bed of `duration` seconds."""
    # Resolve mood from theme if provided
    if theme and theme in THEME_MOOD:
        mood = THEME_MOOD[theme]

    rng = np.random.default_rng(seed % (2**31))
    root, prog, bpm, has_beat = MOODS.get(mood, MOODS[DEFAULT_MOOD])

    # Slight root variation per seed for tonal variety
    root_shift = int(rng.integers(-2, 3))
    root += root_shift

    total = int(duration * SAMPLE_RATE) + SAMPLE_RATE
    track = np.zeros(total, dtype=np.float32)

    # chord pad: 2-bar chords
    beat = 60.0 / bpm
    chord_dur = beat * 4
    pos = 0.0
    ci = 0
    while pos < duration + chord_dur:
        chord = prog[ci % len(prog)]
        seg = _pad_chord(chord, root, chord_dur * 1.05, SAMPLE_RATE, rng)
        s = int(pos * SAMPLE_RATE)
        e = min(s + len(seg), total)
        if s < total and e > s:
            track[s:e] += seg[:e - s] * 0.6
        pos += chord_dur
        ci += 1

    # percussion
    if has_beat:
        kick = _soft_kick(SAMPLE_RATE)
        hat = _hat(SAMPLE_RATE)
        step = 0.0
        i = 0
        while step < duration:
            s = int(step * SAMPLE_RATE)
            if s >= total:
                break
            if i % 2 == 0:
                e = min(s + len(kick), total)
                if e > s:
                    track[s:e] += kick[:e - s]
            e = min(s + len(hat), total)
            if e > s:
                track[s:e] += hat[:e - s] * (0.8 + 0.4 * rng.random())
            step += beat / 2
            i += 1

    # airy noise pad (very quiet)
    air = rng.normal(0, 1, total).astype(np.float32)
    b = 0.0008
    for _ in range(2):
        air = np.cumsum(air * b + np.concatenate([[0], air[:-1]]) * (1 - b))
    air = air / (np.max(np.abs(air)) + 1e-9) * 0.04
    track += air

    # fade proportional to video length (min 1s, max 2.5s)
    fade = int(min(2.5, max(1.0, duration * 0.08)) * SAMPLE_RATE)
    if total > 2 * fade:
        track[:fade] *= np.linspace(0, 1, fade)
        track[-fade:] *= np.linspace(1, 0, fade)

    peak = np.max(np.abs(track)) + 1e-9
    track = track / peak * 0.5
    return track[:int(duration * SAMPLE_RATE) + 1]
