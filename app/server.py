"""
FastAPI backend for the faceless video maker.

Endpoints:
  GET  /                      -> mobile PWA
  GET  /api/options           -> available voices / themes / capabilities
  POST /api/generate          -> {topic|script, mode, voice, theme, speed} -> job id
  GET  /api/status/{job_id}   -> {state, progress, message, error}
  GET  /api/video/{job_id}    -> the finished MP4 (inline)
  GET  /api/download/{job_id} -> the finished MP4 (attachment)

Generation runs in a background thread; the phone polls /api/status.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import script_engine, tts_engine, video_engine

BASE = Path(__file__).resolve().parent
JOBS_DIR = BASE / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
STATIC = BASE / "static"

app = FastAPI(title="Faceless Video Maker")

# job_id -> dict(state, progress, message, error, file, title)
JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
# serialize heavy renders so concurrent requests don't thrash CPU
_RENDER_LOCK = threading.Lock()


class GenerateRequest(BaseModel):
    content: str
    mode: str = "topic"          # "topic" | "script"
    voice: str = "ryan"
    theme: str = "midnight"
    speed: float = 1.0


def _set(job_id: str, **kw):
    with _LOCK:
        JOBS.setdefault(job_id, {}).update(kw)


def _run_job(job_id: str, req: GenerateRequest):
    try:
        _set(job_id, state="scripting", progress=2, message="Writing script")
        script = script_engine.build_script(req.content, mode=req.mode)
        _set(job_id, title=script["title"], lines=script["lines"],
             source=script["source"])

        out = JOBS_DIR / f"{job_id}.mp4"

        def progress(pct, msg):
            _set(job_id, state="rendering", progress=max(5, pct), message=msg)

        with _RENDER_LOCK:
            video_engine.generate_video(
                title=script["title"],
                lines=script["lines"],
                out_path=out,
                voice=req.voice,
                theme=req.theme,
                speed=req.speed,
                progress=progress,
            )
        _set(job_id, state="done", progress=100, message="Ready",
             file=str(out))
    except Exception as e:  # noqa: BLE001
        _set(job_id, state="error", progress=100,
             message="Generation failed", error=str(e)[:600])


@app.get("/api/options")
def options():
    voices = tts_engine.available_voices() or list(tts_engine.VOICE_MODELS)
    return {
        "voices": voices,
        "themes": list(video_engine.THEMES.keys()),
        "voice_labels": {
            "ryan": "Ryan — warm male",
            "male": "Marcus — clear male",
            "female": "Ava — clear female",
            "lessac": "Nova — smooth neutral",
        },
        "llm": bool(__import__("os").environ.get("ANTHROPIC_API_KEY")
                    or __import__("os").environ.get("OPENAI_API_KEY")),
    }


@app.post("/api/generate")
def generate(req: GenerateRequest):
    if not req.content.strip():
        raise HTTPException(400, "Please enter a topic or a script.")
    job_id = uuid.uuid4().hex[:12]
    _set(job_id, state="queued", progress=0, message="Queued")
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    with _LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return {
            "state": job.get("state"),
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "error": job.get("error"),
            "title": job.get("title"),
            "lines": job.get("lines"),
            "source": job.get("source"),
        }


def _video_file(job_id: str) -> Path:
    with _LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("state") != "done" or not job.get("file"):
        raise HTTPException(404, "Video not ready")
    path = Path(job["file"])
    if not path.exists():
        raise HTTPException(404, "Video missing")
    return path


@app.get("/api/video/{job_id}")
def video(job_id: str):
    return FileResponse(_video_file(job_id), media_type="video/mp4")


@app.get("/api/download/{job_id}")
def download(job_id: str):
    path = _video_file(job_id)
    return FileResponse(path, media_type="video/mp4",
                        filename=f"video_{job_id}.mp4")


# static PWA (mounted last so /api/* wins)
app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")


@app.exception_handler(404)
def _404(_, __):
    return JSONResponse({"error": "not found"}, status_code=404)
