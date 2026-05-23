"""
Neural TTS engine built on Piper (offline, high quality).

Synthesizes one caption line at a time so we get the exact audio duration of
each line — that lets captions stay perfectly in sync with the voice, and lets
us distribute words evenly for karaoke-style highlighting.
"""

from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

VOICES_DIR = Path(__file__).resolve().parent.parent / "voices"

# Friendly name -> piper model file stem
VOICE_MODELS = {
    "ryan":   "en_US-ryan-medium",       # warm male narrator
    "male":   "en_US-hfc_male-medium",   # clear neutral male
    "female": "en_US-hfc_female-medium", # clear neutral female
    "lessac": "en_US-lessac-medium",     # smooth neutral
}

DEFAULT_VOICE = "ryan"
SAMPLE_RATE = 22050

_VOICE_CACHE: dict[str, object] = {}


@dataclass
class Line:
    """One caption line with its timing once rendered."""
    text: str
    audio: np.ndarray = field(default=None, repr=False)  # float32 mono
    duration: float = 0.0
    start: float = 0.0   # filled in by render()


def available_voices() -> list[str]:
    out = []
    for name, stem in VOICE_MODELS.items():
        if (VOICES_DIR / f"{stem}.onnx").exists():
            out.append(name)
    return out


def _load_voice(name: str):
    name = name if name in VOICE_MODELS else DEFAULT_VOICE
    if name in _VOICE_CACHE:
        return _VOICE_CACHE[name]
    from piper import PiperVoice
    stem = VOICE_MODELS[name]
    model = VOICES_DIR / f"{stem}.onnx"
    config = VOICES_DIR / f"{stem}.onnx.json"
    if not model.exists():
        raise FileNotFoundError(
            f"Voice model {model} not found. Run setup.sh to download voices."
        )
    voice = PiperVoice.load(str(model), config_path=str(config))
    _VOICE_CACHE[name] = voice
    return voice


def _synthesize(voice, text: str, length_scale: float) -> np.ndarray:
    """Return float32 mono audio for `text`."""
    try:
        from piper import SynthesisConfig
        syn = SynthesisConfig(length_scale=length_scale)
        chunks = list(voice.synthesize(text, syn_config=syn))
    except Exception:
        chunks = list(voice.synthesize(text))
    if not chunks:
        return np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)
    parts = [c.audio_float_array.astype(np.float32) for c in chunks]
    return np.concatenate(parts)


def _soft_compress(audio: np.ndarray, threshold: float = 0.4, ratio: float = 2.0) -> np.ndarray:
    """Sliding-window RMS compressor — broadcast-style voice levelling, O(N) via cumsum."""
    n = len(audio)
    if n < 2:
        return audio
    win = max(1, int(0.04 * SAMPLE_RATE))  # 40ms lookback window
    sq = audio.astype(np.float64) ** 2
    csq = np.zeros(n + 1, dtype=np.float64)
    np.cumsum(sq, out=csq[1:])
    hi = np.arange(1, n + 1)
    lo = np.maximum(hi - win, 0)
    rms = np.sqrt(np.maximum((csq[hi] - csq[lo]) / (hi - lo), 1e-10)).astype(np.float32)
    gain = np.where(rms > threshold,
                    (threshold / rms) * (rms / threshold) ** (1.0 / ratio),
                    np.float32(1.0))
    return (audio * gain).astype(np.float32)


def render_lines(
    lines: list[str],
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
    gap: float = 0.25,
    content_seed: int = 0,
) -> tuple[list[Line], np.ndarray]:
    """
    Synthesize every line, insert a varied silent gap between lines, and return
    (timed_lines, full_audio_float32).

    `speed` > 1 is faster speech (piper length_scale = 1/speed).
    Gaps vary slightly per line for a more natural cadence.
    """
    v = _load_voice(voice)
    length_scale = 1.0 / max(0.5, min(2.0, speed))

    # Seeded RNG for reproducible but varied gaps
    rng = np.random.default_rng(content_seed % (2**31))

    timed: list[Line] = []
    buffers: list[np.ndarray] = []
    cursor = 0.0

    # Short lead-in — just enough to prevent a hard clip at t=0
    lead_samples = int(0.15 * SAMPLE_RATE)
    buffers.append(np.zeros(lead_samples, dtype=np.float32))
    cursor += lead_samples / SAMPLE_RATE

    for i, text in enumerate(lines):
        audio = _synthesize(v, text, length_scale)
        dur = len(audio) / SAMPLE_RATE
        line = Line(text=text, audio=audio, duration=dur, start=cursor)
        timed.append(line)
        buffers.append(audio)
        cursor += dur
        if i < len(lines) - 1:
            # Vary gap 0.18–0.38s; longer after sentences that end with a period
            base = 0.28 if text.rstrip().endswith((".","!","?")) else 0.20
            gap_s = float(rng.uniform(base - 0.05, base + 0.08))
            gap_samples = int(gap_s * SAMPLE_RATE)
            buffers.append(np.zeros(gap_samples, dtype=np.float32))
            cursor += gap_s

    # Short tail
    tail_samples = int(0.35 * SAMPLE_RATE)
    buffers.append(np.zeros(tail_samples, dtype=np.float32))

    full = np.concatenate(buffers) if buffers else np.zeros(1, dtype=np.float32)

    # RMS compression before normalization — evens out loud/quiet sentences
    full = _soft_compress(full)

    # Single gentle overall normalization — preserves natural dynamics
    peak = float(np.max(np.abs(full)))
    if peak > 0.01:
        full = full * (0.88 / peak)

    return timed, full


def write_wav(audio: np.ndarray, path: Path, rate: int = SAMPLE_RATE):
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
