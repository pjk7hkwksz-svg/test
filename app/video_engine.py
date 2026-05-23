"""
Video engine: script + neural voice + animated visuals + music -> MP4.

Visual upgrades over the original generator:
  * animated multi-point "mesh" gradient background (rendered at low res and
    upscaled for a smooth, premium look)
  * soft glowing bokeh orbs that drift
  * vignette + subtle film grain for texture
  * karaoke-style captions: the spoken word is highlighted in the accent color
    and pops, already-spoken words stay white, upcoming words dim
  * title card and progress bar
  * procedural background music mixed under the voice
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import music as music_engine
from . import tts_engine
from .tts_engine import Line

WIDTH, HEIGHT = 1080, 1920
FPS = 30
LOW_W, LOW_H = 270, 480          # background render resolution (then upscaled)
PAD = 70

# ── themes ────────────────────────────────────────────────────────────────────
# each: palette (background mesh colors), accent (caption highlight), music mood
THEMES = {
    "midnight": {
        "palette": [(12, 10, 40), (30, 18, 80), (10, 30, 70), (5, 6, 24)],
        "accent": (120, 180, 255), "mood": "calm",
    },
    "sunset": {
        "palette": [(40, 12, 30), (90, 30, 40), (120, 60, 30), (30, 12, 40)],
        "accent": (255, 190, 80), "mood": "uplifting",
    },
    "neon": {
        "palette": [(20, 4, 40), (60, 8, 70), (10, 20, 80), (40, 6, 50)],
        "accent": (255, 70, 200), "mood": "epic",
    },
    "emerald": {
        "palette": [(4, 30, 28), (8, 60, 45), (6, 40, 60), (4, 20, 30)],
        "accent": (90, 240, 170), "mood": "calm",
    },
    "mono": {
        "palette": [(14, 14, 16), (32, 32, 38), (20, 20, 24), (8, 8, 10)],
        "accent": (255, 205, 90), "mood": "mysterious",
    },
}
DEFAULT_THEME = "midnight"


# ── fonts ────────────────────────────────────────────────────────────────────
def _font(size: int):
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


F_TITLE = _font(56)
F_BODY = _font(76)
F_BODY_BIG = _font(84)
F_SMALL = _font(34)


# ── precomputed assets (built once per resolution) ────────────────────────────
_LOW_GX, _LOW_GY = np.meshgrid(
    np.linspace(0, 1, LOW_W), np.linspace(0, 1, LOW_H)
)

def _make_vignette() -> np.ndarray:
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    cx, cy = WIDTH / 2, HEIGHT / 2
    d = np.sqrt(((xs - cx) / (WIDTH / 2)) ** 2 + ((ys - cy) / (HEIGHT / 2)) ** 2)
    v = 1.0 - np.clip((d - 0.6) / 0.9, 0, 1) * 0.55
    return v.astype(np.float32)[..., None]

_VIGNETTE = _make_vignette()

def _make_grain(n: int = 4) -> list[np.ndarray]:
    """Store grain as int8 to keep memory ~4 MB total instead of ~80 MB."""
    rng = np.random.default_rng(99)
    out = []
    for _ in range(n):
        g = rng.integers(-18, 18, (HEIGHT // 4, WIDTH // 4, 1), dtype=np.int8)
        img = Image.fromarray((g[..., 0].astype(np.int16) + 128).clip(0, 255).astype(np.uint8))
        img = img.resize((WIDTH, HEIGHT), Image.BILINEAR)
        out.append((np.asarray(img, dtype=np.int16)[..., None] - 128).astype(np.int8))
    return out

_GRAIN = _make_grain()


@dataclass
class Bokeh:
    cx: float; cy: float; r: float; spd: float; phase: float; col: int


def _make_bokeh(accent, n=14, seed=3):
    rng = np.random.default_rng(seed)
    return [
        Bokeh(
            cx=rng.uniform(0, 1), cy=rng.uniform(0, 1),
            r=rng.uniform(18, 60), spd=rng.uniform(0.01, 0.04),
            phase=rng.uniform(0, math.tau), col=int(rng.integers(0, 2)),
        )
        for _ in range(n)
    ]


# ── background ────────────────────────────────────────────────────────────────
def _mesh_background(t: float, palette, bokeh, accent) -> Image.Image:
    """Animated mesh gradient + bokeh, rendered low-res."""
    # four moving color control points
    pts = []
    for i, col in enumerate(palette[:4]):
        ang = t * (0.15 + 0.05 * i) + i * 1.7
        px = 0.5 + 0.42 * math.cos(ang + i)
        py = 0.5 + 0.42 * math.sin(ang * 0.8 + i * 1.3)
        pts.append((px, py, np.array(col, dtype=np.float32)))

    acc = np.zeros((LOW_H, LOW_W, 3), dtype=np.float32)
    wsum = np.zeros((LOW_H, LOW_W, 1), dtype=np.float32)
    for px, py, col in pts:
        d2 = (_LOW_GX - px) ** 2 + (_LOW_GY - py) ** 2
        w = 1.0 / (d2 * 18 + 0.04)
        acc += w[..., None] * col
        wsum += w[..., None]
    field = acc / np.maximum(wsum, 1e-6)

    img = Image.fromarray(field.clip(0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    for b in bokeh:
        bx = (b.cx + math.cos(t * b.spd * 6 + b.phase) * 0.05) * LOW_W
        by = ((b.cy - t * b.spd * 1.2) % 1.0) * LOW_H
        col = accent if b.col == 0 else (255, 255, 255)
        a = 26 if b.col else 18
        draw.ellipse([bx - b.r, by - b.r, bx + b.r, by + b.r], fill=(*col, a))

    img = img.resize((WIDTH, HEIGHT), Image.BILINEAR)
    return img


# ── caption layout with per-word highlighting ──────────────────────────────────
def _layout_words(draw, words, font, max_w):
    """Return list of rows; each row is list of (word, x, w). Centered later."""
    space = draw.textlength(" ", font=font)
    rows, cur, cur_w = [], [], 0.0
    for w in words:
        ww = draw.textlength(w, font=font)
        add = ww if not cur else cur_w + space + ww
        if add > max_w and cur:
            rows.append((cur, cur_w))
            cur, cur_w = [w], ww
        else:
            cur.append(w)
            cur_w = add
    if cur:
        rows.append((cur, cur_w))
    return rows, space


def _draw_caption(img, line: Line, t_in_line: float, accent):
    """Karaoke caption for the active line, drawn crisp at full res."""
    draw = ImageDraw.Draw(img, "RGBA")
    words = line.text.split()
    if not words:
        return
    # which word is "spoken" now (weighted by length)
    weights = [len(w) + 1 for w in words]
    total_w = sum(weights)
    prog = max(0.0, min(1.0, t_in_line / max(line.duration, 0.1)))
    target = prog * total_w
    active = 0
    cum = 0
    for i, wt in enumerate(weights):
        cum += wt
        if target <= cum:
            active = i
            break
    else:
        active = len(words) - 1

    font = F_BODY
    max_w = WIDTH - 2 * PAD - 60
    rows, space = _layout_words(draw, words, font, max_w)

    line_h = font.size + 22
    block_h = line_h * len(rows)
    y = HEIGHT // 2 - block_h // 2

    # entry pop (first 0.18s)
    pop = min(1.0, t_in_line / 0.18)
    base_alpha = int(255 * pop)

    # soft card behind text
    card_pad = 46
    draw.rounded_rectangle(
        [PAD - 6, y - card_pad, WIDTH - PAD + 6, y + block_h + card_pad - 10],
        radius=34, fill=(0, 0, 0, int(120 * pop)),
    )

    idx = 0
    for row_words, row_w in rows:
        x = (WIDTH - row_w) / 2
        for w in row_words:
            ww = draw.textlength(w, font=font)
            if idx < active:
                color = (255, 255, 255, base_alpha)          # spoken
            elif idx == active:
                color = (*accent, base_alpha)                  # current
            else:
                color = (170, 175, 190, int(base_alpha * 0.8)) # upcoming
            # shadow / stroke for readability
            draw.text((x, y), w, font=font, fill=color,
                      stroke_width=5, stroke_fill=(0, 0, 0, base_alpha))
            if idx == active:
                # subtle underline glow on the active word
                uy = y + font.size + 6
                draw.line([x, uy, x + ww, uy], fill=(*accent, base_alpha), width=5)
            x += ww + space
            idx += 1
        y += line_h


def _fit_title_font(draw, title, max_w):
    for size in (56, 50, 46, 42, 38, 34):
        f = _font(size)
        if draw.textlength(title, font=f) <= max_w:
            return f
    return _font(34)


def _draw_chrome(img, title, frame, total, accent, n_lines, cur_line):
    draw = ImageDraw.Draw(img, "RGBA")
    # title (auto-shrunk to fit within margins)
    tfont = _fit_title_font(draw, title, WIDTH - 2 * PAD)
    tw = draw.textlength(title, font=tfont)
    tx = (WIDTH - tw) / 2
    draw.text((tx, 150), title, font=tfont, fill=(*accent, 235),
              stroke_width=4, stroke_fill=(0, 0, 0, 180))
    draw.rectangle([WIDTH / 2 - 150, 232, WIDTH / 2 + 150, 236],
                   fill=(*accent, 160))
    # counter
    if cur_line >= 0:
        label = f"{min(cur_line + 1, n_lines)} / {n_lines}"
        lw = draw.textlength(label, font=F_SMALL)
        draw.text(((WIDTH - lw) / 2, HEIGHT - 150), label,
                  font=F_SMALL, fill=(255, 255, 255, 120))
    # progress bar
    prog = frame / max(total - 1, 1)
    bx0, bx1, by = PAD, WIDTH - PAD, HEIGHT - 96
    draw.rounded_rectangle([bx0, by, bx1, by + 9], radius=5,
                           fill=(255, 255, 255, 30))
    fill = int((bx1 - bx0) * prog)
    if fill > 0:
        draw.rounded_rectangle([bx0, by, bx0 + fill, by + 9], radius=5,
                               fill=(*accent, 220))


# ── frame composition ──────────────────────────────────────────────────────────
def _compose(frame, total, t, title, lines, theme, bokeh):
    palette = theme["palette"]
    accent = theme["accent"]

    bg = _mesh_background(t, palette, bokeh, accent)
    arr = np.asarray(bg, dtype=np.float32)
    arr *= _VIGNETTE
    arr += _GRAIN[frame % len(_GRAIN)].astype(np.float32) * 0.35
    arr = arr.clip(0, 255).astype(np.uint8)
    img = Image.fromarray(arr, "RGB").convert("RGBA")

    # active line
    cur = -1
    for i, ln in enumerate(lines):
        if t >= ln.start:
            cur = i
    if cur >= 0 and t < lines[cur].start + lines[cur].duration + 0.25:
        _draw_caption(img, lines[cur], t - lines[cur].start, accent)

    _draw_chrome(img, title, frame, total, accent, len(lines), cur)
    return np.asarray(img.convert("RGB"))


# ── audio assembly ──────────────────────────────────────────────────────────────
def _mix_audio(voice: np.ndarray, duration: float, mood: str,
               music_vol: float, tmp: Path) -> Path:
    bed = music_engine.generate(duration, mood=mood)
    # length-match
    target = int(duration * tts_engine.SAMPLE_RATE) + tts_engine.SAMPLE_RATE
    v = np.zeros(target, dtype=np.float32)
    v[:min(len(voice), target)] = voice[:target]
    m = np.zeros(target, dtype=np.float32)
    m[:min(len(bed), target)] = bed[:target]
    mix = v + m * music_vol
    peak = np.max(np.abs(mix)) + 1e-9
    if peak > 0.99:
        mix = mix / peak * 0.99
    out = tmp / "audio.wav"
    tts_engine.write_wav(mix, out)
    return out


# ── public entry point ──────────────────────────────────────────────────────────
def generate_video(
    title: str,
    lines: list[str],
    out_path: Path,
    voice: str = "ryan",
    theme: str = DEFAULT_THEME,
    speed: float = 1.0,
    music_vol: float = 0.16,
    progress=None,
) -> Path:
    """Render a finished MP4. `progress(pct, msg)` is an optional callback."""
    theme_cfg = THEMES.get(theme, THEMES[DEFAULT_THEME])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def report(p, m):
        if progress:
            progress(p, m)

    report(5, "Synthesizing voice")
    timed, voice_audio = tts_engine.render_lines(lines, voice=voice, speed=speed)
    duration = len(voice_audio) / tts_engine.SAMPLE_RATE

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report(20, "Composing music")
        audio_path = _mix_audio(voice_audio, duration, theme_cfg["mood"],
                                music_vol, tmp)

        total = int(duration * FPS) + FPS
        bokeh = _make_bokeh(theme_cfg["accent"])
        report(25, "Rendering frames")

        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{WIDTH}x{HEIGHT}", "-pix_fmt", "rgb24", "-r", str(FPS),
            "-i", "pipe:0",
            "-i", str(audio_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-maxrate", "6M", "-bufsize", "12M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart",
            str(out_path),
        ]
        errf = tempfile.TemporaryFile()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=errf)
        try:
            for f in range(total):
                t = f / FPS
                frame = _compose(f, total, t, title, timed, theme_cfg, bokeh)
                proc.stdin.write(frame.tobytes())
                if f % 30 == 0:
                    report(25 + int(70 * f / total), "Rendering frames")
        except BrokenPipeError:
            pass
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            errf.seek(0)
            raise RuntimeError(errf.read().decode("utf-8", "replace")[-1500:])

    report(100, "Done")
    return out_path
