/* UI-owned translations. Source identity always comes from the saved Document. */
export function createTranslation({ root, api, esc, gt, alive, document, onChange, invalidate = () => {} }) {
  const state = { phase: "idle", planId: null, jobId: null, settings: null, view: null, dictionary: null,
    focusedSegmentId: null, hoveredSegmentId: null, epoch: 0, selectionEpoch: 0, window: null, orphanOffset: 0, sourceDirty: false, drafts: new Map(), missing: new Set(),
    plan: null, targets: null, results: null, job: null, dictionaryJob: null, settingsDraft: {}, install: {}, engine: "dictionary",
    confirmCost: false, replaceManual: false, switchLanguagePair: false, error: "", busy: false };
  let disposed = false, visible = true, timer = null, dictionaryTimer = null, polling = null, dictionaryPolling = null;
  let busyOwner = null, loadSerial = 0, targetSerial = 0, resultSerial = 0, viewContext = null;
  const draftContexts = new Map();
  const draftMatches = id => !state.drafts.has(id) || draftContexts.get(id) === JSON.stringify([identity(document()), pair(languages())]);
  const live = () => !disposed && visible && alive();
  const identity = doc => doc ? JSON.stringify([doc.libraryId, doc.documentId, doc.sourceHash, doc.sourceVersion, doc.generation]) : null;
  const languages = (settings = { ...state.settings, ...state.settingsDraft }) => ({ sourceLanguage: settings?.sourceLanguage || "en", targetLanguage: settings?.targetLanguage || "zh-CN" });
  const pair = value => JSON.stringify([value?.sourceLanguage, value?.targetLanguage]);
  const context = () => ({ epoch: state.epoch, doc: document(), identity: identity(document()), language: pair(languages()), selection: state.selectionEpoch });
  const current = (c, selection = false) => live() && c.epoch === state.epoch && c.doc === document() && c.identity === identity(document()) && c.language === pair(languages()) && (!selection || c.selection === state.selectionEpoch);
  const begin = () => { const owner = {}; busyOwner = owner; state.busy = true; return owner; };
  function finish(owner) { if (live() && busyOwner === owner) { busyOwner = null; state.busy = false; onChange(); } }
  const protocol = () => Object.assign(new Error(gt("translation.identityMismatch")), { code: "UPSTREAM_PROTOCOL" });
  const report = error => { state.error = `${error.code || ""} ${error.message}`; };
  const integer = n => Number.isInteger(n) && n >= 0;
  function sourceFor(id) {
    const doc = document(), matches = (doc?.fields || []).flatMap(field => field.segments.filter(s => s.segmentId === id).map(segment => ({ field, segment })));
    if (matches.length !== 1) return null;
    const { field, segment: s } = matches[0];
    return s.sourceHash === doc.sourceHash && s.sourceVersion === doc.sourceVersion && s.generation === doc.generation && s.recordId === field.recordId && s.pointer === field.pointer ? matches[0] : null;
  }
  function segmentIdentity(segment, source, language) {
    return Boolean(segment && source && ["segmentId", "recordId", "pointer", "ordinal", "sourceHash", "sourceVersion", "generation"].every(key => Object.hasOwn(segment, key) && segment[key] === source[key]) &&
      segment.sourceText === source.text && pair(segment) === pair(language) && typeof segment.targetText === "string" && integer(segment.segmentRevision));
  }
  // GET view intentionally has no libraryId/sourceHash: bind it to the request's
  // saved Document, never infer its context from a coincidentally matching ID.
  function validView(view, doc, language) {
    return view && doc && /^lib_[a-f0-9]{32}$/.test(doc.libraryId) && /^doc_[a-f0-9]{32}$/.test(doc.documentId) &&
      /^sha256:[a-f0-9]{64}$/.test(doc.sourceHash) && /^ver_[a-f0-9]{32}$/.test(doc.sourceVersion) && integer(doc.generation) && doc.generation > 0 &&
      view.documentId === doc.documentId && view.sourceVersion === doc.sourceVersion && view.generation === doc.generation && pair(view) === pair(language) &&
      integer(view.revision) && Array.isArray(view.segments) && Array.isArray(view.history) && view.historyCount === view.history.length &&
      new Set(view.segments.map(s => s.segmentId)).size === view.segments.length &&
      (view.snapshotRevision === null ? view.snapshotSourceVersion === null && view.snapshotGeneration === null && !view.segments.length :
        integer(view.snapshotRevision) && view.snapshotRevision > 0 && view.snapshotRevision <= view.revision && typeof view.snapshotSourceVersion === "string" && integer(view.snapshotGeneration) && view.snapshotGeneration > 0 &&
        view.segments.every(s => s.sourceVersion === view.snapshotSourceVersion && s.generation === view.snapshotGeneration && pair(s) === pair(view)));
  }
  function currentView() {
    const view = state.view, doc = document();
    return viewContext === identity(doc) && validView(view, doc, languages()) && view.snapshotSourceVersion === doc.sourceVersion && view.snapshotGeneration === doc.generation;
  }
  function validPage(page, key, id, offset) {
    return page?.[key] === id && page.offset === offset && page.limit === 60 && integer(page.total) && Array.isArray(page.segments) && page.segments.length <= 60;
  }
  const running = job => ["preparing", "running", "stopping"].includes(job?.status);
  const tr = key => esc(gt(`translation.${key}`));
  const button = (act, key, disabled = false, extra = "") => `<button type="button" class="btn" data-act="translation-${act}" ${disabled ? "disabled" : ""} ${extra}>${tr(key)}</button>`;
  const check = (field, key, checked = state[field]) => `<label><input type="checkbox" data-scope="translation" data-field="${field}" ${checked ? "checked" : ""}> ${tr(key)}</label>`;
  const field = (name, value, type = "text", scope = "settings") => `<label>${tr(name)}<input class="field" type="${type}" data-scope="translation" data-field="${scope}:${name}" value="${esc(value ?? "")}" autocomplete="off"></label>`;
  const call = async (method, route, payload = {}) => (await api(method, route, payload)).data;
  function visibleSegments() {
    return (state.window?.segments ?? document()?.fields[0]?.segments ?? []).slice(0, 60);
  }
  function invalidateSelection() {
    state.selectionEpoch++; targetSerial++; resultSerial++; state.planId = null; state.plan = null; state.targets = null; state.confirmCost = false; clearHighlight();
  }
  function setWindow(window) {
    if (!live()) return;
    if (state.window?.key !== window.key) invalidateSelection();
    state.window = { key: window.key, segments: window.segments.slice(0, 60) };
  }
  function compatible(segment) {
    const source = sourceFor(segment?.segmentId)?.segment;
    return !state.sourceDirty && currentView() && draftMatches(segment?.segmentId) && segmentIdentity(segment, source, state.view) &&
      ["translated", "stale"].includes(segment.status) && [null, "dictionary_changed", "settings_changed", "language_changed"].includes(segment.staleReason) &&
      visibleSegments().some(s => s.segmentId === source.segmentId);
  }
  function linkHint(key) {
    const hint = root.querySelector("#annotation-linked-description");
    const value = gt(`translation.${key}`);
    if (hint && hint.textContent !== value) hint.textContent = value;
  }
  function clearHighlight() {
    state.focusedSegmentId = null; state.hoveredSegmentId = null;
    for (const node of root.querySelectorAll(".annotation-linked")) { node.classList.remove("annotation-linked"); node.removeAttribute("aria-describedby"); }
    linkHint(state.sourceDirty ? "linkDisabled" : "linked");
  }
  function paintHighlight() {
    const id = state.focusedSegmentId || state.hoveredSegmentId;
    const segment = state.view?.segments.find(s => s.segmentId === id);
    const nodes = [...root.querySelectorAll("[data-segment-id]")];
    const matching = nodes.filter(n => n.dataset.segmentId === id && ["source", "translation"].includes(n.dataset.side));
    const enabled = compatible(segment) && matching.some(n => n.dataset.side === "source") && matching.some(n => n.dataset.side === "translation");
    const doc = document();
    if (enabled) {
      const { field: sourceField, segment: sourceSegment } = sourceFor(id);
      const rawEditor = root.querySelector('[data-field="rawText"]');
      const fieldEditor = [...root.querySelectorAll("textarea")].find(node => node.dataset.field === "source");
      const toTextarea = (value, index) => value.slice(0, index).replace(/\r\n/g, "\n").replace(/\r/g, "\n").length;
      const select = (editor, value, range) => {
        if (range && integer(range.start) && integer(range.end) && range.start <= range.end && range.end <= value.length && editor.value === value.replace(/\r\n/g, "\n").replace(/\r/g, "\n"))
          editor.setSelectionRange(toTextarea(value, range.start), toTextarea(value, range.end));
      };
      // Textareas have only one selection. The source overlay owns all rawRanges;
      // never substitute decoded valueRange for a JSON/JSONL raw offset.
      if (rawEditor) select(rawEditor, doc.rawText, sourceSegment.rawRanges?.[0]);
      else if (fieldEditor && sourceField.type === "string" && fieldEditor.dataset.recordId === sourceField.recordId && fieldEditor.dataset.pointer === sourceField.pointer)
        select(fieldEditor, sourceField.value, sourceSegment.valueRange);
    }
    for (const node of nodes) {
      const on = Boolean(enabled && matching.includes(node));
      if (node.classList.contains("annotation-linked") === on) continue;
      node.classList.toggle("annotation-linked", on);
      if (on) node.setAttribute("aria-describedby", "annotation-linked-description"); else node.removeAttribute("aria-describedby");
    }
    linkHint(state.sourceDirty || (id && !enabled) ? "linkDisabled" : "linked");
  }
  function highlight(event) {
    if (!live()) return;
    const node = event.target?.closest?.("[data-segment-id]");
    const next = event.relatedTarget?.closest?.("[data-segment-id]");
    if (node && root.contains(next) && next?.dataset.segmentId === node.dataset.segmentId) { paintHighlight(); return; }
    if (event.type === "pointerleave") state.hoveredSegmentId = null;
    else if (event.type === "blur") state.focusedSegmentId = null;
    else if (!node || !root.contains(node)) return;
    else if (event.type === "pointerover") state.hoveredSegmentId = node.dataset.segmentId;
    else if (event.type === "pointerout" && state.hoveredSegmentId === node.dataset.segmentId) state.hoveredSegmentId = null;
    else if (event.type === "focusin") state.focusedSegmentId = node.dataset.segmentId;
    else if (event.type === "focusout" && state.focusedSegmentId === node.dataset.segmentId) state.focusedSegmentId = null;
    paintHighlight();
  }
  for (const type of ["pointerover", "pointerout", "focusin", "focusout", "pointerleave"]) root.addEventListener(type, highlight);
  globalThis.window?.addEventListener("blur", highlight);
  async function load() {
    if (!live()) return;
    const c = context(), serial = ++loadSerial;
    clearHighlight();
    try {
      const [settings, dictionary] = await Promise.all([call("GET", "settings"), call("GET", "dictionary")]);
      if (!current(c) || serial !== loadSerial) return;
      const language = languages({ ...settings, ...state.settingsDraft });
      const view = c.doc ? await call("GET", "view", { documentId: c.doc.documentId, ...language }) : null;
      if (!current(c) || serial !== loadSerial) return;
      if (c.doc && !validView(view, c.doc, language)) throw protocol();
      state.settings = settings; state.dictionary = dictionary; state.view = view; viewContext = c.identity; state.error = ""; onChange();
    } catch (error) { if (current(c) && serial === loadSerial) { state.view = null; viewContext = null; report(error); onChange(); } }
  }
  async function targetPage(offset) {
    const c = context(), p = state.plan, serial = ++targetSerial;
    if (!p) return;
    const accepted = () => current(c, true) && p === state.plan && serial === targetSerial;
    try {
      const page = await call("GET", "plans/segments", { planId: p.planId, offset, limit: 60 });
      if (!accepted()) return;
      if (!validPage(page, "planId", p.planId, offset)) throw protocol();
      state.targets = page;
    } catch (error) { if (accepted()) throw error; }
  }
  async function resultPage(offset) {
    const c = context(), jobId = state.jobId, serial = ++resultSerial;
    if (!jobId) return;
    const accepted = () => current(c, true) && jobId === state.jobId && serial === resultSerial;
    try {
      const page = await call("GET", "jobs/segments", { jobId, offset, limit: 60 });
      if (!accepted()) return;
      if (!validPage(page, "jobId", jobId, offset)) throw protocol();
      state.results = page;
    } catch (error) { if (accepted()) throw error; }
  }
  async function plan(scope, options = {}) {
    if (!live() || state.busy || running(state.job) || state.sourceDirty) return false;
    const c = context(), owner = begin(), language = languages();
    state.phase = "planning"; state.confirmCost = false; state.error = ""; onChange();
    try {
      const value = await call("POST", "plans", { scope, engine: options.engine || state.engine, ...language, replaceManual: state.replaceManual,
        ...(options.selections ? { selections: options.selections } : {}), ...(options.segmentIds ? { segmentIds: options.segmentIds } : {}) });
      if (!current(c, true)) return false;
      if (pair(value) !== pair(language)) throw protocol();
      state.plan = value; state.planId = value.planId;
      await targetPage(0);
      if (!current(c, true) || state.plan !== value) return false;
      state.phase = "ready"; return true;
    } catch (error) { if (current(c, true)) { state.phase = "error"; report(error); } return false; }
    finally { finish(owner); }
  }
  async function start() {
    const p = state.plan;
    if (!live() || state.busy || state.sourceDirty || running(state.job) || p?.status !== "ready" || pair(p) !== pair(languages()) || (p.engine === "api" && !state.confirmCost)) return false;
    const c = context(), owner = begin(); onChange();
    try {
      const result = await call("POST", "jobs/start", { planId: p.planId, manifestHash: p.manifestHash,
        confirmRemote: p.engine === "api", confirmCost: p.engine === "api", acceptMaximumOutputTokens: p.maximumOutputTokens });
      if (!current(c, true) || state.plan !== p) return false;
      if (result.job?.planId !== p.planId || result.job.kind !== "translation") throw protocol();
      state.job = result.job; state.jobId = result.job.jobId; p.status = "consumed"; state.phase = "running";
      await poll(); return current(c);
    } catch (error) { if (current(c, true)) report(error); return false; }
    finally { finish(owner); }
  }
  async function poll() {
    if (!live() || polling || !state.jobId) return;
    const c = context(), token = {}, jobId = state.jobId;
    polling = token; clearTimeout(timer);
    const accepted = () => current(c) && polling === token && jobId === state.jobId;
    try {
      const job = await call("GET", "jobs", { jobId });
      if (!accepted()) return;
      if (job.jobId !== jobId || job.kind !== "translation" || job.planId !== state.job?.planId) throw protocol();
      state.job = job; state.phase = running(job) ? job.status === "stopping" ? "stopping" : "running" : "done";
      await resultPage(state.results?.offset || 0);
      if (!accepted()) return;
      if (!running(job)) await load();
      if (accepted()) onChange();
    } catch (error) { if (accepted()) { report(error); onChange(); } }
    finally { if (polling === token) { polling = null; if (current(c) && jobId === state.jobId && running(state.job)) timer = setTimeout(poll, 2000); } }
  }
  async function pollDictionary() {
    if (!live() || dictionaryPolling || !state.dictionaryJob) return;
    const c = context(), token = {}, jobId = state.dictionaryJob.jobId;
    dictionaryPolling = token; clearTimeout(dictionaryTimer);
    const accepted = () => current(c) && dictionaryPolling === token && jobId === state.dictionaryJob?.jobId;
    try {
      const job = await call("GET", "jobs", { jobId });
      if (!accepted()) return;
      if (job.jobId !== jobId || job.kind !== "dictionary") throw protocol();
      state.dictionaryJob = job;
      if (!running(job)) {
        const dictionary = await call("GET", "dictionary");
        if (!accepted()) return;
        state.dictionary = dictionary;
      }
      onChange();
    } catch (error) { if (accepted()) { report(error); onChange(); } }
    finally { if (dictionaryPolling === token) { dictionaryPolling = null; if (current(c) && jobId === state.dictionaryJob?.jobId && running(state.dictionaryJob)) dictionaryTimer = setTimeout(pollDictionary, 2000); } }
  }
  async function saveManual() {
    if (!live() || state.busy || state.sourceDirty || !document()) return false;
    const c = context(), owner = begin(), language = languages(), drafts = [...state.drafts];
    let view = state.view;
    // A concurrent GET must not overwrite the CAS receipt or its revision.
    loadSerial++;
    try {
      if (viewContext !== c.identity || !validView(view, c.doc, language) || drafts.some(([id]) => !sourceFor(id) || !draftMatches(id))) throw protocol();
      for (const [segmentId, targetText] of drafts) {
        if (!current(c, true) || state.sourceDirty || state.view !== view) return false;
        const source = sourceFor(segmentId)?.segment;
        const result = await call("POST", "manual", { documentId: c.doc.documentId, expectedSourceVersion: c.doc.sourceVersion,
          expectedRevision: view.revision, segmentId, targetText, ...language, switchLanguagePair: state.switchLanguagePair });
        if (!current(c, true) || state.sourceDirty || state.view !== view) return false;
        if (!integer(result.revision) || result.revision < view.revision || !segmentIdentity(result.segment, source, language) || result.segment.engine !== "manual" || result.segment.status !== "translated" || result.segment.manual !== true || result.segment.staleReason !== null) throw protocol();
        const segments = currentView() ? view.segments.filter(s => s.segmentId !== segmentId) : [];
        view = { ...view, revision: result.revision, snapshotRevision: result.revision, snapshotSourceVersion: c.doc.sourceVersion,
          snapshotGeneration: c.doc.generation, segments: [...segments, result.segment] };
        state.view = view; loadSerial++;
        if (state.drafts.get(segmentId) === targetText) state.drafts.delete(segmentId);
      }
      return true;
    } catch (error) { if (current(c, true)) report(error); return false; }
    finally { finish(owner); }
  }
  function capture(node) {
    if (!live() || node.dataset.scope !== "translation") return false;
    const name = node.dataset.field, value = node.type === "checkbox" ? node.checked : node.type === "number" ? Number(node.value) : node.value;
    if (name.startsWith("manual:")) {
      const id = name.slice(7);
      if (!state.drafts.has(id)) draftContexts.set(id, JSON.stringify([identity(document()), pair(languages())]));
      state.drafts.set(id, value);
      const save = root.querySelector('[data-act="translation-save-manual"]');
      if (save) save.disabled = state.busy || state.sourceDirty;
      paintHighlight();
    }
    else if (name.startsWith("missing:")) { if (value) state.missing.add(name.slice(8)); else state.missing.delete(name.slice(8)); invalidateSelection(); }
    else if (name.startsWith("settings:")) { state.settingsDraft[name.slice(9)] = value; loadSerial++; invalidateSelection(); }
    else if (name.startsWith("install:")) state.install[name.slice(8)] = value;
    else if (name === "apiKey") return true; // DOM-only until the explicit save reads it.
    else { state[name] = value; if (["engine", "replaceManual", "switchLanguagePair"].includes(name)) invalidateSelection(); }
    invalidate();
    return true;
  }
  async function action(act, target, scope, options = {}) {
    if (!act.startsWith("translation-")) return false;
    if (!live() || (state.busy && !["translation-stop", "translation-dictionary-stop"].includes(act))) return true;
    const c = context(); let owner = null;
    try {
      if (act === "translation-plan") await plan(scope, options);
      else if (act === "translation-missing-api") {
        const segmentIds = visibleSegments().filter(s => state.missing.has(s.segmentId)).map(s => s.segmentId);
        if (segmentIds.length) await plan(scope, { ...options, engine: "api", segmentIds });
      }
      else if (act === "translation-orphan-page") state.orphanOffset = Math.max(0, Number(target.dataset.offset));
      else if (act === "translation-start") await start();
      else if (act === "translation-save-manual") await saveManual();
      else if (act === "translation-discard-manual") { state.drafts.clear(); draftContexts.clear(); clearHighlight(); }
      else if (act === "translation-stop" || act === "translation-dictionary-stop") {
        const dict = act === "translation-dictionary-stop", job = dict ? state.dictionaryJob : state.job;
        if (running(job)) {
          const result = await call("POST", "jobs/stop", { jobId: job.jobId });
          if (!current(c) || job.jobId !== (dict ? state.dictionaryJob?.jobId : state.jobId)) return true;
          if (result.job?.jobId !== job.jobId || result.job.kind !== job.kind || result.job.planId !== job.planId) throw protocol();
          if (dict) state.dictionaryJob = result.job; else state.job = result.job;
        }
      } else if (act === "translation-target-page") await targetPage(Number(target.dataset.offset));
      else if (act === "translation-result-page") await resultPage(Number(target.dataset.offset));
      else if (act === "translation-save-settings") {
        owner = begin(); loadSerial++;
        const draft = { ...state.settingsDraft };
        const settings = await call("POST", "settings", { expectedConfigVersion: state.settings?.configVersion ?? null, ...draft });
        if (!current(c)) return true;
        state.settings = settings;
        for (const key of Object.keys(draft)) if (state.settingsDraft[key] === draft[key]) delete state.settingsDraft[key];
        invalidateSelection();
        await load();
      } else if (act === "translation-save-key" || act === "translation-clear-key") {
        owner = begin(); loadSerial++;
        const input = root.querySelector('[data-field="apiKey"]');
        const payload = { expectedConfigVersion: state.settings?.configVersion ?? null,
          ...(act === "translation-clear-key" ? { clear: true } : { apiKey: input?.value || "" }) };
        // Never retain a secret in controller state, even on failed/late requests.
        if (input) input.value = "";
        invalidate();
        const result = await call("POST", "credentials", payload);
        if (!current(c)) return true;
        Object.assign(state.settings ||= {}, { configVersion: result.configVersion, hasKey: result.hasKey, keyHint: result.keyHint }); loadSerial++; invalidateSelection();
      } else if (["translation-dictionary-import", "translation-dictionary-install", "translation-dictionary-update"].includes(act)) {
        if (running(state.dictionaryJob)) return true;
        const mode = act.slice("translation-dictionary-".length), data = state.install;
        if (mode !== "import" && !data.confirmNetwork) return true;
        owner = begin(); loadSerial++;
        const payload = mode === "import" ? { path: data.path || "" } : { downloadUrl: data.downloadUrl || "", ref: data.ref || "", expectedSha256: data.expectedSha256 || "", confirmNetwork: true,
          ...(mode === "update" ? { expectedDictionaryVersion: state.dictionary?.dictionaryVersion } : {}) };
        if (data.tableName) payload.tableName = data.tableName;
        const result = await call("POST", `dictionary/${mode}`, payload);
        if (!current(c)) return true;
        if (result.job?.kind !== "dictionary") throw protocol();
        state.dictionaryJob = result.job;
        await pollDictionary();
      }
    } catch (error) { if (current(c, true)) { report(error); if (error.details?.tables) state.error += ` · ${error.details.tables.join(", ")}`; } }
    finally { if (owner) finish(owner); else if (current(c, true)) onChange(); }
    return true;
  }
  function pager(data, act) {
    return data ? `<div class="acts">${button(act, "previous", data.offset === 0, `data-offset="${Math.max(0, data.offset - 60)}"`)}<span>${data.offset + 1}–${Math.min(data.offset + 60, data.total)} / ${data.total}</span>${button(act, "next", data.offset + 60 >= data.total, `data-offset="${data.offset + 60}"`)}</div>` : "";
  }
  function jobPane(job, dict = false) {
    const tables = dict && job?.error?.code === "DICTIONARY_TABLE_REQUIRED" && Array.isArray(job.error.details?.tables) ? job.error.details.tables.filter(name => typeof name === "string") : [];
    return job ? `<div role="status"><p>${tr(`status.${job.status}`)} · ${tr("processed")} ${job.processed}/${job.total} · ${tr("written")} ${job.written} · ${tr("skipped")} ${job.skipped} · ${tr("failed")} ${job.failed}</p>${job.error ? `<p role="alert">${esc(job.error.code)} · ${esc(job.error.message)}</p>` : ""}${tables.length ? `<p data-translation-tables>${tr("candidateTables")} ${tables.map(esc).join(", ")}</p>` : ""}${button(dict ? "dictionary-stop" : "stop", "stop", !running(job) || job.status === "stopping")}</div>` : "";
  }
  function pane() {
    const doc = document(), settings = { ...state.settings, ...state.settingsDraft }, saved = state.view?.segments || [];
    const segments = visibleSegments();
    const currentIds = new Set(doc?.fields.flatMap(f => f.segments.map(s => s.segmentId)) || []);
    const orphans = [...state.drafts].filter(([id]) => !currentIds.has(id) || !draftMatches(id));
    state.orphanOffset = Math.min(state.orphanOffset, Math.max(0, Math.ceil(orphans.length / 60) - 1) * 60);
    const visibleMissing = segments.filter(s => state.missing.has(s.segmentId)).length;
    return `<div class="annotation-translation"><h3>${tr("title")}</h3><p id="annotation-linked-description" class="hint">${tr(state.sourceDirty ? "linkDisabled" : "linked")}</p>
      ${state.error ? `<p role="alert">${esc(state.error)}</p>` : ""}
      ${segments.map(source => { const candidate = saved.find(x => x.segmentId === source.segmentId), valid = currentView() && segmentIdentity(candidate, sourceFor(source.segmentId)?.segment, state.view);
        const s = valid ? candidate : null, value = (draftMatches(source.segmentId) ? state.drafts.get(source.segmentId) : null) ?? (s ? s.targetText : source.text);
        return `<div class="annotation-segment" data-segment-id="${esc(source.segmentId)}" data-side="translation" tabindex="0"><p>${tr(`status.${s?.status || "missing"}`)} ${s?.manual || s?.engine === "manual" ? `<span data-translation-manual>${tr("manual")}</span>` : ""} ${s?.staleReason ? tr(`stale.${s.staleReason}`) : ""} ${candidate && !valid ? tr("identityMismatch") : ""}</p><textarea class="field" data-scope="translation" data-field="manual:${esc(source.segmentId)}" ${state.sourceDirty || state.busy ? "disabled" : ""}>${esc(value)}</textarea>${!s || s.status === "missing" ? check(`missing:${source.segmentId}`, "missing", state.missing.has(source.segmentId)) : ""}</div>`;
      }).join("")}${button("save-manual", "saveManual", state.sourceDirty || state.busy || !state.drafts.size)}${button("discard-manual", "discardManual", state.busy || !state.drafts.size)}${check("switchLanguagePair", "switchLanguagePair")}
      ${orphans.slice(state.orphanOffset, state.orphanOffset + 60).map(([id, value]) => `<p role="alert">${tr("orphanDraft")} ${esc(id)}</p><pre class="code">${esc(value)}</pre>`).join("")}
      ${orphans.length ? pager({offset: state.orphanOffset, total: orphans.length}, "orphan-page") : ""}
      <details data-details="translation-history"><summary>${tr("history")} (${state.view?.historyCount || 0})</summary><pre class="code">${esc(JSON.stringify(state.view?.history || [], null, 2))}</pre></details>
      <div class="acts"><select class="field" data-scope="translation" data-field="engine">${["dictionary", "api"].map(e => `<option value="${e}" ${state.engine === e ? "selected" : ""}>${tr(e)}</option>`).join("")}</select>${check("replaceManual", "replaceManual")}${button("plan", "preview", state.sourceDirty || state.busy || running(state.job))}${button("missing-api", "missingApi", state.sourceDirty || state.busy || !visibleMissing || running(state.job))}</div>
      ${state.plan ? `<div class="box"><p>${tr(`status.${state.plan.status}`)} · ${esc(state.plan.sourceLanguage)} → ${esc(state.plan.targetLanguage)} · ${esc(state.plan.model || state.plan.dictionaryVersion || "")}</p><p>${tr("targets")} ${state.plan.targetSegments} · ${tr("characters")} ${state.plan.sourceCharacters} · ${tr("cacheHits")} ${state.plan.cacheHits} · ${tr("remoteBatches")} ${state.plan.remoteBatches} · ${tr("maximumOutputTokens")} ${state.plan.maximumOutputTokens}</p><p class="hint">${tr("costHint")}</p>${state.plan.engine === "api" ? check("confirmCost", "confirmCost") : ""}${(state.targets?.segments || []).map(t => `<p>${esc(t.documentId)} · ${esc(t.segment.text)} · ${esc(t.status)} ${esc(t.code || "")}</p>`).join("")}${pager(state.targets, "target-page")}${button("start", "start", state.busy || state.plan.status !== "ready" || (state.plan.engine === "api" && !state.confirmCost))}</div>` : ""}
      ${jobPane(state.job)}${state.results ? `<details data-details="translation-results" open><summary>${tr("results")}</summary>${state.results.segments.map(s => `<p>${esc(s.sourceText)} → ${esc(s.targetText)} · ${esc(s.status)} ${esc(s.code || "")}</p>`).join("")}${pager(state.results, "result-page")}</details>` : ""}
      <details data-details="translation-settings"><summary>${tr("settings")}</summary><p>${tr("dictionary")} · ${tr(`status.${state.dictionary?.status || "missing"}`)} · ${esc(state.dictionary?.dictionaryVersion || "")} · ${esc(state.dictionary?.tableName || "")} · ${state.dictionary?.rows || 0}</p><p class="hint">${tr("license")}</p>
      ${["path", "tableName", "downloadUrl", "ref", "expectedSha256"].map(k => field(k, state.install[k], "text", "install")).join("")}${check("install:confirmNetwork", "confirmNetwork", state.install.confirmNetwork)}
      ${button("dictionary-import", "import", state.busy || running(state.dictionaryJob))}${button("dictionary-install", "install", state.busy || running(state.dictionaryJob))}${button("dictionary-update", "update", state.busy || running(state.dictionaryJob) || !state.dictionary?.dictionaryVersion)}${jobPane(state.dictionaryJob, true)}
      ${["endpoint", "model", "sourceLanguage", "targetLanguage"].map(k => field(k, settings[k])).join("")}${["timeoutSeconds", "maxOutputTokens", "temperature", "concurrency"].map(k => field(k, settings[k], "number")).join("")}${check("settings:enabled", "enabled", settings.enabled)}${button("save-settings", "saveSettings", state.busy)}
      <p>${tr(state.settings?.hasKey ? "keyConfigured" : "keyMissing")}</p><label>${tr("apiKey")}<input type="password" class="field" data-scope="translation" data-field="apiKey" autocomplete="new-password" value=""></label>${button("save-key", "saveKey", state.busy)}${button("clear-key", "clearKey", state.busy)}</details></div>`;
  }
  function reset() {
    state.epoch++; loadSerial++; invalidateSelection();
    clearTimeout(timer); clearTimeout(dictionaryTimer); polling = null; dictionaryPolling = null; busyOwner = null; viewContext = null;
    state.window = null; state.orphanOffset = 0; state.view = null; state.drafts.clear(); draftContexts.clear(); state.missing.clear();
    state.job = null; state.jobId = null; state.results = null; state.dictionaryJob = null;
    state.busy = false; state.sourceDirty = false; state.phase = "idle"; state.error = "";
    const input = root.querySelector('[data-field="apiKey"]'); if (input) input.value = "";
  }
  // Invalidate view work without discarding the transaction's old detail/drafts
  // or an already accepted job. Hidden tabs pause GET polling, never stop jobs.
  function invalidateView() {
    state.epoch++; loadSerial++; invalidateSelection();
    clearTimeout(timer); clearTimeout(dictionaryTimer); polling = null; dictionaryPolling = null;
    busyOwner = null; state.busy = false;
    state.phase = state.job ? (running(state.job) ? "running" : "done") : "idle";
    if (live() && running(state.job)) timer = setTimeout(poll, 2000);
    if (live() && running(state.dictionaryJob)) dictionaryTimer = setTimeout(pollDictionary, 2000);
  }
  function leave() { visible = false; invalidateView(); }
  function enter() {
    if (disposed) return;
    visible = true; clearHighlight();
    if (state.jobId) void poll();
    if (state.dictionaryJob) void pollDictionary();
  }
  return { state, pane, load, plan, start, poll, saveManual, highlight, clearHighlight, capture, action,
    invalidateSelection, invalidateView, enter, leave, setWindow, reset,
    dispose() { reset(); disposed = true; state.settings = null; state.dictionary = null; state.settingsDraft = {}; state.install = {};
      for (const type of ["pointerover", "pointerout", "focusin", "focusout", "pointerleave"]) root.removeEventListener(type, highlight); globalThis.window?.removeEventListener("blur", highlight); } };
}
