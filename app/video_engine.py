"""
Video engine: script + neural voice + animated visuals + music -> MP4.

Optimisations over v1:
  * Caption word layout is pre-computed per Line before the render loop
    (was recomputed every frame — ~900x redundant for a 30s video)
  * Title font size fitted once per video, not once per frame
  * Grain arrays pre-cast to float32 at import time
  * Smoother exponential vignette (was linear, looked harsh)
  * Bokeh seed derived from video content hash for variety
  * Title fades to a dimmer hold after the opening seconds
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import music as music_engine
from . import tts_engine
from .tts_engine import Line

WIDTH, HEIGHT = 1080, 1920
FPS = 30
LOW_W, LOW_H = 270, 480
PAD = 70

# ── themes ────────────────────────────────────────────────────────────────────
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
    "crimson": {
        "palette": [(30, 4, 8), (70, 10, 18), (50, 8, 30), (20, 4, 12)],
        "accent": (255, 100, 120), "mood": "epic",
    },
    "ocean": {
        "palette": [(2, 18, 50), (4, 40, 90), (6, 60, 80), (2, 24, 60)],
        "accent": (60, 220, 240), "mood": "calm",
    },
}
DEFAULT_THEME = "midnight"


# ── fonts ────────────────────────────────────────────────────────────────────
_FONT_CACHE: dict[int, ImageFont.FreeTypeFont] = {}

def _font(size: int) -> ImageFont.FreeTypeFont:
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]:
        if Path(p).exists():
            try:
                f = ImageFont.truetype(p, size)
                _FONT_CACHE[size] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


F_TITLE = _font(56)
F_BODY  = _font(76)
F_SMALL = _font(34)

# dummy image + draw used solely for text measurements (avoids creating per-frame)
_MEASURE_IMG  = Image.new("RGBA", (4, 4))
_MEASURE_DRAW = ImageDraw.Draw(_MEASURE_IMG)


# ── precomputed assets ────────────────────────────────────────────────────────
_LOW_GX, _LOW_GY = np.meshgrid(
    np.linspace(0, 1, LOW_W), np.linspace(0, 1, LOW_H)
)


def _make_vignette() -> np.ndarray:
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    cx, cy = WIDTH / 2, HEIGHT / 2
    d = np.sqrt(((xs - cx) / (WIDTH / 2)) ** 2 + ((ys - cy) / (HEIGHT / 2)) ** 2)
    # smooth exponential falloff: bright centre, gradual edge darkening
    v = np.exp(-d ** 2.2 * 0.9).astype(np.float32)
    v = np.clip(0.45 + v * 0.55, 0, 1)  # min brightness ~45%
    return v[..., None]


_VIGNETTE = _make_vignette()


def _make_grain(n: int = 6) -> list[np.ndarray]:
    """Pre-cast grain to scaled float32 so the render loop pays zero cast cost."""
    rng = np.random.default_rng(42)
    out = []
    for _ in range(n):
        g = rng.integers(-20, 20, (HEIGHT // 4, WIDTH // 4), dtype=np.int8)
        img = Image.fromarray(
            (g.astype(np.int16) + 128).clip(0, 255).astype(np.uint8)
        )
        img = img.resize((WIDTH, HEIGHT), Image.BILINEAR)
        # pre-scale: multiply by 0.30, subtract 128*0.30 so 0 stays 0
        arr = (np.asarray(img, dtype=np.float32) - 128.0) * 0.30
        out.append(arr[..., None])
    return out


_GRAIN = _make_grain()


@dataclass
class Bokeh:
    cx: float; cy: float; r: float; spd: float; phase: float; col: int


def _make_bokeh(accent, content_seed: int = 0, n: int = 16):
    rng = np.random.default_rng(content_seed % (2**31))
    bk = []
    for i in range(n):
        depth = rng.uniform(0.3, 1.0)   # simulated depth: smaller/dimmer = farther
        bk.append(Bokeh(
            cx=rng.uniform(0, 1),
            cy=rng.uniform(0, 1),
            r=rng.uniform(12, 55) * depth,
            spd=rng.uniform(0.008, 0.035) * (1.0 - depth * 0.4),
            phase=rng.uniform(0, math.tau),
            col=int(rng.integers(0, 3)),   # 0=accent, 1=white, 2=dim white
        ))
    return bk


# ── pre-computed caption layout (one per Line, before the render loop) ────────
@dataclass
class LineLayout:
    line: Line
    rows: list                  # [(words_list, row_width_px)]
    space: float                # pixel width of a space in F_BODY
    line_h: int
    block_h: int
    weights: list[int]          # word timing weights
    total_w: int


def _precompute_layouts(lines: list[Line]) -> list[LineLayout]:
    """Do all word-wrap math once instead of once per frame."""
    max_w = WIDTH - 2 * PAD - 60
    out = []
    for ln in lines:
        words = ln.text.split()
        if not words:
            out.append(LineLayout(ln, [], 0, 0, 0, [], 0))
            continue
        space = _MEASURE_DRAW.textlength(" ", font=F_BODY)
        rows, cur, cur_w = [], [], 0.0
        for w in words:
            ww = _MEASURE_DRAW.textlength(w, font=F_BODY)
            add = ww if not cur else cur_w + space + ww
            if add > max_w and cur:
                rows.append((cur, cur_w))
                cur, cur_w = [w], ww
            else:
                cur.append(w)
                cur_w = add
        if cur:
            rows.append((cur, cur_w))
        line_h = F_BODY.size + 22
        block_h = line_h * len(rows)
        weights = [len(w) + 1 for w in words]
        out.append(LineLayout(ln, rows, space, line_h, block_h, weights, sum(weights)))
    return out


def _fit_title_font(title: str) -> ImageFont.FreeTypeFont:
    """Fit title into the canvas width. Called once per video."""
    max_w = WIDTH - 2 * PAD
    for size in (56, 50, 46, 42, 38, 34, 28):
        f = _font(size)
        if _MEASURE_DRAW.textlength(title, font=f) <= max_w:
            return f
    return _font(28)


# ── background ────────────────────────────────────────────────────────────────
def _mesh_background(t: float, palette, bokeh, accent) -> Image.Image:
    pts = []
    for i, col in enumerate(palette[:4]):
        ang = t * (0.13 + 0.04 * i) + i * 1.9
        px = 0.5 + 0.44 * math.cos(ang + i * 0.7)
        py = 0.5 + 0.44 * math.sin(ang * 0.75 + i * 1.4)
        pts.append((px, py, np.array(col, dtype=np.float32)))

    acc  = np.zeros((LOW_H, LOW_W, 3), dtype=np.float32)
    wsum = np.zeros((LOW_H, LOW_W, 1), dtype=np.float32)
    for px, py, col in pts:
        d2 = (_LOW_GX - px) ** 2 + (_LOW_GY - py) ** 2
        w  = 1.0 / (d2 * 20 + 0.03)
        acc  += w[..., None] * col
        wsum += w[..., None]
    field = acc / np.maximum(wsum, 1e-6)

    img  = Image.fromarray(field.clip(0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    for b in bokeh:
        bx = (b.cx + math.cos(t * b.spd * 5 + b.phase) * 0.06) * LOW_W
        by = ((b.cy - t * b.spd) % 1.0) * LOW_H
        if b.col == 0:
            col, a = accent, 30
        elif b.col == 1:
            col, a = (255, 255, 255), 20
        else:
            col, a = (200, 200, 220), 14
        draw.ellipse([bx - b.r, by - b.r, bx + b.r, by + b.r], fill=(*col, a))

    return img.resize((WIDTH, HEIGHT), Image.LANCZOS)  # LANCZOS for sharpness


# ── caption rendering ─────────────────────────────────────────────────────────
def _draw_caption(img, layout: LineLayout, t_in_line: float, accent):
    if not layout.rows:
        return
    draw = ImageDraw.Draw(img, "RGBA")
    words  = layout.line.text.split()
    prog   = max(0.0, min(1.0, t_in_line / max(layout.line.duration, 0.1)))
    target = prog * layout.total_w
    active, cum = 0, 0
    for i, wt in enumerate(layout.weights):
        cum += wt
        if target <= cum:
            active = i
            break
    else:
        active = len(words) - 1

    # entry fade (first 0.15s)
    pop        = min(1.0, t_in_line / 0.15)
    base_alpha = int(255 * pop)

    y = HEIGHT // 2 - layout.block_h // 2
    card_pad = 48
    # card shadow
    draw.rounded_rectangle(
        [PAD - 8, y - card_pad, WIDTH - PAD + 8, y + layout.block_h + card_pad - 8],
        radius=36, fill=(0, 0, 0, int(130 * pop)),
    )

    idx = 0
    for row_words, row_w in layout.rows:
        x = (WIDTH - row_w) / 2
        for w in row_words:
            ww = draw.textlength(w, font=F_BODY)
            if idx < active:
                color = (255, 255, 255, base_alpha)
            elif idx == active:
                color = (*accent, base_alpha)
            else:
                color = (160, 165, 180, int(base_alpha * 0.75))
            # soft shadow via offset text (cleaner than stroke_fill)
            draw.text((x + 2, y + 2), w, font=F_BODY, fill=(0, 0, 0, int(base_alpha * 0.6)))
            draw.text((x, y), w, font=F_BODY, fill=color)
            if idx == active:
                uy = y + F_BODY.size + 4
                # two-pass underline for soft glow look
                draw.line([x, uy + 2, x + ww, uy + 2], fill=(*accent, int(base_alpha * 0.3)), width=7)
                draw.line([x, uy, x + ww, uy], fill=(*accent, base_alpha), width=3)
            x += ww + layout.space
            idx += 1
        y += layout.line_h


def _draw_chrome(img, title, title_font, frame, total, accent, n_lines, cur_line, t):
    draw = ImageDraw.Draw(img, "RGBA")

    # title fades in over 0.4s, holds at 85%, dims to 50% after 2s
    title_alpha = min(235, int(255 * min(1.0, t / 0.4)))
    if t > 2.0:
        hold = max(0.5, 1.0 - (t - 2.0) * 0.08)
        title_alpha = int(title_alpha * hold)

    tw = draw.textlength(title, font=title_font)
    tx = (WIDTH - tw) / 2
    draw.text((tx + 2, 152), title, font=title_font, fill=(0, 0, 0, int(title_alpha * 0.55)))
    draw.text((tx, 150), title, font=title_font, fill=(*accent, title_alpha))
    # accent line under title
    line_a = int(title_alpha * 0.65)
    draw.rectangle([WIDTH / 2 - 160, 234, WIDTH / 2 + 160, 238], fill=(*accent, line_a))

    # line counter
    if cur_line >= 0:
        label = f"{min(cur_line + 1, n_lines)} / {n_lines}"
        lw    = draw.textlength(label, font=F_SMALL)
        draw.text(((WIDTH - lw) / 2, HEIGHT - 150), label,
                  font=F_SMALL, fill=(255, 255, 255, 100))

    # progress bar — two-layer for depth
    prog  = frame / max(total - 1, 1)
    bx0, bx1, by = PAD, WIDTH - PAD, HEIGHT - 96
    bar_w = bx1 - bx0
    draw.rounded_rectangle([bx0, by, bx1, by + 10], radius=5, fill=(255, 255, 255, 22))
    filled = int(bar_w * prog)
    if filled > 2:
        draw.rounded_rectangle([bx0, by, bx0 + filled, by + 10], radius=5,
                               fill=(*accent, 230))
        # bright pip at leading edge
        draw.rounded_rectangle([bx0 + filled - 4, by - 1,
                                 bx0 + filled + 4, by + 12], radius=3,
                               fill=(255, 255, 255, 200))


# ── frame composition ──────────────────────────────────────────────────────────
def _compose(frame, total, t, title, title_font, layouts, theme, bokeh):
    accent = theme["accent"]

    bg  = _mesh_background(t, theme["palette"], bokeh, accent)
    arr = np.asarray(bg, dtype=np.float32)
    arr *= _VIGNETTE
    arr += _GRAIN[frame % len(_GRAIN)]
    arr  = arr.clip(0, 255).astype(np.uint8)
    img  = Image.fromarray(arr, "RGB").convert("RGBA")

    # find active caption
    cur = -1
    for i, lo in enumerate(layouts):
        if t >= lo.line.start:
            cur = i
    if cur >= 0:
        lo = layouts[cur]
        if t < lo.line.start + lo.line.duration + 0.3:
            _draw_caption(img, lo, t - lo.line.start, accent)

    _draw_chrome(img, title, title_font, frame, total, accent, len(layouts), cur, t)
    return np.asarray(img.convert("RGB"))


# ── audio assembly ─────────────────────────────────────────────────────────────
def _mix_audio(voice: np.ndarray, duration: float, mood: str,
               music_vol: float, content_seed: int, tmp: Path) -> Path:
    bed = music_engine.generate(duration, mood=mood, seed=content_seed)
    rate = tts_engine.SAMPLE_RATE
    target = int(duration * rate) + rate
    v = np.zeros(target, dtype=np.float32)
    v[:min(len(voice), target)] = voice[:target]
    m = np.zeros(target, dtype=np.float32)
    m[:min(len(bed), target)] = bed[:target]
    mix = v + m * music_vol
    # soft limiter instead of hard clip
    peak = np.max(np.abs(mix)) + 1e-9
    if peak > 0.95:
        mix = np.tanh(mix / peak * 1.2) * 0.95
    out = tmp / "audio.wav"
    tts_engine.write_wav(mix, out)
    return out


# ── public entry point ─────────────────────────────────────────────────────────
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
    theme_cfg  = THEMES.get(theme, THEMES[DEFAULT_THEME])
    out_path   = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content_seed = int(hashlib.md5(title.encode()).hexdigest()[:8], 16)

    def report(p, m):
        if progress:
            progress(p, m)

    report(5, "Synthesizing voice")
    timed, voice_audio = tts_engine.render_lines(lines, voice=voice, speed=speed,
                                                   content_seed=content_seed)
    duration = len(voice_audio) / tts_engine.SAMPLE_RATE

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report(20, "Composing music")
        audio_path = _mix_audio(voice_audio, duration, theme_cfg["mood"],
                                music_vol, content_seed, tmp)

        total       = int(duration * FPS) + FPS
        bokeh       = _make_bokeh(theme_cfg["accent"], content_seed)
        title_font  = _fit_title_font(title)
        layouts     = _precompute_layouts(timed)    # ← compute once, not 900x
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
                t     = f / FPS
                frame = _compose(f, total, t, title, title_font, layouts,
                                 theme_cfg, bokeh)
                proc.stdin.write(frame.tobytes())
                if f % 30 == 0:
                    report(25 + int(70 * f / total), "Rendering frames")
        except BrokenPipeError:
            pass
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            errf.seek(0)
            raise RuntimeError(errf.read().decode("utf-8", "replace")[-2000:])

    report(100, "Done")
    return out_path
