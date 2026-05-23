"""
Topic -> short-form video script.

Two modes:
  * "topic"  : you give a subject, we build an engaging faceless-video script
               (hook -> punchy beats -> call to action).
  * "script" : you paste your own text; we just clean + split it into caption
               lines (best quality, you control the words).

If an LLM key is present (ANTHROPIC_API_KEY or OPENAI_API_KEY) the topic mode
uses it for real, topic-specific content. Otherwise a strong template engine
produces a usable script offline.
"""

from __future__ import annotations

import json
import os
import random
import re
import textwrap
import urllib.request

# ── caption splitting ───────────────────────────────────────────────────────

def split_into_lines(text: str, max_chars: int = 64) -> list[str]:
    """Break free text into short, punchy caption lines.

    Whole sentences are kept together when they fit; long ones are wrapped on
    word boundaries with orphan tails (1-2 words) merged back so we never show a
    single stray word on its own caption.
    """
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    lines: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_chars:
            lines.append(s)
            continue
        chunks = textwrap.wrap(s, width=max_chars, break_long_words=False)
        # merge a short orphan tail back into the previous chunk
        if len(chunks) >= 2 and len(chunks[-1].split()) <= 2:
            chunks[-2] = chunks[-2] + " " + chunks[-1]
            chunks.pop()
        lines.extend(chunks)
    return [l for l in lines if l]


def make_title(topic: str) -> str:
    t = topic.strip().rstrip(".!?")
    if not t:
        return "Did You Know?"
    words = t.split()
    if len(words) > 8:
        t = " ".join(words[:8]) + "…"
    return t[0].upper() + t[1:]


# ── template engine (offline) ────────────────────────────────────────────────

_HOOKS = [
    "Here's what nobody tells you about {topic}.",
    "Most people get {topic} completely wrong.",
    "This is why {topic} matters more than you think.",
    "Stop scrolling — this changes how you see {topic}.",
    "The truth about {topic} is wilder than you'd expect.",
    "I wish someone told me this about {topic} sooner.",
]

_BEATS = [
    "It started small, but it quietly shapes everything around it.",
    "The experts who study {topic} agree on one surprising thing.",
    "Number one: the part everyone ignores is the part that counts.",
    "Number two: tiny changes here create massive results over time.",
    "Most people quit right before the breakthrough moment.",
    "There's a hidden pattern, and once you see it you can't unsee it.",
    "The data shows something almost no one expects.",
    "And the simplest version of this is the most powerful.",
]

_CTAS = [
    "Follow for more on {topic}.",
    "Save this so you don't forget.",
    "Which part surprised you most?",
    "Share this with someone who needs it.",
    "Follow for more mind-blowing facts.",
]


def _template_script(topic: str, beats: int = 6) -> list[str]:
    rng = random.Random(hash(topic) & 0xFFFFFFFF)
    t = topic.strip().rstrip(".!?") or "this"
    lines = [rng.choice(_HOOKS).format(topic=t)]
    pool = _BEATS[:]
    rng.shuffle(pool)
    for b in pool[:max(3, beats)]:
        lines.append(b.format(topic=t))
    lines.append(rng.choice(_CTAS).format(topic=t))
    return lines


# ── optional LLM ──────────────────────────────────────────────────────────────

LLM_SYSTEM = (
    "You write punchy faceless short-form video scripts (TikTok / Reels / "
    "Shorts). Given a TOPIC, output 7-9 short spoken lines: a strong hook first, "
    "then concrete, specific, surprising facts or tips, then a call to action. "
    "One sentence per line. No emojis, no numbering, no markdown, no stage "
    "directions. Keep each line under 14 words."
)


def _llm_anthropic(topic: str) -> list[str] | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    body = json.dumps({
        "model": os.environ.get("SCRIPT_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 600,
        "system": LLM_SYSTEM,
        "messages": [{"role": "user", "content": f"TOPIC: {topic}"}],
    }).encode()
    req = urllib.request.Request(
        f"{base}/v1/messages", data=body, method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read())
        text = "".join(b.get("text", "") for b in data.get("content", []))
        lines = [l.strip(" -•\t") for l in text.splitlines() if l.strip()]
        return [l for l in lines if l] or None
    except Exception:
        return None


def _llm_openai(topic: str) -> list[str] | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = json.dumps({
        "model": os.environ.get("SCRIPT_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {"role": "user", "content": f"TOPIC: {topic}"},
        ],
        "temperature": 0.8,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body, method="POST",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read())
        text = data["choices"][0]["message"]["content"]
        lines = [l.strip(" -•\t") for l in text.splitlines() if l.strip()]
        return [l for l in lines if l] or None
    except Exception:
        return None


# ── public API ────────────────────────────────────────────────────────────────

def build_script(content: str, mode: str = "topic") -> dict:
    """
    Returns {"title": str, "lines": [str], "source": "llm"|"template"|"verbatim"}.
    """
    content = (content or "").strip()
    if not content:
        content = "an interesting fact"

    if mode == "script":
        lines = split_into_lines(content)
        title = make_title(lines[0] if lines else content)
        return {"title": title, "lines": lines, "source": "verbatim"}

    # topic mode
    lines = _llm_anthropic(content) or _llm_openai(content)
    source = "llm"
    if not lines:
        lines = _template_script(content)
        source = "template"

    # normalize lengths into caption-friendly lines
    flat = " ".join(lines)
    if max((len(l) for l in lines), default=0) > 58:
        lines = split_into_lines(flat)

    return {"title": make_title(content), "lines": lines, "source": source}
