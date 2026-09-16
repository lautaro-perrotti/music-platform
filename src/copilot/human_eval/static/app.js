const $ = (id) => document.getElementById(id);
const state = {
  case: null,
  response: emptyResponse(),
  deep: false,
  sessionId: localStorage.getItem("boothSession") || crypto.randomUUID().slice(0, 12),
  autosaveTimer: null,
  startedAt: 0,
  playToken: 0,
};
localStorage.setItem("boothSession", state.sessionId);

function emptyResponse() {
  return {
    mode: "QUICK",
    overall_feel: null,
    would_change: null,
    groove_feels_right: null,
    low_end_feels_right: null,
    problem_types: [],
    likely_causes: [],
    desired_actions: [],
    severity: null,
    confidence: null,
    notes: "",
    replays: 0,
    listen_ms: 0,
  };
}

const player = $("player");

function fmt(t) {
  if (!Number.isFinite(t)) return "0:00";
  const s = Math.max(0, Math.floor(t));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function autoplayOn() {
  return Boolean($("autoplay")?.checked);
}

function flash(text) {
  if ($("flash")) $("flash").textContent = text || "";
}

function selectGroup(name, value) {
  state.response[name] = value;
  document.querySelectorAll(`[data-group="${name}"] button`).forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.value === value);
  });
  adapt();
  queueSave();
}

function toggleMulti(name, value) {
  const set = new Set(state.response[name] || []);
  if (set.has(value)) set.delete(value);
  else set.add(value);
  state.response[name] = [...set];
  document.querySelectorAll(`[data-multi="${name}"] button`).forEach((btn) => {
    btn.classList.toggle("on", set.has(btn.dataset.value));
  });
  adapt();
  queueSave();
}

function adapt() {
  const change = state.response.would_change;
  $("quick-dims").classList.toggle("hidden", change !== "NO" && change !== "UNSURE");
  const problem = change === "YES";
  if (problem) $("deep").classList.remove("hidden");
  $("severity-box").classList.toggle("hidden", !problem);
  state.response.mode = $("deep").classList.contains("hidden") ? "QUICK" : "DEEP";
}

function applyResponse(resp) {
  state.response = { ...emptyResponse(), ...(resp || {}) };
  ["overall_feel", "would_change", "groove_feels_right", "low_end_feels_right", "severity", "confidence"].forEach((name) => {
    const value = state.response[name];
    document.querySelectorAll(`[data-group="${name}"] button`).forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.value === value);
    });
  });
  ["problem_types", "likely_causes", "desired_actions"].forEach((name) => {
    const set = new Set(state.response[name] || []);
    document.querySelectorAll(`[data-multi="${name}"] button`).forEach((btn) => {
      btn.classList.toggle("on", set.has(btn.dataset.value));
    });
  });
  $("notes").value = state.response.notes || "";
  state.deep = state.response.mode === "DEEP";
  $("deep").classList.toggle("hidden", !state.deep && state.response.would_change !== "YES");
  adapt();
}

function queuePlayback(play) {
  const token = ++state.playToken;
  const start = () => {
    if (token !== state.playToken) return;
    if (!play) return;
    player.play().catch(() => flash("dale Play"));
  };
  player.addEventListener("canplay", start, { once: true });
  if (player.readyState >= 3) start();
}

function loadCase(payload, { play } = {}) {
  if (!payload || !payload.case_id) {
    flash(payload?.error || "no se pudo cargar el caso");
    return;
  }
  state.case = payload;
  state.startedAt = performance.now();
  $("stage").classList.remove("hidden");
  $("done").classList.add("hidden");
  $("counter").textContent = `CASE ${String(payload.index).padStart(2, "0")} / ${String(payload.total).padStart(2, "0")}`;
  $("counts").textContent = `${payload.total - payload.remaining} completed`;
  $("remaining").textContent = `${payload.remaining} remaining`;
  $("progress").style.width = `${((payload.index - 1) / Math.max(payload.total, 1)) * 100}%`;
  applyResponse(payload.draft || payload.response || emptyResponse());
  flash("");
  const shouldPlay = play ?? autoplayOn();
  const url = payload.audio_url.includes("?")
    ? `${payload.audio_url}&v=${encodeURIComponent(payload.audio_sha256 || "")}`
    : `${payload.audio_url}?v=${encodeURIComponent(payload.audio_sha256 || "")}`;
  player.src = url;
  queuePlayback(shouldPlay);
  player.load();
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({ ok: false, error: res.statusText }));
  if (!res.ok && data.error) flash(data.error);
  return data;
}

function snapshot() {
  state.response.notes = $("notes").value;
  state.response.listen_ms = Math.round(performance.now() - state.startedAt);
  return {
    evaluation_run_id: state.case.evaluation_run_id,
    case_id: state.case.case_id,
    session_id: state.sessionId,
    response: state.response,
  };
}

function queueSave() {
  clearTimeout(state.autosaveTimer);
  state.autosaveTimer = setTimeout(autosave, 250);
}

async function autosave() {
  if (!state.case || state.case.locked) return;
  await api("/api/draft", { method: "POST", body: JSON.stringify(snapshot()) });
}

async function loadNext() {
  const data = await api("/api/next");
  if (data.done) return showDone(data);
  loadCase(data, { play: autoplayOn() });
}

function showDone(data) {
  player.pause();
  $("stage").classList.add("hidden");
  $("done").classList.remove("hidden");
  $("counter").textContent = "QUEUE COMPLETE";
  $("counts").textContent = `${data.completed || 0} labeled`;
  $("remaining").textContent = `${data.skipped || 0} skipped`;
  $("progress").style.width = "100%";
}

function answersReady() {
  return Boolean(state.response.overall_feel && state.response.would_change);
}

async function goRelative(direction) {
  if (!state.case) return;
  await autosave();
  const currentId = state.case.case_id;
  const data = await api("/api/go", {
    method: "POST",
    body: JSON.stringify({ ...snapshot(), direction }),
  });
  if (data.case_id === currentId && direction === "next") {
    flash("último caso");
    queuePlayback(autoplayOn());
    return;
  }
  loadCase(data, { play: autoplayOn() });
}

async function saveOrNext() {
  if (!state.case) return;
  $("next").classList.remove("need");
  await autosave();
  if (answersReady()) {
    const data = await api("/api/save", { method: "POST", body: JSON.stringify(snapshot()) });
    if (data.fatigue) {
      $("fatigue-text").textContent = `You completed ${data.completed_in_session} cases.`;
      $("fatigue-modal").classList.remove("hidden");
      state.pendingNext = data;
      return;
    }
    if (data.done) return showDone(data);
    if (data.next) {
      loadCase(data.next, { play: autoplayOn() });
      return;
    }
  }
  await goRelative("next");
}

$("play").addEventListener("click", () => {
  if (player.paused) player.play().catch(() => flash("no se pudo reproducir"));
  else player.pause();
});
$("replay").addEventListener("click", () => {
  player.currentTime = 0;
  state.response.replays += 1;
  player.play().catch(() => flash("dale Play"));
  queueSave();
});
$("seek").addEventListener("input", () => {
  if (!player.duration) return;
  player.currentTime = (Number($("seek").value) / 1000) * player.duration;
});
$("vol").addEventListener("input", () => {
  player.volume = Number($("vol").value) / 100;
});
player.addEventListener("play", () => {
  $("lamp").classList.add("on");
  $("play").textContent = "Pause";
});
player.addEventListener("pause", () => {
  $("lamp").classList.remove("on");
  $("play").textContent = "Play";
});
player.addEventListener("timeupdate", () => {
  $("time").textContent = `${fmt(player.currentTime)} / ${fmt(player.duration || state.case?.duration_s || 0)}`;
  if (player.duration) $("seek").value = String(Math.round((player.currentTime / player.duration) * 1000));
});
$("notes").addEventListener("input", queueSave);

document.querySelectorAll("[data-group]").forEach((group) => {
  group.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    selectGroup(group.dataset.group, btn.dataset.value);
  });
});
document.querySelectorAll("[data-multi]").forEach((group) => {
  group.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    toggleMulti(group.dataset.multi, btn.dataset.value);
  });
});

$("more").addEventListener("click", () => {
  $("deep").classList.toggle("hidden");
  state.deep = !$("deep").classList.contains("hidden");
  state.response.mode = state.deep ? "DEEP" : "QUICK";
  queueSave();
});

$("next").addEventListener("click", saveOrNext);

$("prev").addEventListener("click", () => goRelative("prev"));

$("skip").addEventListener("click", () => $("skip-modal").classList.remove("hidden"));
$("skip-cancel").addEventListener("click", () => $("skip-modal").classList.add("hidden"));
$("skip-reasons").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  $("skip-modal").classList.add("hidden");
  const data = await api("/api/skip", {
    method: "POST",
    body: JSON.stringify({ ...snapshot(), reason: btn.dataset.value }),
  });
  if (data.done) return showDone(data);
  loadCase(data.next, { play: autoplayOn() });
});

$("keep-going").addEventListener("click", async () => {
  $("fatigue-modal").classList.add("hidden");
  await api("/api/session", { method: "POST", body: JSON.stringify({ action: "continue", session_id: state.sessionId }) });
  const data = state.pendingNext;
  if (data?.done) return showDone(data);
  if (data?.next) loadCase(data.next, { play: autoplayOn() });
});
$("take-break").addEventListener("click", async () => {
  $("fatigue-modal").classList.add("hidden");
  await api("/api/session", { method: "POST", body: JSON.stringify({ action: "break", session_id: state.sessionId }) });
});

$("lock-case").addEventListener("click", async () => {
  if (!state.case) return;
  if (!answersReady()) {
    $("next").classList.add("need");
    flash("elegí overall feel y si cambiarías algo");
    return;
  }
  await autosave();
  const data = await api("/api/lock-case", {
    method: "POST",
    body: JSON.stringify(snapshot()),
  });
  flash(data.ok ? `locked r${data.revision}` : (data.error || "lock failed"));
  if (data.ok) loadNext();
});

$("lock-batch").addEventListener("click", async () => {
  const data = await api("/api/lock-batch", { method: "POST", body: JSON.stringify({}) });
  $("done-status").textContent = data.all_required_locked ? "Batch locked." : `Locked ${data.locked} cases.`;
});

$("autoplay").addEventListener("change", async () => {
  await api("/api/settings", { method: "POST", body: JSON.stringify({ autoplay: autoplayOn() }) });
  if (autoplayOn() && player.paused) player.play().catch(() => flash("dale Play"));
});

document.addEventListener("keydown", (ev) => {
  const typing = ev.target.tagName === "TEXTAREA" || ev.target.tagName === "INPUT";
  if (ev.code === "Space" && !typing) {
    ev.preventDefault();
    if (player.paused) player.play();
    else player.pause();
  }
  if (typing) return;
  if (ev.key === "r" || ev.key === "R") {
    player.currentTime = 0;
    state.response.replays += 1;
    player.play();
  }
  if (ev.key === "1") selectGroup("overall_feel", "VERY_GOOD");
  if (ev.key === "2") selectGroup("overall_feel", "GOOD");
  if (ev.key === "3") selectGroup("overall_feel", "UNSURE");
  if (ev.key === "4") selectGroup("overall_feel", "BAD");
  if (ev.key === "5") selectGroup("overall_feel", "VERY_BAD");
  if (ev.key === "y" || ev.key === "Y") selectGroup("would_change", "YES");
  if (ev.key === "n" || ev.key === "N") selectGroup("would_change", "NO");
  if (ev.key === "u" || ev.key === "U") selectGroup("would_change", "UNSURE");
  if (ev.key === "Enter") {
    ev.preventDefault();
    saveOrNext();
  }
  if (ev.key === "ArrowLeft") {
    ev.preventDefault();
    goRelative("prev");
  }
  if (ev.key === "ArrowRight") {
    ev.preventDefault();
    goRelative("next");
  }
});

if (autoplayOn()) {
  api("/api/settings", { method: "POST", body: JSON.stringify({ autoplay: true }) });
}
loadNext();
