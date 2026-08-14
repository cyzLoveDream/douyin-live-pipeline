const $ = (id) => document.getElementById(id);
const TOGGLES = [
  ["punch", "冲击 punch"],
  ["shake", "抖动 shake"],
  ["fade", "淡入 fade"],
  ["mask", "底栏遮罩"],
  ["progress", "进度条"],
  ["grain", "胶片颗粒"],
  ["glitch", "故障 glitch"],
  ["vignette", "暗角"],
];
const STAGES = ["watch", "record", "transcribe", "detect", "create", "edit", "compile"];

const state = {
  style: "douyin_hot",
  room: null,
  jobs: [],
  runId: null,
  versions: [],
};

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || data.detail || res.statusText);
  return data;
}

function defaultOn(k) {
  if (state.style === "clean") return false;
  if (k === "glitch" || k === "shake") return state.style === "party";
  return true;
}
function togglesHtml() {
  $("toggles").innerHTML = TOGGLES.map(
    ([k, label]) =>
      `<label class="toggle"><input type="checkbox" data-k="${k}" ${defaultOn(k) ? "checked" : ""}/>${label}</label>`
  ).join("");
}
function toggleValues() {
  const out = {};
  for (const el of $("toggles").querySelectorAll("input")) out[el.dataset.k] = el.checked;
  return out;
}

async function loadConfig() {
  const cfg = await api("/api/config");
  const styles = cfg.styles || ["douyin_hot", "party", "clean"];
  state.style = styles.includes("douyin_hot") ? "douyin_hot" : styles[0];
  $("styles").innerHTML = styles
    .map((s) => `<button data-style="${s}" class="${s === state.style ? "on" : ""}">${s}</button>`)
    .join("");
  $("versions").innerHTML = styles
    .map((s) => `<button data-ver="${s}" class="${state.versions.includes(s) ? "on" : ""}">${s}</button>`)
    .join("");
  const xf = cfg.xfades || ["fadeblack", "fade", "wipeleft", "slideleft", "circlecrop"];
  $("xfade").innerHTML = xf
    .map((x) => `<option value="${x}">${x}</option>`)
    .join("");
  togglesHtml();
}

$("styles").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-style]");
  if (!b) return;
  state.style = b.dataset.style;
  for (const el of $("styles").querySelectorAll("button")) el.classList.toggle("on", el === b);
  togglesHtml();
});
$("versions").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-ver]");
  if (!b) return;
  const v = b.dataset.ver;
  state.versions = state.versions.includes(v)
    ? state.versions.filter((x) => x !== v)
    : [...state.versions, v];
  b.classList.toggle("on", state.versions.includes(v));
});

function renderStages(stages) {
  $("stages").innerHTML = STAGES.map((s) => {
    const st = (stages && stages[s]) || "pending";
    return `<span class="${st === "done" ? "done" : ""}">${s}</span>`;
  }).join("");
}
function renderJobs(jobs) {
  state.jobs = jobs;
  $("jobs").innerHTML = jobs
    .map(
      (j) =>
        `<li data-room="${j.room}" class="${j.room === state.room ? "on" : ""}">${j.room}<div class="snippet">${j.current || ""} · ${j.clips?.length || 0} 条成片</div></li>`
    )
    .join("");
}
function renderHighlights(list) {
  $("highlights").innerHTML = (list || [])
    .map(
      (h) =>
        `<li><span class="score">${(h.score || 0).toFixed(2)}</span> ${h.start.toFixed(1)}–${h.end.toFixed(1)}s
         <div class="snippet">${h.title || ""}</div>
         <div class="snippet">${(h.hashtags || []).map((t) => `<span class="tag">${t}</span>`).join("")}</div></li>`
    )
    .join("") || "<li class='muted'>暂无高能</li>";
}
function renderGallery(clips) {
  const g = $("gallery");
  const playable = (clips || []).filter((c) => c.url);
  g.innerHTML = playable
    .map(
      (c, i) =>
        `<div class="thumb ${i === 0 ? "on" : ""}" data-url="${c.url}" title="${c.name}">
           <video src="${c.url}#t=0.1" muted></video>
         </div>`
    )
    .join("");
  if (playable[0]) setPreview(playable[0].url);
}
function setPreview(url) {
  const v = $("preview");
  v.src = url;
  v.classList.add("show");
  $("preview-empty").classList.add("hide");
  for (const t of $("gallery").querySelectorAll(".thumb")) t.classList.toggle("on", t.dataset.url === url);
}
$("gallery").addEventListener("click", (e) => {
  const t = e.target.closest(".thumb");
  if (t) setPreview(t.dataset.url);
});
$("jobs").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-room]");
  if (li) selectRoom(li.dataset.room);
});
async function selectRoom(room) {
  state.room = room;
  const job = await api(`/api/jobs/${encodeURIComponent(room)}`);
  renderStages(job.stages);
  renderGallery(job.clips);
  renderHighlights(job.highlights);
  renderJobs(state.jobs);
}
async function refreshJobs() {
  const data = await api("/api/jobs");
  renderJobs(data.jobs || []);
  if (!state.room && data.jobs?.length) await selectRoom(data.jobs[0].room);
  else if (state.room) await selectRoom(state.room);
}
function appendLog(line) {
  const el = $("log");
  el.textContent += line + "\n";
  el.scrollTop = el.scrollHeight;
}
function connectSSE() {
  const es = new EventSource("/api/events");
  es.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.line) appendLog(msg.line);
    } catch (_) {}
  };
}
async function start(dry) {
  const url = $("url").value.trim();
  if (!url) return appendLog("请先粘贴直播链接");
  const t = toggleValues();
  const body = {
    url,
    dry_run: dry,
    style: state.style,
    xfade: $("xfade").value,
    versions: state.versions,
    ...t,
  };
  appendLog(dry ? "Dry-run 启动…" : "开始流水线…");
  const res = await api("/api/run", { method: "POST", body: JSON.stringify(body) });
  state.runId = res.run_id;
  state.room = res.room;
  pollRun(res.run_id);
}
async function pollRun(id) {
  const tick = async () => {
    try {
      const r = await api(`/api/runs/${id}`);
      if (r.room) state.room = r.room;
      if (r.status === "done") {
        appendLog("流水线完成，刷新成片");
        await refreshJobs();
        return;
      }
      if (r.status === "error") {
        appendLog("失败 " + (r.error || ""));
        return;
      }
    } catch (e) {
      appendLog(String(e));
    }
    setTimeout(tick, 2000);
  };
  tick();
}
$("btn-start").onclick = () => start(false).catch((e) => appendLog(e.message));
$("btn-dry").onclick = () => start(true).catch((e) => appendLog(e.message));
$("btn-create").onclick = async () => {
  if (!state.room) return appendLog("先选择一个任务");
  try {
    const r = await api(`/api/create/${encodeURIComponent(state.room)}`, { method: "POST", body: "{}" });
    appendLog("二次创作完成 " + JSON.stringify((r.create || {}).cta || ""));
    await refreshJobs();
  } catch (e) { appendLog(e.message); }
};
$("btn-open").onclick = async () => {
  try {
    const r = await api("/api/open", { method: "POST", body: JSON.stringify({ kind: "clips", room: state.room }) });
    appendLog("成片目录 " + r.path);
  } catch (e) { appendLog(e.message); }
};
$("btn-jy").onclick = async () => {
  if (!state.room) return appendLog("先选择一个任务");
  try {
    const r = await api(`/api/jianying/${encodeURIComponent(state.room)}`, { method: "POST", body: "{}" });
    appendLog("草稿已写到 " + r.path);
  } catch (e) { appendLog(e.message); }
};
$("btn-pub").onclick = async () => {
  if (!state.room) return appendLog("先选择一个任务");
  try {
    const r = await api(`/api/publish/${encodeURIComponent(state.room)}`, {
      method: "POST",
      body: JSON.stringify({ dry_run: true }),
    });
    appendLog("发布 dry-run " + JSON.stringify(r.results || r));
  } catch (e) { appendLog(e.message); }
};

loadConfig().catch((e) => appendLog(e.message));
connectSSE();
refreshJobs().catch((e) => appendLog(e.message));
