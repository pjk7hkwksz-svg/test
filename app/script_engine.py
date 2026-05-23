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

import hashlib
import json
import os
import random
import re
import textwrap
import urllib.request

# ── caption splitting ───────────────────────────────────────────────────────

def split_into_lines(text: str, max_chars: int = 64) -> list[str]:
    """Break free text into short, punchy caption lines."""
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
        # merge a short orphan tail back into the previous chunk (only if it fits)
        if len(chunks) >= 2 and len(chunks[-1].split()) <= 3:
            merged = chunks[-2] + " " + chunks[-1]
            if len(merged) <= max_chars:
                chunks[-2] = merged
                chunks.pop()
        lines.extend(chunks)
    return [l for l in lines if l]


_TITLE_PATTERNS = [
    "{t}",
    "The Truth About {t}",
    "{t}: What Nobody Tells You",
    "{t} Explained",
    "Why {t} Matters",
    "The Real Story Behind {t}",
    "{t}: Shocking Facts",
    "What Nobody Tells You About {t}",
]


def make_title(topic: str) -> str:
    t = topic.strip().rstrip(".!?")
    if not t:
        return "Did You Know?"
    words = t.split()
    # 3-word cap ensures any pattern stays ≤ 55 chars on canvas
    short = " ".join(words[:3]) if len(words) > 3 else " ".join(words)
    short = short[0].upper() + short[1:]
    seed = int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)
    pattern = _TITLE_PATTERNS[seed % len(_TITLE_PATTERNS)]
    title = pattern.format(t=short)
    if len(title) > 55:
        title = short if len(short) <= 55 else short[:52] + "…"
    return title


# ── template engine (offline) ────────────────────────────────────────────────

_HOOKS = [
    "Here's what nobody tells you about {topic}.",
    "Most people get {topic} completely wrong.",
    "This is why {topic} matters more than you think.",
    "Stop scrolling — this changes how you see {topic}.",
    "The truth about {topic} is surprising.",
    "I wish someone told me about {topic} sooner.",
    "Schools never teach you this about {topic}.",
    "Three facts about {topic} that will shock you.",
    "This one fact about {topic} changes everything.",
    "Nobody talks about what {topic} does to you.",
    "The hidden side of {topic} nobody discovers.",
    "Most beliefs about {topic} are flat wrong.",
    "Here is the real story behind {topic}.",
    "If you care about {topic}, watch this.",
    "New research on {topic} shocked scientists.",
    "Your {topic} routine is missing this step.",
    "The truth about {topic} nobody wants to hear.",
    "Why {topic} is more powerful than you think.",
]

_BEATS = [
    "It started small, but it shapes everything around it.",
    "Experts on {topic} agree on one surprising thing.",
    "The part everyone ignores is the part that counts.",
    "Tiny changes here create massive results over time.",
    "Most people quit right before the breakthrough.",
    "Once you see the hidden pattern, you can't unsee it.",
    "The data shows something almost no one expects.",
    "The simplest version of this is always the most powerful.",
    "High performers figured this out years ago.",
    "The knowing-doing gap is where most people get stuck.",
    "It takes just twenty-one days to rewire this completely.",
    "The top one percent treat this completely differently.",
    "Your brain literally rewires when you do this.",
    "This single habit compounds faster than almost anything.",
    "What looks like talent is usually just practice.",
    "The science here is clearer than most people realize.",
    "Small improvements here stack into life-changing results.",
    "The reason most people fail at this is entirely fixable.",
    "Once you see the mechanism, everything changes.",
    "This is the part the self-help books always leave out.",
    "The counterintuitive move here is the one that works.",
    "Consistency beats intensity every single time.",
    "The people who get this right share one common habit.",
    "It compounds quietly until the results become undeniable.",
    "The gap between knowing and doing is where most fail.",
    "Your environment shapes your results more than willpower.",
    "What you track consistently, you improve automatically.",
    "Systems beat motivation every single time.",
    "The feedback loop is where real learning happens.",
    "Most advice ignores what actually drives long-term results.",
    "Boredom is the price of mastery — and it is worth it.",
    "The compounding effect applies to nearly everything.",
]

_CTAS = [
    "Follow for more on {topic}.",
    "Save this so you don't forget.",
    "Which part surprised you most?",
    "Share this with someone who needs it.",
    "Follow for more mind-blowing facts.",
    "Comment if this changed how you see {topic}.",
    "Follow to learn what most people never discover.",
    "Tag someone who needs to hear this today.",
    "Save this — you will want to rewatch it.",
    "Follow for daily insights that actually matter.",
    "Like if you learned something new here.",
    "Which of these will you try first?",
    "What would you add? Drop it below.",
    "Share this before the algorithm buries it.",
    "Follow for more content that makes you think.",
]


def _stable_seed(topic: str) -> int:
    """SHA-256-based seed — stable across Python runs unlike hash()."""
    return int(hashlib.sha256(topic.encode()).hexdigest()[:16], 16) % (2**31)


# Topic-category keywords → extra beats that feel relevant to that domain
_CATEGORY_BEATS: dict[str, list[str]] = {
    "health": [
        "Your body adapts faster than any doctor will admit.",
        "Inflammation is the hidden driver behind modern disease.",
        "Sleep is the multiplier that makes every other habit work.",
        "What happens after seven days straight is remarkable.",
        "Gut health controls far more than just digestion.",
        "Most people overlook the single most important biomarker.",
        "The link between stress and cellular aging is very real.",
    ],
    "finance": [
        "The wealthy automate savings before spending a dollar.",
        "Compound interest is the only rule that matters long-term.",
        "Every extra debt payment destroys years of interest.",
        "The average millionaire holds seven income streams.",
        "Index funds beat ninety percent of managers long-term.",
        "Most people lose wealth slowly, then all at once.",
        "The first dollar invested is always the most valuable.",
    ],
    "psychology": [
        "Your subconscious decides seven seconds before you do.",
        "Social proof is the most powerful persuasion tool alive.",
        "Mere exposure explains most of your preferences.",
        "Cognitive load drains willpower every hour of the day.",
        "Identity-based habits outlast goal-based ones three-fold.",
        "Fear of loss is twice as powerful as desire for gain.",
        "The brain cannot distinguish memory from imagination.",
    ],
    "fitness": [
        "Muscle is built in recovery, not during the workout.",
        "Progressive overload is the only principle that matters.",
        "Protein total beats protein timing for most people.",
        "Zone two cardio burns fat that HIIT cannot touch.",
        "The minimum effective dose is smaller than you think.",
        "Rest days are where adaptation actually happens.",
        "Most injuries come from doing too much too soon.",
    ],
    "history": [
        "This event was deliberately kept from the history books.",
        "That single decision changed the next five hundred years.",
        "What really happened was nothing like the official story.",
        "Historians only recently found the real motivation.",
        "The eyewitnesses left very different accounts.",
        "The victors rewrote this part almost completely.",
        "A single letter changed the course of the entire war.",
    ],
    "science": [
        "The peer-reviewed data tells a completely different story.",
        "Scientists were wrong about this for fifty years straight.",
        "The discovery happened entirely by accident.",
        "Even the researchers were shocked by the results.",
        "This effect shows up from quantum scale to cosmic scale.",
        "The real mechanism was hiding in plain sight all along.",
        "Replication studies reversed what everyone believed.",
    ],
    "productivity": [
        "The two-minute rule kills most procrastination cold.",
        "Deep work is a trainable skill almost no one practices.",
        "Time blocking doubles output over reactive scheduling.",
        "Decision fatigue peaks every single afternoon.",
        "Top performers work fewer hours but with total focus.",
        "Single-tasking beats multitasking by thirty percent.",
        "Your first ninety minutes are your most valuable.",
    ],
    "tech": [
        "AI is rewriting entire industries right now.",
        "The automation wave will touch every job category.",
        "Open-source tools made this accessible overnight.",
        "The best engineers ship small changes every single day.",
        "The gap between early and late adopters compounds fast.",
        "Most tech breakthroughs started as side projects.",
        "What looks like magic is just data and iteration.",
    ],
}

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "health":      ["health", "diet", "sleep", "gut", "food", "nutrition", "disease",
                    "immune", "cancer", "blood", "body", "brain", "hormone", "vitamin"],
    "finance":     ["money", "finance", "invest", "wealth", "debt", "stock", "crypto",
                    "budget", "saving", "income", "rich", "millionaire", "compound"],
    "psychology":  ["psychology", "mindset", "brain", "habit", "behavior", "anxiety",
                    "trauma", "emotion", "narcissist", "dopamine", "cognitive", "mental",
                    "meditation", "mindful", "stress", "happiness", "gratitude"],
    "fitness":     ["workout", "exercise", "muscle", "gym", "cardio", "fat", "weight",
                    "strength", "training", "protein", "lift", "run", "fitness"],
    "history":     ["history", "ancient", "war", "empire", "civiliz", "century",
                    "discover", "inventor", "revolution", "cold war", "dynasty"],
    "science":     ["science", "quantum", "physics", "chemistry", "biology", "evolut",
                    "atom", "dna", "gene", "climate", "universe", "space", "nasa"],
    "productivity":["productiv", "focus", "procrastinat", "deep work", "routine",
                    "morning", "habit", "schedule", "goal", "discipline", "stoic"],
    "tech":        ["ai", " tech", "software", "coding", "programming", "algorithm",
                    "machine learning", "blockchain", "startup", "automation", "robot",
                    "app", "crypto", "web3", "computer", "digital"],
}


def _detect_category(topic: str) -> str | None:
    t = topic.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return cat
    return None


def _short_topic(topic: str, max_words: int = 4) -> str:
    """Return a short version of the topic for use in hook/CTA templates."""
    words = topic.strip().rstrip(".!?").split()
    return " ".join(words[:max_words]) if len(words) > max_words else " ".join(words)


def _template_script(topic: str, beats: int = 6) -> list[str]:
    rng = random.Random(_stable_seed(topic))
    t = topic.strip().rstrip(".!?") or "this"
    # Use a short version in hooks/CTAs so lines don't exceed caption width
    t_short = _short_topic(t, max_words=3)

    lines = [rng.choice(_HOOKS).format(topic=t_short)]

    # Mix generic beats with category-specific ones for relevance
    cat = _detect_category(topic)
    if cat and cat in _CATEGORY_BEATS:
        cat_pool = _CATEGORY_BEATS[cat][:]
        rng.shuffle(cat_pool)
        # Use 2-3 category beats, rest generic
        n_cat = min(3, len(cat_pool))
        pool = cat_pool[:n_cat] + _BEATS[:]
    else:
        pool = _BEATS[:]

    rng.shuffle(pool)
    for b in pool[:max(3, beats)]:
        lines.append(b.format(topic=t_short))
    lines.append(rng.choice(_CTAS).format(topic=t_short))
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
        "model": os.environ.get("SCRIPT_MODEL", "claude-haiku-4-5-20251001"),
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
        with urllib.request.urlopen(req, timeout=15) as r:
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
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        choices = data.get("choices")
        if not choices:
            return None
        text = choices[0].get("message", {}).get("content", "")
        if not text:
            return None
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
    if max((len(l) for l in lines), default=0) > 64:
        lines = split_into_lines(flat)

    return {"title": make_title(content), "lines": lines, "source": source}
