const $ = (id) => document.getElementById(id);
const MAX_CONTENT = 2000;
const state = { mode: "topic", voice: "ryan", theme: "midnight", speed: 1, job: null, pollTimer: null, pollDelay: 1200 };

const THEME_COLORS = {
  midnight: "linear-gradient(150deg,#1e1450,#0a1e4a)",
  sunset:   "linear-gradient(150deg,#7a2030,#c0501e)",
  neon:     "linear-gradient(150deg,#5a0846,#1014c0)",
  emerald:  "linear-gradient(150deg,#085a3a,#06403c)",
  mono:     "linear-gradient(150deg,#222228,#0a0a0c)",
  crimson:  "linear-gradient(150deg,#5a0a18,#8a1428)",
  ocean:    "linear-gradient(150deg,#041e3a,#063060)",
};

async function loadOptions() {
  try {
    const r = await fetch("/api/options");
    const o = await r.json();
    // voices
    const vc = $("voiceChips"); vc.innerHTML = "";
    o.voices.forEach((v, i) => {
      const b = document.createElement("button");
      b.className = "chip" + (i === 0 ? " active" : "");
      b.textContent = (o.voice_labels && o.voice_labels[v]) || v;
      b.onclick = () => { state.voice = v; sel(vc, b); };
      vc.appendChild(b);
      if (i === 0) state.voice = v;
    });
    // themes
    const tc = $("themeChips"); tc.innerHTML = "";
    o.themes.forEach((t, i) => {
      const d = document.createElement("div");
      d.className = "theme" + (i === 0 ? " active" : "");
      d.style.background = THEME_COLORS[t] || "#222";
      d.innerHTML = `<span>${t}</span>`;
      d.onclick = () => { state.theme = t; sel(tc, d); };
      tc.appendChild(d);
      if (i === 0) state.theme = t;
    });
    $("modeBadge").textContent = o.llm ? "AI scripts" : "offline AI";
  } catch (e) { toast("Could not reach server", true); }
}

function sel(parent, el) {
  [...parent.children].forEach(c => c.classList.remove("active"));
  el.classList.add("active");
}

// mode toggle
$("modeSeg").querySelectorAll(".seg-btn").forEach(b => {
  b.onclick = () => {
    state.mode = b.dataset.mode;
    sel($("modeSeg"), b);
    const topic = state.mode === "topic";
    $("modeHint").textContent = topic
      ? "Give a topic — we write the script for you."
      : "Paste your own script — we narrate it word for word.";
    $("content").placeholder = topic
      ? "e.g. 3 morning habits that boost focus"
      : "Paste your full script here. Each sentence becomes a caption.";
    $("content").rows = topic ? 3 : 6;
  };
});

const SPEED_WORDS = { 0.8: "slow", 0.85: "slow", 0.9: "relaxed", 0.95: "relaxed",
  1: "normal", 1.05: "lively", 1.1: "lively", 1.15: "fast", 1.2: "fast", 1.25: "fast" };
$("speed").oninput = (e) => {
  state.speed = parseFloat(e.target.value);
  $("speedVal").textContent = SPEED_WORDS[state.speed] || "normal";
};

// Character counter
$("content").addEventListener("input", () => {
  const len = $("content").value.length;
  const cc = $("charCount");
  if (len > 100) {
    cc.textContent = len + " / " + MAX_CONTENT;
    cc.classList.toggle("warn", len > MAX_CONTENT * 0.9);
  } else {
    cc.textContent = "";
    cc.classList.remove("warn");
  }
});

function show(view) {
  ["createView", "progressView", "resultView"].forEach(v =>
    $(v).classList.toggle("hidden", v !== view));
}

function toast(msg, err) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.classList.remove("hidden");
  clearTimeout(t._hide);
  t._hide = setTimeout(() => t.classList.add("hidden"), err ? 8000 : 5000);
}

$("genBtn").onclick = async () => {
  const content = $("content").value.trim();
  if (!content) { toast("Enter a topic first", true); return; }
  if (content.length > MAX_CONTENT) {
    toast(`Topic too long — please shorten to ${MAX_CONTENT} characters`, true); return;
  }
  if (navigator.vibrate) navigator.vibrate(10);
  $("genBtn").disabled = true;
  $("genBtn").textContent = "Starting…";
  show("progressView");
  setProgress(2, "Starting…");
  $("scriptPreview").innerHTML = "";
  $("scriptPreview").dataset.filled = "";
  $("queueMsg").textContent = "";
  state.pollDelay = 1200;
  try {
    const r = await fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content, mode: state.mode, voice: state.voice,
        theme: state.theme, speed: state.speed,
      }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || "Request failed");
    }
    const { job_id } = await r.json();
    state.job = job_id;
    schedulePoll();
  } catch (e) {
    show("createView");
    toast(e.message || "Failed", true);
  } finally {
    $("genBtn").disabled = false;
    $("genBtn").textContent = "Generate video";
  }
};

function setProgress(pct, msg) {
  const p = Math.round(pct);
  $("pct").textContent = p + "%";
  $("barFill").style.width = p + "%";
  const pb = $("progressBar");
  if (pb) pb.setAttribute("aria-valuenow", p);
  if (msg) $("statusMsg").textContent = msg;
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(doPoll, state.pollDelay);
}

async function doPoll() {
  if (!state.job) return;
  try {
    const r = await fetch("/api/status/" + state.job);
    if (!r.ok) { schedulePoll(); return; }
    const s = await r.json();
    setProgress(s.progress || 0, s.message);

    // Show queue position or ETA
    if (s.queue_pos > 1) {
      $("queueMsg").textContent = `Queue position: ${s.queue_pos}`;
    } else if (s.eta != null && s.eta > 2) {
      $("queueMsg").textContent = `About ${s.eta}s remaining`;
    } else {
      $("queueMsg").textContent = "";
    }

    if (s.lines && !$("scriptPreview").dataset.filled) {
      $("scriptPreview").dataset.filled = "1";
      $("scriptPreview").innerHTML =
        `<div class="ln dim" style="color:#8ab4ff">📝 ${escapeHtml(s.title || "")} · ${s.source || ""}</div>` +
        (s.lines || []).map(l => `<div class="ln">${escapeHtml(l)}</div>`).join("");
    }

    if (s.state === "done") {
      if (navigator.vibrate) navigator.vibrate([40, 30, 40]);
      showResult(s);
      return;
    } else if (s.state === "error") {
      show("createView");
      toast("Error: " + (s.error || "generation failed"), true);
      return;
    }

    // Exponential backoff: 1.2s → 1.8s → 2.7s … cap at 4s
    state.pollDelay = Math.min(state.pollDelay * 1.5, 4000);
    schedulePoll();
  } catch (e) {
    schedulePoll();
  }
}

function showResult(s) {
  const player = $("player");
  player.onerror = () => {
    toast("Video failed to load — try downloading instead", true);
    $("resultMeta").textContent = "Playback error";
  };
  player.src = "/api/video/" + state.job + "?t=" + Date.now();
  $("downloadBtn").href = "/api/download/" + state.job;

  const meta = [];
  if (s && s.title) meta.push(s.title);
  if (s && s.size_mb) meta.push(s.size_mb + " MB");
  $("resultTitle").textContent = s && s.title ? s.title : "";
  if ($("resultMeta")) $("resultMeta").textContent = meta.length > 1 ? meta[1] : "";

  show("resultView");
}

$("cancelBtn").onclick = async () => {
  clearTimeout(state.pollTimer);
  if (state.job) {
    fetch("/api/cancel/" + state.job, { method: "DELETE" }).catch(() => {});
  }
  state.job = null;
  show("createView");
};

$("againBtn").onclick = () => {
  $("scriptPreview").dataset.filled = "";
  const player = $("player");
  player.pause();
  player.src = "";
  player.onerror = null;
  show("createView");
};

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
loadOptions();
