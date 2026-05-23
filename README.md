# ShortMagic — AI Faceless Video Maker (phone app)

Type a topic on your phone, tap **Generate**, and get a ready-to-post
faceless short-form video (9:16, 1080×1920) with a neural AI voiceover,
animated visuals, captions, and background music — generated **entirely
on-device / offline**.

It's an installable **PWA** (Progressive Web App): open it in your phone
browser and "Add to Home Screen" to use it like a native app.

## Features

- **Topic → script** — type a subject and a script is written for you, or
  switch to **My script** mode and narrate your own words verbatim.
- **Neural voices** — four natural Piper voices (warm male, clear male,
  clear female, smooth neutral). No robotic TTS, no API keys.
- **Animated visuals** — drifting multi-point *mesh gradient* backgrounds,
  glowing bokeh, vignette + film grain. Five styles: midnight, sunset,
  neon, emerald, mono.
- **Karaoke captions** — the spoken word is highlighted in the accent color
  and underlined, in sync with the voice.
- **Procedural music** — a soft, royalty-free music bed mixed under the
  narration (mood matched to the style).
- **Phone-first UI** — responsive, installable, with live progress.
- **Optional smarter scripts** — set `ANTHROPIC_API_KEY` or
  `OPENAI_API_KEY` and topic mode will use an LLM for richer, topic-specific
  content. Without a key it falls back to a solid built-in template engine.

## Quick start

```bash
git clone <repo> && cd test
./setup.sh                       # installs ffmpeg/espeak-ng, deps, voices
python3 -m uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in a browser, or on your phone visit
`http://<your-computer-ip>:8000` while on the same Wi-Fi. Tap the browser's
**Add to Home Screen** to install it as an app.

### Optional: better scripts via LLM
```bash
export ANTHROPIC_API_KEY=sk-ant-...      # or OPENAI_API_KEY=sk-...
python3 -m uvicorn app.server:app --port 8000
```

## How it works

```
topic ─▶ script_engine ─▶ tts_engine (Piper) ─┐
                                               ├─▶ video_engine ─▶ MP4
        music.py (procedural bed) ─────────────┘
```

| File | Role |
|------|------|
| `app/server.py`       | FastAPI backend + serves the PWA; async render jobs |
| `app/script_engine.py`| topic → caption lines (template or LLM); verbatim mode |
| `app/tts_engine.py`   | Piper neural TTS, per-line timing for caption sync |
| `app/music.py`        | procedural lo-fi/ambient background music |
| `app/video_engine.py` | mesh-gradient visuals, karaoke captions, encoding |
| `app/static/`         | mobile PWA (HTML/CSS/JS, manifest, service worker) |

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/options`        | available voices, themes, LLM status |
| `POST` | `/api/generate`       | `{content, mode, voice, theme, speed}` → `{job_id}` |
| `GET`  | `/api/status/{id}`    | progress, state, generated script |
| `GET`  | `/api/video/{id}`     | stream the finished MP4 |
| `GET`  | `/api/download/{id}`  | download the finished MP4 |

## Requirements

- Python 3.9+
- `ffmpeg` and `espeak-ng` on the system
- ~250 MB disk for the neural voice models (downloaded by `setup.sh`)

## Notes

- A ~30 s video renders in roughly 60–90 s on a 4-core CPU (no GPU needed).
- The original command-line generator (`generate_tiktoks.py`) is kept for
  reference; the app supersedes it.
