#!/usr/bin/env python3
"""
Faceless AI TikTok Video Generator
====================================
Generates 3 ready-to-post TikTok MP4 videos with:
  - AI voiceover via espeak-ng (offline, no API key needed)
  - Animated gradient backgrounds with floating particles
  - Bold centered captions that sync with audio
  - Progress bar

Requirements:
  pip install pillow numpy
  System: ffmpeg espeak-ng
    Ubuntu/Debian : sudo apt install ffmpeg espeak-ng
    macOS         : brew install ffmpeg espeak
    Windows       : install ffmpeg + eSpeak from their respective sites

Usage:
  python generate_tiktoks.py

Output: output/  directory with 3 MP4 files (1080x1920, ~30-50s each)
"""

import asyncio
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

# ── Auto-install Python deps ───────────────────────────────────────────────────

def _pip(*pkgs):
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", *pkgs],
        stdout=subprocess.DEVNULL,
    )


try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installing pillow…")
    _pip("pillow")
    from PIL import Image, ImageDraw, ImageFont

try:
    import numpy as np
except ImportError:
    print("Installing numpy…")
    _pip("numpy")
    import numpy as np

# ── Constants ──────────────────────────────────────────────────────────────────

WIDTH    = 1080
HEIGHT   = 1920
FPS      = 30
PAD      = 80          # horizontal margin
LEAD_IN  = FPS // 2   # 0.5 s title-only before first line
TAIL     = FPS         # 1 s hold after last line
FADE     = 8           # frames for text fade-in / out

OUT_DIR  = Path("output")
TMP_DIR  = Path(".tmp_tiktok")
OUT_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)

# ── Video configs ──────────────────────────────────────────────────────────────

VIDEOS = [
    {
        "slug":   "psychology_facts",
        "title":  "Psychology Facts",
        # espeak-ng voice string: language+variant  (m1-m7 male, f1-f4 female)
        "voice":  "en-us+m3",
        "speed":  145,    # words per minute
        "pitch":  42,     # 0-99
        "bg_top": (8,   4,  22),
        "bg_bot": (22,  8,  44),
        "accent": (160,  60, 240),
        "lines": [
            "Your brain processes rejection",
            "the same way it processes physical pain.",
            "People who laugh more are better at tolerating pain.",
            "Your mind can sense someone staring at you",
            "even while you are asleep.",
            "The average person walks past 36 murderers in their lifetime.",
            "Memories are reconstructed every time you recall them.",
            "Your oldest memories may be completely fabricated.",
            "Follow for more mind-blowing psychology facts.",
        ],
    },
    {
        "slug":   "millionaire_habits",
        "title":  "Morning Habits of Millionaires",
        "voice":  "en-us+f3",
        "speed":  150,
        "pitch":  55,
        "bg_top": (6,   6,   6),
        "bg_bot": (20,  14,  2),
        "accent": (255, 200,   0),
        "lines": [
            "The top 1% wake up before 5 AM every single day.",
            "They exercise for at least 30 minutes each morning.",
            "They read for 20 minutes before touching their phone.",
            "They write down 3 goals before the day starts.",
            "They eat a high-protein breakfast. Zero sugar.",
            "They meditate for 10 minutes to clear their mind.",
            "Start just one of these habits tomorrow.",
            "And watch your life slowly transform.",
            "Save this so you do not forget.",
        ],
    },
    {
        "slug":   "body_facts",
        "title":  "Your Body Is Incredible",
        "voice":  "en-us+m5",
        "speed":  140,
        "pitch":  36,
        "bg_top": (0,  10,  26),
        "bg_bot": (0,  24,  52),
        "accent": (0,  180, 255),
        "lines": [
            "Your stomach gets a brand new lining every 3 to 5 days.",
            "Your bones are stronger than steel, pound for pound.",
            "Your eyes can distinguish 10 million different colors.",
            "Your heart beats 100,000 times every single day.",
            "You produce enough saliva in your lifetime",
            "to fill two swimming pools.",
            "Your body generates enough heat in 30 minutes",
            "to boil half a gallon of water.",
            "You are literally a walking miracle.",
            "Follow for more facts that will blow your mind.",
        ],
    },
]

# ── Font loading ───────────────────────────────────────────────────────────────

def _load_font(size):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


FONT_TITLE = _load_font(54)
FONT_BODY  = _load_font(72)
FONT_SMALL = _load_font(36)

# ── Particle definitions (deterministic) ───────────────────────────────────────

_rng = random.Random(1337)
PARTICLES = [
    {
        "x":     _rng.randint(40, WIDTH - 40),
        "y0":    _rng.uniform(-HEIGHT, 0),
        "speed": _rng.uniform(20, 70),
        "r":     _rng.randint(2, 7),
        "alpha": _rng.randint(25, 90),
    }
    for _ in range(60)
]

# ── Drawing helpers ────────────────────────────────────────────────────────────

def _tw(font, text):
    bb = font.getbbox(text)
    return bb[2] - bb[0]


def _th(font, text="Ag"):
    bb = font.getbbox(text)
    return bb[3] - bb[1]


def _word_wrap(font, text, max_w):
    words = text.split()
    lines = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        if _tw(font, trial) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _draw_centered_text(draw, text, font, cy, color, max_w, gap=14):
    lines  = _word_wrap(font, text, max_w)
    lh     = _th(font) + gap
    total  = lh * len(lines) - gap
    y      = cy - total // 2
    for line in lines:
        lw = _tw(font, line)
        x  = (WIDTH - lw) // 2
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 120))
        draw.text((x,     y    ), line, font=font, fill=color)
        y += lh

# ── Background builder (numpy, vectorised) ─────────────────────────────────────

def _make_bg(frame, total, top, bot):
    t     = frame / max(total - 1, 1)
    pulse = 0.04 * math.sin(t * math.pi * 8)
    ys    = np.clip(np.linspace(0, 1, HEIGHT)[:, None] + pulse, 0, 1)
    r     = np.uint8(top[0] + (bot[0] - top[0]) * ys)
    g     = np.uint8(top[1] + (bot[1] - top[1]) * ys)
    b     = np.uint8(top[2] + (bot[2] - top[2]) * ys)
    return np.repeat(np.dstack([r, g, b]), WIDTH, axis=1)

# ── Single frame renderer ──────────────────────────────────────────────────────

def _render(frame, total, config, line_idx, line_alpha):
    accent  = config["accent"]
    t_sec   = frame / FPS

    bg_arr  = _make_bg(frame, total, config["bg_top"], config["bg_bot"])
    img     = Image.fromarray(bg_arr).convert("RGBA")
    draw    = ImageDraw.Draw(img, "RGBA")

    # particles
    for p in PARTICLES:
        y = int((p["y0"] + t_sec * p["speed"]) % HEIGHT)
        r = p["r"]
        draw.ellipse([p["x"] - r, y - r, p["x"] + r, y + r],
                     fill=(*accent, p["alpha"]))

    # title
    title_y = 180
    tw      = _tw(FONT_TITLE, config["title"])
    tx      = (WIDTH - tw) // 2
    draw.text((tx + 2, title_y + 2), config["title"],
              font=FONT_TITLE, fill=(0, 0, 0, 100))
    draw.text((tx,     title_y    ), config["title"],
              font=FONT_TITLE, fill=(*accent, 230))

    # divider
    div_y = title_y + _th(FONT_TITLE) + 20
    draw.rectangle([WIDTH // 2 - 160, div_y,
                    WIDTH // 2 + 160, div_y + 4],
                   fill=(*accent, 140))

    # body text card
    if 0 <= line_idx < len(config["lines"]):
        a    = int(255 * line_alpha)
        cy   = HEIGHT // 2
        cx0  = PAD
        cx1  = WIDTH - PAD
        draw.rounded_rectangle([cx0, cy - 220, cx1, cy + 220],
                                radius=28, fill=(0, 0, 0, int(0.35 * a)))
        _draw_centered_text(draw, config["lines"][line_idx],
                            FONT_BODY, cy, (255, 255, 255, a),
                            cx1 - cx0 - 60)

    # counter
    n     = len(config["lines"])
    shown = max(0, min(line_idx + 1, n))
    label = f"{shown} / {n}"
    lw    = _tw(FONT_SMALL, label)
    draw.text(((WIDTH - lw) // 2, HEIGHT - 140), label,
              font=FONT_SMALL, fill=(255, 255, 255, 110))

    # progress bar
    prog   = frame / max(total - 1, 1)
    bar_y  = HEIGHT - 72
    bx0    = PAD
    bx1    = WIDTH - PAD
    filled = int((bx1 - bx0) * prog)
    draw.rounded_rectangle([bx0, bar_y, bx1, bar_y + 8],
                            radius=4, fill=(255, 255, 255, 28))
    if filled > 0:
        draw.rounded_rectangle([bx0, bar_y, bx0 + filled, bar_y + 8],
                                radius=4, fill=(*accent, 210))

    return np.array(img.convert("RGB"))

# ── TTS (espeak-ng, fully offline) ────────────────────────────────────────────

def _tts(text, voice, speed, pitch, path):
    """Render text to a WAV file using espeak-ng."""
    wav_path = Path(str(path).rsplit(".", 1)[0] + ".wav")
    subprocess.run(
        [
            "espeak-ng",
            "-v", voice,
            "-s", str(speed),
            "-p", str(pitch),
            "-a", "180",      # amplitude (louder than default 100)
            "-w", str(wav_path),
            text,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # convert WAV → MP3 via ffmpeg for consistent handling
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-q:a", "4", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wav_path.unlink(missing_ok=True)

# ── Audio duration via ffprobe ─────────────────────────────────────────────────

def _duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    try:
        for s in json.loads(r.stdout).get("streams", []):
            if "duration" in s:
                return float(s["duration"])
    except Exception:
        pass
    return 30.0

# ── Pipe frames → ffmpeg ───────────────────────────────────────────────────────

def _encode(frame_gen, audio_path, out_path):
    cmd = [
        "ffmpeg", "-y",
        "-f",       "rawvideo", "-vcodec", "rawvideo",
        "-s",       f"{WIDTH}x{HEIGHT}",
        "-pix_fmt", "rgb24", "-r", str(FPS),
        "-i",       "pipe:0",
        "-i",       str(audio_path),
        "-c:v",     "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a",     "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        str(out_path),
    ]
    import tempfile, io
    stderr_buf = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=stderr_buf)
    try:
        for arr in frame_gen:
            proc.stdin.write(arr.tobytes())
    except BrokenPipeError:
        pass  # ffmpeg closed stdin early (e.g. -shortest triggered); that's fine
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        stderr_buf.seek(0)
        err = stderr_buf.read().decode(errors="replace")[-2000:]
        raise RuntimeError(f"ffmpeg exited {proc.returncode}\n{err}")
    stderr_buf.close()

# ── Per-line frame ranges ──────────────────────────────────────────────────────

def _line_ranges(lines, body_frames):
    weights  = [max(len(l), 10) for l in lines]
    total_w  = sum(weights)
    ranges   = []
    cum      = 0
    for w in weights:
        s = int(cum / total_w * body_frames)
        cum += w
        e = int(cum / total_w * body_frames)
        ranges.append((s + LEAD_IN, e + LEAD_IN))
    return ranges

# ── Full video pipeline ────────────────────────────────────────────────────────

async def _generate(config):
    slug = config["slug"]
    print(f"\n{'─' * 54}")
    print(f"  {config['title']}")
    print(f"{'─' * 54}")

    # 1. TTS
    audio = TMP_DIR / f"{slug}.mp3"
    print(f"  [1/3] TTS voice: {config['voice']} …", end=" ", flush=True)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _tts,
        " ".join(config["lines"]),
        config["voice"],
        config["speed"],
        config["pitch"],
        audio,
    )
    print("done")

    # 2. Measure
    dur          = _duration(audio)
    body_frames  = int(dur * FPS)
    total_frames = LEAD_IN + body_frames + TAIL
    print(f"  [2/3] Rendering {total_frames} frames ({dur:.1f}s audio) …")

    ranges = _line_ranges(config["lines"], body_frames)

    # 3. Render + encode
    def frames():
        for fi in range(total_frames):
            # find active line
            li = 0
            for i, (s, _) in enumerate(ranges):
                if fi >= s:
                    li = i

            s, e = ranges[li]
            span = max(e - s, 1)
            pos  = fi - s

            if fi < LEAD_IN:
                li, alpha = -1, 0.0
            elif pos < FADE:
                alpha = pos / FADE
            elif pos > span - FADE:
                alpha = max(0.0, (span - pos) / FADE)
            else:
                alpha = 1.0

            yield _render(fi, total_frames, config, li, alpha)

            if fi % (FPS * 3) == 0:
                print(f"    {fi / total_frames * 100:5.1f}%", end="\r", flush=True)

    out = OUT_DIR / f"{slug}.mp4"
    _encode(frames(), audio, out)
    mb = out.stat().st_size / 1024 / 1024
    print(f"  [3/3] Saved → {out}  ({mb:.1f} MB)        ")
    return out

# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    print("\n" + "=" * 54)
    print("  Faceless AI TikTok Generator")
    print("=" * 54)

    # verify system tools
    missing = []
    for tool in ("ffmpeg", "espeak-ng"):
        if not shutil.which(tool):
            missing.append(tool)
    if missing:
        print(f"\nERROR: missing system tools: {', '.join(missing)}")
        print("  Ubuntu/Debian : sudo apt install ffmpeg espeak-ng")
        print("  macOS         : brew install ffmpeg espeak")
        print("  Windows       : install ffmpeg + eSpeak NG from their sites")
        sys.exit(1)

    results = []
    for cfg in VIDEOS:
        path = await _generate(cfg)
        results.append(path)

    print("\n" + "=" * 54)
    print("  All 3 videos ready to post!")
    print("=" * 54)
    for p in results:
        mb = p.stat().st_size / 1024 / 1024
        print(f"  {p}  ({mb:.1f} MB)")
    print()


if __name__ == "__main__":
    asyncio.run(main())
