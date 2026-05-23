const $ = (id) => document.getElementById(id);
const state = { mode: "topic", voice: "ryan", theme: "midnight", speed: 1, job: null, poll: null };

const THEME_COLORS = {
  midnight: "linear-gradient(150deg,#1e1450,#0a1e4a)",
  sunset:   "linear-gradient(150deg,#7a2030,#c0501e)",
  neon:     "linear-gradient(150deg,#5a0846,#1014c0)",
  emerald:  "linear-gradient(150deg,#085a3a,#06403c)",
  mono:     "linear-gradient(150deg,#222228,#0a0a0c)",
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

function show(view) {
  ["createView", "progressView", "resultView"].forEach(v =>
    $(v).classList.toggle("hidden", v !== view));
}

function toast(msg, err) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  setTimeout(() => t.classList.add("hidden"), 3200);
}

$("genBtn").onclick = async () => {
  const content = $("content").value.trim();
  if (!content) { toast("Enter a topic first", true); return; }
  show("progressView");
  setProgress(2, "Starting…");
  $("scriptPreview").innerHTML = "";
  try {
    const r = await fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content, mode: state.mode, voice: state.voice,
        theme: state.theme, speed: state.speed,
      }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || "failed");
    const { job_id } = await r.json();
    state.job = job_id;
    poll();
  } catch (e) { show("createView"); toast(e.message || "Failed", true); }
};

function setProgress(pct, msg) {
  $("pct").textContent = Math.round(pct) + "%";
  $("barFill").style.width = pct + "%";
  if (msg) $("statusMsg").textContent = msg;
}

function poll() {
  clearInterval(state.poll);
  state.poll = setInterval(async () => {
    if (!state.job) return;
    try {
      const r = await fetch("/api/status/" + state.job);
      const s = await r.json();
      setProgress(s.progress || 0, s.message);
      if (s.lines && !$("scriptPreview").dataset.filled) {
        $("scriptPreview").dataset.filled = "1";
        $("scriptPreview").innerHTML =
          `<div class="ln dim" style="color:#8ab4ff">📝 ${s.title} · ${s.source}</div>` +
          s.lines.map(l => `<div class="ln">${escapeHtml(l)}</div>`).join("");
      }
      if (s.state === "done") { clearInterval(state.poll); showResult(); }
      else if (s.state === "error") {
        clearInterval(state.poll); show("createView");
        toast("Error: " + (s.error || "failed"), true);
      }
    } catch (e) { /* keep polling */ }
  }, 800);
}

function showResult() {
  $("player").src = "/api/video/" + state.job + "?t=" + Date.now();
  $("downloadBtn").href = "/api/download/" + state.job;
  fetch("/api/status/" + state.job).then(r => r.json())
    .then(s => { $("resultTitle").textContent = s.title || ""; });
  show("resultView");
}

$("cancelBtn").onclick = () => { clearInterval(state.poll); state.job = null; show("createView"); };
$("againBtn").onclick = () => {
  $("scriptPreview").dataset.filled = "";
  $("player").pause(); $("player").src = "";
  show("createView");
};

function escapeHtml(s) {
  return s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
loadOptions();
