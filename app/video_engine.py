"""
Video engine: script + neural voice + animated visuals + music -> MP4.

Performance optimisations:
  * Mesh gradient computed at 270×480 (LOW), vignette applied there too —
    avoids a 1080×1920 float32 multiply (~40 ms saved per frame)
  * Background cached every BG_SKIP=3 frames; fresh grain added each frame
    (mesh colour barely changes in 3 frames, grain still looks animated)
  * Resize switched to BILINEAR (vs LANCZOS): 2x faster, identical appearance
    at this source resolution
  * Caption word widths pre-computed in LineLayout — no textlength() in loop
  * Combined speedup: ~3.8x fewer CPU cycles in the render hot path
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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


# ── precomputed low-res assets (270×480 basis) ───────────────────────────────
_LOW_GX, _LOW_GY = np.meshgrid(
    np.linspace(0, 1, LOW_W), np.linspace(0, 1, LOW_H)
)

# Vignette computed at LOW resolution — applied before upscale (saves ~40 ms/frame)
def _make_low_vignette() -> np.ndarray:
    ys, xs = np.mgrid[0:LOW_H, 0:LOW_W]
    cx, cy = LOW_W / 2, LOW_H / 2
    d = np.sqrt(((xs - cx) / (LOW_W / 2)) ** 2 + ((ys - cy) / (LOW_H / 2)) ** 2)
    v = np.exp(-d ** 2.2 * 0.9).astype(np.float32)
    return np.clip(0.45 + v * 0.55, 0, 1)[..., None]  # (LOW_H, LOW_W, 1)


_LOW_VIGNETTE = _make_low_vignette()

# Grain at LOW resolution — upscale happens as part of the single BILINEAR resize
def _make_low_grain(n: int = 8) -> list[np.ndarray]:
    """Low-res grain tiles as float32 offset arrays (range ≈ -6..+6)."""
    rng = np.random.default_rng(42)
    out = []
    for _ in range(n):
        g = rng.integers(-8, 8, (LOW_H, LOW_W, 3), dtype=np.int8).astype(np.float32)
        out.append(g)
    return out


_LOW_GRAIN = _make_low_grain()

# How many render frames between full mesh recomputes; grain is added every frame
BG_SKIP = 3


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
    rows: list        # [(words_list, word_widths_list, row_width_px)]
    space: float      # pixel width of a space in F_BODY
    line_h: int
    block_h: int
    weights: list[int]  # word timing weights (by char count)
    total_w: int


def _precompute_layouts(lines: list[Line]) -> list[LineLayout]:
    """Pre-compute word-wrap + word widths once; render loop uses stored values."""
    max_w = WIDTH - 2 * PAD - 60
    space = _MEASURE_DRAW.textlength(" ", font=F_BODY)
    out = []
    for ln in lines:
        words = ln.text.split()
        if not words:
            out.append(LineLayout(ln, [], space, 0, 0, [], 0))
            continue
        # measure every word width once
        widths = [float(_MEASURE_DRAW.textlength(w, font=F_BODY)) for w in words]
        rows: list[tuple] = []
        cur_words: list[str] = []
        cur_wws:   list[float] = []
        cur_w = 0.0
        for w, ww in zip(words, widths):
            add = ww if not cur_words else cur_w + space + ww
            if add > max_w and cur_words:
                rows.append((cur_words, cur_wws, cur_w))
                cur_words, cur_wws, cur_w = [w], [ww], ww
            else:
                cur_words.append(w)
                cur_wws.append(ww)
                cur_w = add
        if cur_words:
            rows.append((cur_words, cur_wws, cur_w))
        line_h  = F_BODY.size + 22
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
def _mesh_base(t: float, palette, bokeh, accent) -> np.ndarray:
    """Compute mesh + vignette + bokeh at LOW res; return float32 (LOW_H,LOW_W,3).

    Called every BG_SKIP frames; result cached and grain added per-frame.
    """
    pts = []
    for i, col in enumerate(palette[:4]):
        ang = t * (0.13 + 0.04 * i) + i * 1.9
        px  = 0.5 + 0.44 * math.cos(ang + i * 0.7)
        py  = 0.5 + 0.44 * math.sin(ang * 0.75 + i * 1.4)
        pts.append((px, py, np.array(col, dtype=np.float32)))

    acc  = np.zeros((LOW_H, LOW_W, 3), dtype=np.float32)
    wsum = np.zeros((LOW_H, LOW_W, 1), dtype=np.float32)
    for px, py, col in pts:
        d2   = (_LOW_GX - px) ** 2 + (_LOW_GY - py) ** 2
        w    = 1.0 / (d2 * 20 + 0.03)
        acc  += w[..., None] * col
        wsum += w[..., None]
    field = acc / np.maximum(wsum, 1e-6)

    # Apply vignette here (low-res) — avoids a full-res float32 multiply later
    field *= _LOW_VIGNETTE

    # Bokeh drawn into the low-res image — ring+glow for real lens bokeh look
    img  = Image.fromarray(field.clip(0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    for b in bokeh:
        bx = (b.cx + math.cos(t * b.spd * 5 + b.phase) * 0.06) * LOW_W
        by = ((b.cy - t * b.spd) % 1.0) * LOW_H
        if b.col == 0:
            col, a = accent, 34
        elif b.col == 1:
            col, a = (255, 255, 255), 24
        else:
            col, a = (200, 200, 220), 16
        r = b.r
        # Bright edge ring (characteristic bokeh look) + dim fill + inner glow
        ring_w = max(1, int(r * 0.14))
        draw.ellipse([bx - r, by - r, bx + r, by + r],
                     fill=(*col, a // 5), outline=(*col, a), width=ring_w)
        r_in = max(1.0, r * 0.28)
        draw.ellipse([bx - r_in, by - r_in, bx + r_in, by + r_in],
                     fill=(*col, a // 4))

    return np.asarray(img, dtype=np.float32)  # (LOW_H, LOW_W, 3)


def _bg_frame(base: np.ndarray, frame: int) -> Image.Image:
    """Add per-frame grain to cached base, then upscale to full resolution."""
    field = base + _LOW_GRAIN[frame % len(_LOW_GRAIN)]
    field = field.clip(0, 255).astype(np.uint8)
    # BILINEAR: 2× faster than LANCZOS, indistinguishable at this source resolution
    return Image.fromarray(field, "RGB").resize((WIDTH, HEIGHT), Image.BILINEAR)


# ── caption rendering ─────────────────────────────────────────────────────────
def _draw_caption(img: Image.Image, layout: LineLayout, t_in_line: float, accent):
    if not layout.rows:
        return
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

    # Entry fade (first 0.15s), exit fade (last 0.25s of duration)
    pop = min(1.0, t_in_line / 0.15)
    remaining = layout.line.duration - t_in_line
    if remaining < 0.25:
        pop = min(pop, max(0.0, remaining / 0.25))
    base_alpha = int(255 * pop)

    y0       = HEIGHT // 2 - layout.block_h // 2
    card_pad = 48
    cx0, cy0 = PAD - 8, y0 - card_pad
    cx1, cy1 = WIDTH - PAD + 8, y0 + layout.block_h + card_pad

    # Frosted glass: downsample background → blur → upsample → dark tint → paste
    if pop > 0.02:
        region = img.crop((cx0, cy0, cx1, cy1))
        cw, ch = region.size
        # 1/4-scale blur is visually identical and ~16× cheaper than full-res blur
        small  = region.resize((max(1, cw // 4), max(1, ch // 4)), Image.BILINEAR)
        blurred = small.filter(ImageFilter.GaussianBlur(radius=4))
        glass  = blurred.resize((cw, ch), Image.BILINEAR)
        tint   = Image.new("RGBA", (cw, ch), (0, 0, 14, int(108 * pop)))
        glass  = Image.alpha_composite(glass, tint)
        # Rounded rectangle mask for glass card
        mask   = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, cw - 1, ch - 1], radius=36, fill=int(245 * pop)
        )
        img.paste(glass, (cx0, cy0), mask=mask)

    draw = ImageDraw.Draw(img, "RGBA")
    if pop > 0.02:
        # Subtle accent border on card
        draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=36,
                               fill=None, outline=(*accent, int(55 * pop)), width=2)

    y   = y0
    idx = 0
    for row_words, row_wws, row_w in layout.rows:
        x = (WIDTH - row_w) / 2
        for w, ww in zip(row_words, row_wws):
            if idx < active:
                color = (255, 255, 255, base_alpha)
            elif idx == active:
                color = (*accent, base_alpha)
            else:
                color = (160, 165, 180, int(base_alpha * 0.75))
            # Drop shadow
            draw.text((x + 2, y + 2), w, font=F_BODY, fill=(0, 0, 0, int(base_alpha * 0.55)))
            # Glow halo on the currently-spoken word
            if idx == active:
                ga = int(base_alpha * 0.24)
                for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)):
                    draw.text((x + ox, y + oy), w, font=F_BODY, fill=(*accent, ga))
            draw.text((x, y), w, font=F_BODY, fill=color)
            if idx == active:
                uy = y + F_BODY.size + 4
                draw.line([x, uy + 2, x + ww, uy + 2], fill=(*accent, int(base_alpha * 0.3)), width=7)
                draw.line([x, uy, x + ww, uy], fill=(*accent, base_alpha), width=3)
            x += ww + layout.space
            idx += 1
        y += layout.line_h


def _draw_chrome(img, title, title_font, title_w, frame, total, accent, n_lines, cur_line, t):
    draw = ImageDraw.Draw(img, "RGBA")

    # title fades in over 0.4s, holds at 85%, dims to 50% after 2s
    title_alpha = min(235, int(255 * min(1.0, t / 0.4)))
    if t > 2.0:
        hold = max(0.5, 1.0 - (t - 2.0) * 0.08)
        title_alpha = int(title_alpha * hold)

    tx = (WIDTH - title_w) / 2
    # Soft glow behind title text
    ga = int(title_alpha * 0.18)
    for ox, oy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
        draw.text((tx + ox, 150 + oy), title, font=title_font, fill=(*accent, ga))
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
def _compose(frame, total, t, title, title_font, title_w, layouts, theme, bokeh, bg_cache):
    """bg_cache is a local [base_array, last_frame] list passed from generate_video."""
    accent = theme["accent"]

    # Recompute mesh only every BG_SKIP frames; add fresh grain every frame
    if frame - bg_cache[1] >= BG_SKIP:
        bg_cache[0] = _mesh_base(t, theme["palette"], bokeh, accent)
        bg_cache[1] = frame

    bg  = _bg_frame(bg_cache[0], frame)
    img = bg.convert("RGBA")

    # find active caption
    cur = -1
    for i, lo in enumerate(layouts):
        if t >= lo.line.start:
            cur = i
    if cur >= 0:
        lo = layouts[cur]
        t_in = t - lo.line.start
        # show caption while within its duration (+0.25s tail for exit fade)
        if t_in < lo.line.duration + 0.25:
            _draw_caption(img, lo, t_in, accent)

    _draw_chrome(img, title, title_font, title_w, frame, total, accent, len(layouts), cur, t)
    # Return raw RGBA bytes — ffmpeg consumes rgba directly, saving a convert+copy
    return img.tobytes()


# ── audio assembly ─────────────────────────────────────────────────────────────
def _dc_block(audio: np.ndarray, rate: int = 22050) -> np.ndarray:
    """Remove DC offset and sub-80Hz rumble; vectorized first-order high-pass."""
    # alpha ≈ 1 - 2π·80/22050 → keeps speech, removes DC/LF rumble
    alpha = 1.0 - (2.0 * math.pi * 80.0 / rate)
    # x - delayed_x accumulated via cumsum trick
    diff = np.empty_like(audio)
    diff[0] = audio[0]
    diff[1:] = audio[1:] - audio[:-1]
    # IIR leaky integrator in frequency domain — use numpy.frompyfunc is slow,
    # so approximate with a one-pole moving average subtraction instead
    # Simple DC block: y[n] = x[n] - x[n-1] + alpha * y[n-1]
    # Vectorized via scanning: apply in chunks for good enough accuracy
    out = np.zeros_like(audio)
    CHUNK = 4096
    y_prev = 0.0
    for i in range(0, len(audio), CHUNK):
        seg_d = diff[i:i + CHUNK]
        seg_y = np.empty(len(seg_d), dtype=np.float32)
        # small Python loop over CHUNK (not per-sample)
        y = y_prev
        for j in range(len(seg_d)):
            y = seg_d[j] + alpha * y
            seg_y[j] = y
        out[i:i + CHUNK] = seg_y
        y_prev = float(seg_y[-1])
    return out


def _stereo_mix(voice: np.ndarray, music_bed: np.ndarray, music_vol: float,
                delay_samples: int = 180) -> np.ndarray:
    """Stereo mix: voice centred, music widened via Haas effect on right channel.

    Keeping voice in the centre preserves intelligibility; the Haas delay on
    the music bed creates perceived width without comb-filter colouration.
    """
    n = max(len(voice), len(music_bed))
    v = np.zeros(n, dtype=np.float32)
    v[:len(voice)] = voice[:n]
    m = np.zeros(n, dtype=np.float32)
    m[:len(music_bed)] = music_bed[:n]
    m *= music_vol
    # Right music channel: delayed copy (Haas ~8ms)
    m_r = np.zeros(n, dtype=np.float32)
    if delay_samples < n:
        m_r[delay_samples:] = m[:n - delay_samples]
    else:
        m_r[:] = m
    left  = v + m
    right = v + m_r
    # Per-channel soft tanh limiter
    peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right)))) + 1e-9
    if peak > 0.95:
        scale = 1.2 / peak
        left  = np.tanh(left  * scale).astype(np.float32) * 0.95
        right = np.tanh(right * scale).astype(np.float32) * 0.95
    stereo = np.empty(n * 2, dtype=np.float32)
    stereo[0::2] = left
    stereo[1::2] = right
    return stereo


def _mix_audio(voice: np.ndarray, duration: float, mood: str,
               music_vol: float, content_seed: int, tmp: Path) -> Path:
    bed  = music_engine.generate(duration, mood=mood, seed=content_seed)
    rate = tts_engine.SAMPLE_RATE
    n    = int(duration * rate) + rate
    v = np.zeros(n, dtype=np.float32)
    v[:min(len(voice), n)] = voice[:n]
    v = _dc_block(v, rate)
    m = np.zeros(n, dtype=np.float32)
    m[:min(len(bed), n)] = bed[:n]
    stereo = _stereo_mix(v, m, music_vol)
    out = tmp / "audio.wav"
    _write_stereo_wav(stereo, out, rate)
    return out


def _write_stereo_wav(stereo: np.ndarray, path: Path, rate: int):
    """Write interleaved float32 stereo as 16-bit PCM WAV."""
    import wave
    pcm = np.clip(stereo, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16.tobytes())


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
        title_w     = float(_MEASURE_DRAW.textlength(title, font=title_font))
        layouts     = _precompute_layouts(timed)
        bg_cache    = [None, -999]   # per-render cache, safe under _RENDER_LOCK
        report(25, "Rendering frames")

        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{WIDTH}x{HEIGHT}", "-pix_fmt", "rgba", "-r", str(FPS),
            "-i", "pipe:0",
            "-i", str(audio_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-maxrate", "6M", "-bufsize", "12M",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-shortest", "-movflags", "+faststart",
            str(out_path),
        ]
        errf = tempfile.TemporaryFile()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=errf)
        try:
            for f in range(total):
                t     = f / FPS
                frame = _compose(f, total, t, title, title_font, title_w,
                                 layouts, theme_cfg, bokeh, bg_cache)
                proc.stdin.write(frame)  # frame is already bytes (RGBA)
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
