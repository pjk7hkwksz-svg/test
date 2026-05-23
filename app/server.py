"""
FastAPI backend for the faceless video maker.

Endpoints:
  GET    /                      -> mobile PWA
  GET    /api/options           -> available voices / themes / capabilities
  POST   /api/generate          -> {topic|script, mode, voice, theme, speed} -> job id
  GET    /api/status/{job_id}   -> {state, progress, message, error, queue_pos}
  DELETE /api/cancel/{job_id}   -> cancel a pending or running job
  GET    /api/video/{job_id}    -> the finished MP4 (inline)
  GET    /api/download/{job_id} -> the finished MP4 (attachment)

Generation runs in a background thread; the phone polls /api/status.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import script_engine, tts_engine, video_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")

BASE = Path(__file__).resolve().parent
JOBS_DIR = BASE / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
STATIC = BASE / "static"

app = FastAPI(title="Faceless Video Maker")

# job_id -> dict(state, progress, message, error, file, title, created_at)
JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
# serialize heavy renders so concurrent requests don't thrash CPU
_RENDER_LOCK = threading.Lock()
_QUEUE: list[str] = []   # ordered list of job_ids waiting/rendering

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_MAX_CONTENT = 2000


class GenerateRequest(BaseModel):
    content: str
    mode: str = "topic"
    voice: str = "ryan"
    theme: str = "midnight"
    speed: float = 1.0

    @field_validator("content")
    @classmethod
    def _check_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("content is empty")
        if len(v) > _MAX_CONTENT:
            raise ValueError(f"content exceeds {_MAX_CONTENT} characters")
        return v

    @field_validator("voice")
    @classmethod
    def _check_voice(cls, v: str) -> str:
        if v not in tts_engine.VOICE_MODELS:
            raise ValueError(f"unknown voice: {v!r}")
        return v

    @field_validator("theme")
    @classmethod
    def _check_theme(cls, v: str) -> str:
        if v not in video_engine.THEMES:
            raise ValueError(f"unknown theme: {v!r}")
        return v

    @field_validator("speed")
    @classmethod
    def _check_speed(cls, v: float) -> float:
        return max(0.7, min(1.5, v))

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in ("topic", "script"):
            raise ValueError(f"unknown mode: {v!r}")
        return v


def _set(job_id: str, **kw):
    with _LOCK:
        JOBS.setdefault(job_id, {}).update(kw)


def _queue_pos(job_id: str) -> int:
    """Return 1-based position in queue, 0 if not waiting."""
    try:
        return _QUEUE.index(job_id) + 1
    except ValueError:
        return 0


def _run_job(job_id: str, req: GenerateRequest):
    log.info("job %s started  topic=%r  voice=%s  theme=%s", job_id, req.content[:60], req.voice, req.theme)
    try:
        _set(job_id, state="scripting", progress=2, message="Writing script")
        script = script_engine.build_script(req.content, mode=req.mode)
        _set(job_id, title=script["title"], lines=script["lines"],
             source=script["source"])
        log.info("job %s script done  source=%s  lines=%d", job_id, script["source"], len(script["lines"]))

        out = JOBS_DIR / f"{job_id}.mp4"

        render_start = time.time()

        def progress(pct, msg):
            pct = max(5, pct)
            eta = None
            elapsed = time.time() - render_start
            if pct > 10 and elapsed > 2:
                frac = (pct - 5) / 95.0
                total_est = elapsed / frac
                eta = max(0, int(total_est - elapsed))
            _set(job_id, state="rendering", progress=pct, message=msg, eta=eta)

        with _RENDER_LOCK:
            with _LOCK:
                if job_id in _QUEUE:
                    _QUEUE.remove(job_id)
            video_engine.generate_video(
                title=script["title"],
                lines=script["lines"],
                out_path=out,
                voice=req.voice,
                theme=req.theme,
                speed=req.speed,
                progress=progress,
            )

        size_mb = out.stat().st_size / 1_048_576 if out.exists() else 0
        log.info("job %s done  %.1f MB", job_id, size_mb)
        _set(job_id, state="done", progress=100, message="Ready",
             file=str(out), size_mb=round(size_mb, 1))
    except Exception as e:
        log.exception("job %s failed", job_id)
        with _LOCK:
            if job_id in _QUEUE:
                _QUEUE.remove(job_id)
        _set(job_id, state="error", progress=100,
             message="Generation failed", error=str(e)[:600])


def _cleanup_old_jobs():
    """Remove job files and records older than 2 hours."""
    cutoff = time.time() - 7200
    with _LOCK:
        expired = [jid for jid, j in JOBS.items()
                   if j.get("created_at", 0) < cutoff and j.get("state") in ("done", "error")]
    for jid in expired:
        path = JOBS_DIR / f"{jid}.mp4"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        with _LOCK:
            JOBS.pop(jid, None)
        log.debug("cleaned up job %s", jid)


def _validate_job_id(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(400, "Invalid job id")


@app.get("/api/options")
def options():
    _cleanup_old_jobs()
    voices = tts_engine.available_voices() or list(tts_engine.VOICE_MODELS)
    return {
        "voices": voices,
        "themes": list(video_engine.THEMES.keys()),
        "voice_labels": {
            "ryan":   "Ryan — warm male",
            "male":   "Marcus — clear male",
            "female": "Ava — clear female",
            "lessac": "Nova — smooth neutral",
        },
        "llm": bool(__import__("os").environ.get("ANTHROPIC_API_KEY")
                    or __import__("os").environ.get("OPENAI_API_KEY")),
    }


@app.post("/api/generate")
def generate(req: GenerateRequest):
    job_id = uuid.uuid4().hex[:12]
    _set(job_id, state="queued", progress=0, message="Queued", created_at=time.time())
    with _LOCK:
        _QUEUE.append(job_id)
    log.info("job %s queued  queue_len=%d", job_id, len(_QUEUE))
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    _validate_job_id(job_id)
    with _LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        queue_pos = _queue_pos(job_id)
        return {
            "state":     job.get("state"),
            "progress":  job.get("progress", 0),
            "message":   job.get("message", ""),
            "error":     job.get("error"),
            "title":     job.get("title"),
            "lines":     job.get("lines"),
            "source":    job.get("source"),
            "size_mb":   job.get("size_mb"),
            "queue_pos": queue_pos,
            "eta":       job.get("eta"),
        }


@app.delete("/api/cancel/{job_id}")
def cancel(job_id: str):
    _validate_job_id(job_id)
    with _LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        if job.get("state") in ("done", "error"):
            return {"cancelled": False, "reason": "already finished"}
        job["state"] = "error"
        job["message"] = "Cancelled"
        job["error"] = "Cancelled by user"
        if job_id in _QUEUE:
            _QUEUE.remove(job_id)
    log.info("job %s cancelled by user", job_id)
    return {"cancelled": True}


def _video_file(job_id: str) -> Path:
    _validate_job_id(job_id)
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
