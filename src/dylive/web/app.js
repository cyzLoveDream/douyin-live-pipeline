const $ = (id) => document.getElementById(id);
const show = (id) => { $(id).hidden = false; };
const hide = (id) => { $(id).hidden = true; };

const state = { room: null, runId: null, pollTimer: null, lastJob: null };

async function api(path, opts) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.detail || res.statusText);
  return data;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function renderGrid(job) {
  state.lastJob = job;
  const clips = (job.clips || []).filter((c) => c.url && !c.pack);
  const pack = (job.clips || []).find((c) => c.url && c.pack);
  const highs = job.highlights || [];
  const cards = clips.map((c, i) => {
    const h = highs[i] || {};
    const score = h.score != null ? (h.score * 100).toFixed(0) : "";
    const tags = (h.hashtags || []).slice(0, 3).map((t) => "<span>" + esc(t) + "</span>").join("");
    return [
      '<div class="card" data-url="' + esc(c.url) + '" data-index="' + i + '">',
      '<div class="cover"><video src="' + esc(c.url) + '#t=0.1" muted></video>',
      score ? '<span class="score">' + score + ' 分</span>' : "",
      "</div>",
      '<div class="meta"><div class="title">' + esc(h.title || c.name) + "</div>",
      '<div class="tags">' + tags + "</div>",
      '<button class="reclip">重新切</button>',
      "</div>",
      "</div>",
    ].join("");
  });
  if (pack) {
    cards.unshift([
      '<div class="card" data-url="' + esc(pack.url) + '">',
      '<div class="cover"><video src="' + esc(pack.url) + '#t=0.1" muted></video><span class="score">合集</span></div>',
      '<div class="meta"><div class="title">高能合集</div></div>',
      "</div>",
    ].join(""));
  }
  $("grid").innerHTML = cards.join("");
  $("room-label").textContent = "直播间 " + esc(job.room || "");
}

async function refreshResults() {
  try {
    const data = await api("/api/jobs");
    const jobs = data.jobs || [];
    if (!jobs.length) { show("empty"); hide("results"); return; }
    hide("empty");
    show("results");
    renderGrid(jobs[0]);
  } catch (e) { /* ignore */ }
}

function appendLog(line) {
  const el = $("log");
  const div = document.createElement("div");
  div.textContent = line;
  el.appendChild(div);
  while (el.children.length > 300) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
}

function connectSSE() {
  const es = new EventSource("/api/events");
  es.onmessage = (ev) => {
    try { const m = JSON.parse(ev.data); if (m.line) appendLog(m.line); } catch (_) {}
  };
}

function enterRunning() {
  hide("hero"); hide("results"); hide("empty");
  show("running");
  $("log").innerHTML = "";
  $("run-title").textContent = "正在等待主播开播…";
  $("run-sub").textContent = "开播后自动录制、转写、检测高能、剪辑";
}

async function start() {
  const url = $("url").value.trim();
  if (!url) { $("url").focus(); return; }
  $("btn-start").disabled = true;
  enterRunning();
  try {
    const res = await api("/api/run", { method: "POST", body: JSON.stringify({ url, dry_run: true }) });
    state.runId = res.id;
    state.room = res.room;
    pollRun();
  } catch (e) {
    appendLog("启动失败：" + e.message);
    $("btn-start").disabled = false;
  }
}

async function pollRun() {
  const tick = async () => {
    if (!state.runId) return;
    try {
      const r = await api("/api/runs/" + state.runId);
      if (r.room) state.room = r.room;
      if (r.status === "done") {
        $("run-title").textContent = "切片完成！";
        $("run-sub").textContent = "已自动剪出高能切片，下方可预览";
        $("btn-start").disabled = false;
        await refreshResults();
        hide("running");
        show("results");
        return;
      }
      if (r.status === "error") {
        $("run-title").textContent = "出错了";
        $("run-sub").textContent = r.error || "";
        $("btn-start").disabled = false;
        return;
      }
      $("run-title").textContent = "正在处理中…";
    } catch (_) {}
    state.pollTimer = setTimeout(tick, 2000);
  };
  tick();
}

async function reclip(room, index, btn) {
  btn.disabled = true;
  btn.textContent = "剪辑中…";
  try {
    await api("/api/reclip/" + encodeURIComponent(room), {
      method: "POST",
      body: JSON.stringify({ index: Number(index) }),
    });
    await refreshResults();
  } catch (e) {
    alert("重切失败：" + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "重新切";
  }
}

$("btn-start").addEventListener("click", start);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });

$("grid").addEventListener("click", (e) => {
  const reclipBtn = e.target.closest(".reclip");
  if (reclipBtn) {
    const card = reclipBtn.closest(".card");
    reclip(state.lastJob.room, card.dataset.index, reclipBtn);
    return;
  }
  const card = e.target.closest(".card");
  if (!card || !card.dataset.url) return;
  $("preview").src = card.dataset.url;
  show("backdrop");
});

$("btn-close").addEventListener("click", () => { $("preview").pause(); hide("backdrop"); });
$("backdrop").addEventListener("click", (e) => { if (e.target === $("backdrop")) { $("preview").pause(); hide("backdrop"); } });

connectSSE();
refreshResults();
