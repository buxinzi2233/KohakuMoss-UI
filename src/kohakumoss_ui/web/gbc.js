/* Local GBC workbench. All effects are explicit; route lifetime owns requests,
 * timers, media and delegated listeners. No generation/training on mount. */
import { esc } from "./format.js";
import { createGbcApi, countText, errorText, nextPageOffset, GbcError } from "./gbc-api.js";
import { mountGraph } from "./gbc-graph.js";
import { loadGraphMedia } from "./gbc-media.js";
import { mountDraftEditor } from "./gbc-draft.js";

const ACTIVE = new Set(["preparing", "running", "stopping"]);
export async function gbcView(main, ctx) {
  main.__studioCleanup?.();
  const controller = new AbortController();
  const api = createGbcApi({ signal: controller.signal });
  const tr = (key, vars) => ctx.t(`gbc.${key}`, vars);
  const label = (key) => esc(tr(key));
  let dead = false, busy = false, timer = null, polling = false;
  let caps = null, settings = null, tasks = [], jobs = [], datasets = [];
  let task = null, job = null, dataset = null, cursor = 0, logs = "";
  let shards = [], shardNext = null, rows = [], pageNext = null, pageOffset = 0, pageKind = "dataset", sample = null;
  const selectedShards = new Set();
  const button = (action, key, extra = "") => `<button class="btn" type="button" data-act="${action}" ${extra}>${label(key)}</button>`;
  const input = (name, key, type = "text", value = "") => `<label class="gbc-field">${label(key)}<input class="field" name="${name}" type="${type}" value="${esc(value)}" ${type === "number" ? 'min="1" step="1"' : ""}></label>`;
  const select = (name, key, values) => `<label class="gbc-field">${label(key)}<select class="field" name="${name}">${values.map((v) => `<option value="${esc(v)}">${label(`option.${v}`)}</option>`).join("")}</select></label>`;
  const registered = (name, key, kind, purpose) => `<label class="gbc-field">${label(key)}<select class="field" name="${name}" data-registry="${kind}" data-purpose="${purpose}"></select></label>`;
  main.innerHTML = `<section class="gbc-workspace" data-ui>
    <div class="vhead surface"><h1>${label("title")}</h1><div class="acts"><a class="btn" href="#/datasets">${label("back")}</a>${button("refresh", "refresh")}</div></div>
    <p class="hint">${label("scope")}</p><div class="box gbc-capabilities" data-capabilities>${label("loading")}</div>
    <div class="gbc-toolbar" role="tablist" aria-label="${label("title")}">${["environment", "tasks", "datasets", "caption"].map((key, i) => `<button class="btn" type="button" role="tab" id="gbc-tab-${key}" aria-controls="gbc-pane-${key}" aria-selected="${i === 0}" data-tab="${key}">${label(`tab.${key}`)}</button>`).join("")}</div>
    <div data-status class="okline surface" role="status" aria-live="polite" tabindex="-1">${label("loading")}</div>
    <fieldset class="gbc-controls" disabled>
    <section class="box gbc-pane" id="gbc-pane-environment" role="tabpanel" aria-labelledby="gbc-tab-environment">
      <h2>${label("environment.title")}</h2><p class="hint">${label("environment.trust")}</p><div data-registry-summary></div>
      <form data-form="root" class="gbc-form">${input("rootPath", "environment.path")}${select("rootPurpose", "environment.purpose", ["input", "output", "model"])}<label class="gbc-check"><input name="rootTrust" type="checkbox">${label("environment.confirmRoot")}</label>${button("root-add", "environment.addRoot")}</form>
      <form data-form="runtime" class="gbc-form">${input("python", "environment.python")}${select("runtimeRole", "environment.role", ["generator", "data"])}<label class="gbc-check"><input name="runtimeTrust" type="checkbox">${label("environment.confirmRuntime")}</label>${button("runtime-add", "environment.addRuntime")}</form>
      <div class="gbc-form">${registered("probeRuntime", "environment.runtime", "runtimes", "generator")}${button("version", "environment.version")}${button("doctor", "environment.doctor")}</div><p class="hint">${label("environment.probeNote")}</p>
    </section>
    <section class="box gbc-pane" id="gbc-pane-tasks" role="tabpanel" aria-labelledby="gbc-tab-tasks" hidden>
      <h2>${label("tasks.title")}</h2><p class="hint">${label("tasks.limits")}</p>
      <form data-form="task" class="gbc-form">${select("mode", "tasks.mode", ["replay", "qwen35-local-v1"])}${registered("taskInput", "tasks.input", "roots", "input")}${input("taskPath", "tasks.path")}${registered("taskOutput", "tasks.output", "roots", "output")}${registered("taskRuntime", "environment.runtime", "runtimes", "generator")}${input("timeout", "tasks.timeout", "number", "3600")}
      <div data-local-models class="gbc-wide gbc-form" hidden>${select("sourceKind", "tasks.sourceKind", ["folder", "jsonl"])}${["vlm", "detect", "embed"].map((slot) => `${registered(`${slot}Root`, `model.${slot}`, "roots", "model")}${input(`${slot}Path`, "model.path")}`).join("")}<p class="hint gbc-wide">${label("model.note")}</p></div>
      ${button("task-create", "tasks.create")}</form>
      <div class="gbc-form"><label class="gbc-field">${label("tasks.history")}<select name="taskHistory" class="field"><option value="">${label("choose")}</option></select></label>${button("task-select", "tasks.select")}${button("task-check", "tasks.check")}${button("task-start", "tasks.start")}</div>
      <pre data-task class="code gbc-json">${label("tasks.empty")}</pre>
      <div class="gbc-form"><label class="gbc-field">${label("jobs.history")}<select name="jobHistory" class="field"><option value="">${label("choose")}</option></select></label>${button("job-select", "jobs.select")}${button("job-stop", "jobs.stop")}${button("job-force", "jobs.force")}</div>
      <div data-job role="status">${label("jobs.empty")}</div><pre class="code gbc-log" data-logs aria-label="${label("jobs.logs")}"></pre><p class="hint" data-log-meta></p>
      <div class="gbc-form">${registered("exportOutput", "tasks.output", "roots", "output")}${registered("exportRuntime", "datasets.runtime", "runtimes", "data")}${button("graphs", "graph.load")}${button("export", "datasets.export")}</div><p class="hint">${label("datasets.exportNote")}</p>
    </section>
    <section class="box gbc-pane" id="gbc-pane-datasets" role="tabpanel" aria-labelledby="gbc-tab-datasets" hidden>
      <h2>${label("datasets.title")}</h2><form data-form="import" class="gbc-form">${registered("importInput", "tasks.input", "roots", "input")}${input("importPath", "datasets.path")}${registered("importOutput", "tasks.output", "roots", "output")}${registered("dataRuntime", "datasets.runtime", "runtimes", "data")}${button("import", "datasets.import")}</form>
      <div class="gbc-form"><label class="gbc-field">${label("datasets.select")}<select name="dataset" class="field"><option value="">${label("choose")}</option></select></label>${button("dataset-select", "datasets.open")}${button("dataset-validate", "datasets.validate")}${button("samples", "datasets.samples")}</div>
      <div data-dataset>${label("datasets.empty")}</div><div class="table-scroll" tabindex="0"><table class="led"><thead><tr><th>${label("datasets.shard")}</th>${["declaredSamples", "manifestRows", "validatedSamples", "actualBytes"].map((key) => `<th class="num">${label(`count.${key}`)}</th>`).join("")}</tr></thead><tbody data-shards></tbody></table></div>${button("shards-next", "next")}
    </section>
    <section class="box gbc-pane" id="gbc-pane-caption" role="tabpanel" aria-labelledby="gbc-tab-caption" hidden><div data-draft-editor></div></section>
    <section class="box gbc-preview"><h2>${label("samples.title")}</h2><div class="gbc-form"><label class="gbc-field">${label("samples.select")}<select name="sample" class="field"><option value="">${label("choose")}</option></select></label>${button("sample-select", "samples.open")}${button("samples-first", "first")}${button("samples-next", "next")}<span class="hint" data-page-info>${label("samples.empty")}</span></div><div data-graph></div></section>
    </fieldset></section>`;
  const root = main.querySelector(".gbc-workspace");
  const q = (selector) => root.querySelector(selector);
  const value = (name) => q(`[name="${name}"]`).value;
  const required = (name) => { const el = q(`[name="${name}"]`); if (!el.value.trim()) { el.focus(); el.setAttribute("aria-invalid", "true"); throw new GbcError("INVALID_REQUEST", tr("required")); } el.removeAttribute("aria-invalid"); return el.value.trim(); };
  const confirm = (name) => { const el = q(`[name="${name}"]`); if (!el.checked) { el.focus(); throw new GbcError("INVALID_REQUEST", tr("environment.needTrust")); } };
  function status(message, failed = false) { if (dead) return; q("[data-status]").textContent = message; q("[data-status]").classList.toggle("gbc-error", failed); }
  function tab(name) { root.querySelectorAll("[data-tab]").forEach((el) => el.setAttribute("aria-selected", String(el.dataset.tab === name))); root.querySelectorAll(".gbc-pane").forEach((el) => { el.hidden = el.id !== `gbc-pane-${name}`; }); }
  const graph = mountGraph(q("[data-graph]"), ctx, { media: (row, context, signal) => {
    if (caps?.features?.media !== true) throw new GbcError("UNSUPPORTED_CAPABILITY");
    return loadGraphMedia(row, context, signal);
  } });
  const draft = mountDraftEditor(q("[data-draft-editor]"), ctx, { api, getSelection: () => ({ dataset, shards: [...selectedShards], sampleId: pageKind === "dataset" && sample ? `${sample.shard}/${sample.key}` : null }), onBusy: (on) => { busy = on; controls(); } });
  function controls() {
    if (dead) return;
    q(".gbc-controls").disabled = busy || !caps || !settings;
    q('[data-act="refresh"]').disabled = busy;
    const disabled = (act, off) => { q(`[data-act="${act}"]`).disabled = off; };
    disabled("task-check", !task || ACTIVE.has(job?.status));
    disabled("task-start", !task?.validationId || ACTIVE.has(job?.status));
    disabled("job-stop", !ACTIVE.has(job?.status) || job?.status === "stopping");
    disabled("job-force", !ACTIVE.has(job?.status));
    disabled("export", !job?.resultArtifactId || caps?.features?.exports !== true);
    disabled("graphs", !job?.resultArtifactId);
    disabled("dataset-validate", !dataset); disabled("samples", !dataset);
    disabled("shards-next", shardNext === null); disabled("samples-next", pageNext === null);
    disabled("samples-first", !rows.length); disabled("sample-select", !rows.length);
    disabled("task-create", !caps?.features?.[value("mode") === "replay" ? "replay" : "localModels"]);
    disabled("import", caps?.features?.imports !== true);
  }
  function registry() {
    root.querySelectorAll("[data-registry]").forEach((el) => {
      const previous = el.value; const kind = el.dataset.registry;
      el.innerHTML = `<option value="">${label("choose")}</option>` + settings[kind].filter((item) => (item.purpose || item.role) === el.dataset.purpose).map((item) => `<option value="${esc(item.rootId || item.runtimeId)}">${esc(item.path || item.python)}</option>`).join("");
      el.value = previous;
    });
    q("[data-registry-summary]").innerHTML = `<p>${label("revision")}: ${esc(settings.revision)}</p><ul>${settings.roots.map((item) => `<li>${label(`option.${item.purpose}`)} · ${esc(item.path)} <code>${esc(item.rootId)}</code></li>`).join("")}${settings.runtimes.map((item) => `<li>${label(`option.${item.role}`)} · ${esc(item.python)} <code>${esc(item.runtimeId)}</code></li>`).join("")}</ul>`;
  }
  function histories() {
    for (const [name, list, id] of [["taskHistory", tasks, "taskId"], ["jobHistory", jobs, "jobId"], ["dataset", datasets, "datasetId"]]) {
      const el = q(`[name="${name}"]`), previous = el.value;
      el.innerHTML = `<option value="">${label("choose")}</option>` + [...list].reverse().map((item) => `<option value="${esc(item[id])}">${esc(item[id])}${item.status ? ` · ${label(`status.${item.status}`)}` : ""}</option>`).join("");
      el.value = previous;
    }
  }
  async function refresh() {
    const [cap, config, ts, js, ds] = await Promise.all([api.get("capabilities"), api.get("settings"), api.get("tasks"), api.get("jobs"), api.get("datasets")]);
    if (dead) return;
    caps = cap; settings = config.settings; tasks = ts.tasks; jobs = js.jobs; datasets = ds.datasets;
    q("[data-capabilities]").innerHTML = `<h2>${label("capabilities")}</h2><div class="gbc-cap-list">${["replay", "localModels", "imports", "exports", "trainingDrafts", "captionPreview", "media", "remoteApi"].map((key) => `<span>${label(`feature.${key}`)}: <b>${label(cap.features[key] === true ? "supported" : "unsupported")}</b></span>`).join("")}</div><p class="hint">${label("capabilityNote")}</p><p class="hint">${label("remoteDisabled")}</p>`;
    registry(); histories();
    if (task) task = tasks.find((item) => item.taskId === task.taskId) || null;
    if (job) job = jobs.find((item) => item.jobId === job.jobId) || null;
    renderTask(); renderJob();
  }
  function renderTask() { q("[data-task]").textContent = task ? JSON.stringify(task, null, 2) : tr("tasks.empty"); }
  function renderJob() {
    q("[data-job]").innerHTML = job ? `<p><b>${label(`status.${job.status}`)}</b> <code>${esc(job.jobId)}</code> · ${label("revision")}: ${esc(job.revision)}</p><p>${label("jobs.counts")}: ${esc(countText(job.counts?.observedRecords))} / ${esc(countText(job.counts?.total))} · ${label("count.validatedSamples")}: ${esc(countText(job.counts?.validated))}</p>${job.error ? `<p class="gbc-error">${esc(errorText(job.error, ctx.t))}</p>` : ""}<p class="hint">${label("jobs.noPercent")}</p>` : label("jobs.empty");
    controls();
  }
  let jobEpoch = 0;
  function setJob(next) { jobEpoch++; job = next; cursor = 0; logs = ""; if (!jobs.some((item) => item.jobId === job.jobId)) jobs.push(job); histories(); q("[data-logs]").textContent = ""; renderJob(); schedule(); }
  function schedule() { clearTimeout(timer); if (!dead && job) timer = setTimeout(poll, 2000); }
  async function poll() {
    if (dead) return;
    if (busy || polling || document.hidden) { schedule(); return; }
    polling = true; const id = job?.jobId, epoch = jobEpoch, fromCursor = cursor;
    try {
      const [js, log] = await Promise.all([api.get("jobs", { jobId: id }), api.get("jobs/logs", { jobId: id, cursor: fromCursor, maxBytes: 32768 })]);
      if (dead || job?.jobId !== id || epoch !== jobEpoch) return;
      if (!Number.isSafeInteger(log.nextCursor) || log.nextCursor < fromCursor) throw new GbcError("PROTOCOL_ERROR");
      if (js.job.revision >= job.revision) job = js.job;
      cursor = log.nextCursor; logs = (logs + log.text).slice(-131072);
      q("[data-logs]").textContent = logs; q("[data-log-meta]").textContent = tr("jobs.logMeta", { cursor, dropped: countText(log.dropped) });
      if (job.taskId && task?.taskId === job.taskId) { const taskId = task.taskId; const result = await api.get("tasks", { taskId }); if (dead || job?.jobId !== id || epoch !== jobEpoch) return; if (task?.taskId === taskId && result.task.revision >= task.revision) { task = result.task; renderTask(); } }
      renderJob();
      if (ACTIVE.has(job.status) || !log.terminal || log.nextCursor - fromCursor >= 32768) schedule();
    } catch (error) { if (!dead && epoch === jobEpoch && error.name !== "AbortError") status(errorText(error, ctx.t), true); }
    finally { polling = false; }
  }
  async function loadShards(offset = 0) {
    const result = await api.get("datasets/shards", { datasetId: dataset.datasetId, offset, limit: 100 });
    if (dead) return;
    shardNext = nextPageOffset(result, offset, 10000); shards = result.shards;
    q("[data-shards]").innerHTML = shards.map((item, index) => `<tr><td><label><input type="checkbox" data-shard="${index}" ${selectedShards.has(item.name) ? "checked" : ""}>${esc(item.name)}</label></td>${["declaredSamples", "manifestRows", "validatedSamples", "actualBytes"].map((key) => `<td class="num">${esc(countText(item[key]))}</td>`).join("")}</tr>`).join("");
  }
  function renderDataset() {
    q("[data-dataset]").innerHTML = dataset ? `<p><code>${esc(dataset.datasetId)}</code> · ${label("revision")}: ${esc(dataset.revision)}</p><p>${["declaredSamples", "manifestRows", "validatedSamples", "actualBytes"].map((key) => `${label(`count.${key}`)}: ${esc(countText(dataset[key]))}`).join(" · ")}</p><p class="hint">${label("datasets.countNote")}</p><code>${esc(dataset.manifestHash)}</code>` : label("datasets.empty");
  }
  function clearPage() {
    rows = []; sample = null; pageNext = null; pageOffset = 0;
    q('[name="sample"]').innerHTML = `<option value="">${label("choose")}</option>`;
    q("[data-page-info]").textContent = tr("samples.empty");
    graph.show(null); draft.selectionChanged?.();
  }
  async function selectDataset(next) {
    dataset = next; selectedShards.clear(); shards = []; shardNext = null;
    q("[data-shards]").innerHTML = ""; clearPage(); renderDataset(); await loadShards();
  }
  async function loadPage(offset = 0) {
    const result = pageKind === "dataset" ? await api.get("datasets/page", { datasetId: dataset.datasetId, dataRuntimeId: required("dataRuntime"), offset, limit: 20 }) : await api.get("graphs", { artifactId: job.resultArtifactId, offset, limit: 20 });
    if (dead) return;
    pageNext = nextPageOffset(result, offset, pageKind === "dataset" ? 10000 : 268435456); rows = result.items; pageOffset = offset; sample = null;
    q('[name="sample"]').innerHTML = `<option value="">${label("choose")}</option>` + rows.map((row, index) => `<option value="${index}">${esc(row.key ?? row.sampleId)}</option>`).join("");
    q("[data-page-info]").textContent = tr(pageKind === "dataset" ? "samples.rowOffset" : "samples.byteOffset", { offset: pageOffset, count: rows.length });
    graph.show(null); draft.selectionChanged?.();
  }
  async function operation(action) {
    if (busy || dead) return; busy = true; controls(); status(tr("working"));
    try { await action(); if (!dead) status(tr("ready")); }
    catch (error) { if (!dead && error.name !== "AbortError") { status(errorText(error, ctx.t), true); q("[data-status]").focus(); } }
    finally { busy = false; controls(); }
  }
  const actions = {
    refresh: async () => { await refresh(); if (job) schedule(); },
    "root-add": async () => { confirm("rootTrust"); const result = await api.post("settings", { expectedRevision: settings.revision, roots: [{ path: required("rootPath"), purpose: value("rootPurpose"), confirmTrust: true }] }); if (dead) return; settings = result.settings; registry(); q('[name="rootTrust"]').checked = false; },
    "runtime-add": async () => { confirm("runtimeTrust"); const result = await api.post("settings", { expectedRevision: settings.revision, runtimes: [{ python: required("python"), role: value("runtimeRole"), confirmExecute: true }] }); if (dead) return; settings = result.settings; registry(); q('[name="runtimeTrust"]').checked = false; },
    version: () => probe("version"), doctor: () => probe("doctor"),
    "task-create": async () => {
      const mode = value("mode"), timeoutSeconds = Number(required("timeout"));
      if (!Number.isSafeInteger(timeoutSeconds)) throw new GbcError("INVALID_REQUEST");
      const payload = { settingsRevision: settings.revision, inputRootId: required("taskInput"), relativePath: required("taskPath"), outputRootId: required("taskOutput"), runtimeId: required("taskRuntime"), mode, timeoutSeconds };
      if (mode === "qwen35-local-v1") { payload.sourceKind = value("sourceKind"); payload.models = Object.fromEntries(["vlm", "detect", "embed"].map((slot) => [slot, { rootId: required(`${slot}Root`), relativePath: required(`${slot}Path`) }])); }
      const result = await api.post("tasks", payload); if (dead) return; task = result.task; tasks.push(task); histories(); renderTask();
    },
    "task-select": async () => { const result = await api.get("tasks", { taskId: required("taskHistory") }); if (!dead) { task = result.task; renderTask(); } },
    "task-check": async () => { const result = await api.post("tasks/check", { taskId: task.taskId, expectedRevision: task.revision, confirmExecution: true }); if (!dead) { task.validationId = null; setJob(result.job); } },
    "task-start": async () => { if (!window.confirm(tr("tasks.confirmRun"))) return; const result = await api.post("jobs/start", { taskId: task.taskId, expectedRevision: task.revision, manifestHash: task.inputManifestHash, validationId: task.validationId, confirmRun: true }); if (!dead) setJob(result.job); },
    "job-select": async () => { const result = await api.get("jobs", { jobId: required("jobHistory") }); if (!dead) setJob(result.job); },
    "job-stop": () => stop(false), "job-force": () => stop(true),
    import: async () => { if (!window.confirm(tr("datasets.confirmImport"))) return; const result = await api.post("imports", { sourceRootId: required("importInput"), relativePath: required("importPath"), outputRootId: required("importOutput"), dataRuntimeId: required("dataRuntime"), confirm: true }); if (!dead) { datasets.push(result.dataset); histories(); await selectDataset(result.dataset); } },
    export: async () => { if (!window.confirm(tr("datasets.confirmExport"))) return; const result = await api.post("exports", { artifactId: job.resultArtifactId, outputRootId: required("exportOutput"), dataRuntimeId: required("exportRuntime"), confirm: true }); if (!dead) { datasets.push(result.dataset); histories(); await selectDataset(result.dataset); if (!dead) tab("datasets"); } },
    "dataset-select": async () => { const result = await api.get("datasets", { datasetId: required("dataset") }); if (!dead) await selectDataset(result.dataset); },
    "dataset-validate": async () => { if (!window.confirm(tr("datasets.confirmValidate"))) return; const result = await api.post("datasets/validate", { datasetId: dataset.datasetId, expectedRevision: dataset.revision, dataRuntimeId: required("dataRuntime"), confirm: true }); if (!dead) { dataset = result.dataset; clearPage(); renderDataset(); await loadShards(); } },
    "shards-next": () => loadShards(shardNext),
    samples: async () => { pageKind = "dataset"; await loadPage(); },
    graphs: async () => { pageKind = "artifact"; await loadPage(); },
    "samples-first": () => loadPage(), "samples-next": () => loadPage(pageNext),
    "sample-select": async () => { const index = required("sample"); sample = rows[Number(index)]; await graph.show(sample, { dataset, job, dataRuntimeId: value("dataRuntime"), kind: pageKind }); draft.selectionChanged?.(); },
  };
  async function probe(checkMode) { if (!window.confirm(tr("environment.confirmProbe"))) return; const result = await api.post("runtimes/check", { runtimeId: required("probeRuntime"), checkMode, confirmExecution: true }); if (!dead) { setJob(result.job); tab("tasks"); } }
  async function stop(force) { if (force && !window.confirm(tr("jobs.confirmForce"))) return; const result = await api.post("jobs/stop", { jobId: job.jobId, expectedRevision: job.revision, force }); if (!dead) { job = result.job; renderJob(); schedule(); } }
  function click(event) {
    const target = event.target.closest("button"); if (!target || !root.contains(target)) return;
    if (target.dataset.tab) { tab(target.dataset.tab); return; }
    if (target.dataset.act && actions[target.dataset.act]) operation(actions[target.dataset.act]);
  }
  function change(event) {
    if (event.target.name === "mode") { q("[data-local-models]").hidden = value("mode") !== "qwen35-local-v1"; controls(); }
    if (event.target.hasAttribute("data-shard")) { const name = shards[Number(event.target.dataset.shard)].name; if (event.target.checked) selectedShards.add(name); else selectedShards.delete(name); draft.selectionChanged?.(); }
  }
  const submit = (event) => event.preventDefault();
  const visibility = () => { if (!document.hidden && job) schedule(); };
  root.addEventListener("click", click); root.addEventListener("change", change); root.addEventListener("submit", submit); document.addEventListener("visibilitychange", visibility);
  function cleanup() { dead = true; controller.abort(); clearTimeout(timer); graph.cleanup(); draft.cleanup(); root.removeEventListener("click", click); root.removeEventListener("change", change); root.removeEventListener("submit", submit); document.removeEventListener("visibilitychange", visibility); if (main.__studioCleanup === cleanup) main.__studioCleanup = null; }
  main.__studioCleanup = cleanup;
  await operation(refresh);
}
