import { createTranslation } from "./grabber-translation.js";

export function textareaOffset(rawText, offset) {
  return rawText.slice(0, offset).replace(/\r\n/g, "\n").replace(/\r/g, "\n").length;
}
const ACTIVE_JOBS = new Set(["preparing", "running", "stopping"]);
const OPERATIONS = ["tag_add", "tag_remove", "tag_replace", "tag_dedupe", "nl_prepend", "nl_append", "nl_replace", "nl_delete", "clear", "normalize_newlines"];

export function createAnnotations({ root, main, api, esc, gt, alive, render, media, invalidate = () => {} }) {
  const browse = { phase: "idle", snapshotId: null, group: "", filters: { format: "any", status: "any", search: "" }, offset: 0, limit: 60,
    items: [], total: 0, truncated: false, selection: new Set(), itemId: null, epoch: 0, groups: [], createdAt: null };
  const editor = { phase: "closed", document: null, bindingId: null, sourceDraft: null, translationDrafts: null, plan: null, job: null,
    imageCollapsed: false, epoch: 0, candidates: [], candidateId: "", fieldIndex: 0, segmentOffset: 0, fieldDrafts: new Map(), mode: "both", raw: false, error: "" };
  const batch = { phase: "closed", scope: "page_selected", operation: "tag_add", missingPolicy: "skip", planId: null, manifestHash: null, epoch: 0,
    match: "", replacement: "", tokens: "", text: "", separator: "\n", newline: "lf", plan: null, rows: null, results: null, error: "" };
  const state = { browse, editor, batch, translation: null, error: "", binding: { kind: "tags", delimiter: "comma", mapMode: "sidecar", recordsPointer: "", recordIdPointer: "", imagePointer: "", fields: '[{"pointer":"/caption","type":"string"}]', recordIds: "" },
    create: { path: "", format: "txt", kind: "nl", delimiter: "comma", value: "" }, sharedPath: "", gate: false, gateSaving: false };
  let disposed = false, visible = false, timer = null, searchTimer = null, polling = false, mutation = false, pendingNavigate = null, jobResolve = null;
  const pages = new Map(), protectedGroups = new Set();
  const requestSerials = new Map();
  const context = () => ({ browse: browse.epoch, editor: editor.epoch, batch: batch.epoch, doc: editor.document, itemId: browse.itemId });
  const isCurrent = c => live() && visible && c.browse === browse.epoch && c.editor === editor.epoch && c.batch === batch.epoch && c.doc === editor.document && c.itemId === browse.itemId;
  // Each read lane also has a serial: two pages of the same plan may finish in reverse order.
  async function scopedRead(lane, method, route, payload, accept, commit) {
    const c = context(), serial = (requestSerials.get(lane) || 0) + 1;
    requestSerials.set(lane, serial);
    const current = () => isCurrent(c) && serial === requestSerials.get(lane) && accept();
    try { const result = await call(method, route, payload); if (current()) { commit(result); draw(); } }
    catch (error) { if (current()) throw error; }
  }
  function discardDrafts() {
    editor.sourceDraft = null; editor.fieldDrafts.clear(); editor.draftBlocked = false;
    translation.state.drafts.clear(); translation.state.sourceDirty = false; invalidatePlan(); translation.clearHighlight();
  }
  function cancelPendingRead() {
    editor.epoch++; browse.epoch++; browse.phase = "ready";
    if (editor.phase === "loading") editor.phase = dirty() ? "dirty" : editor.document ? "clean" : "closed";
  }
  const live = () => !disposed && alive();
  const tr = key => esc(gt(`annotation.${key}`));
  const button = (act, key, disabled = false, extra = "") => `<button type="button" class="btn" data-act="annotation-${act}" ${disabled ? "disabled" : ""} ${extra}>${tr(key)}</button>`;
  const input = (name, value, type = "text") => `<label>${tr(name)}<input class="field" type="${type}" data-scope="annotation" data-field="${name}" value="${esc(value ?? "")}"></label>`;
  const select = (name, value, options) => `<label>${tr(name)}<select class="field" data-scope="annotation" data-field="${name}">${options.map(v => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${tr(`option.${v}`)}</option>`).join("")}</select></label>`;
  const text = (name, value) => `<label>${tr(name)}<textarea class="field" data-scope="annotation" data-field="${name}">${esc(value ?? "")}</textarea></label>`;
  const draw = () => { if (live() && visible) render(); };
  const call = async (method, route, payload = {}) => (await api.annotation(method, route, payload)).data;
  const translation = createTranslation({ root, api: api.translation, esc, gt, alive: live, document: () => editor.document, onChange: draw, invalidate });
  state.translation = translation.state; editor.translationDrafts = translation.state.drafts;
  const active = () => mutation || ACTIVE_JOBS.has(editor.job?.status);
  const dirty = () => editor.sourceDraft !== null || editor.fieldDrafts.size > 0 || translation.state.drafts.size > 0;
  const item = () => browse.items.find(i => i.itemId === browse.itemId);
  const binding = () => editor.document?.bindings.find(b => b.bindingId === editor.bindingId);
  const fields = () => editor.document?.fields.filter(f => binding()?.recordIds.includes(f.recordId) && binding()?.spec.fields.some(p => p.pointer === f.pointer)) || [];
  function segmentWindow() {
    const field = fields()[editor.fieldIndex], doc = editor.document;
    const draft = editor.fieldDrafts.get(editor.fieldIndex);
    const total = field?.kind === "tags" && Array.isArray(draft) ? draft.length : field?.segments.length || 0;
    editor.segmentOffset = Math.min(editor.segmentOffset, Math.max(0, Math.ceil(total / 60) - 1) * 60);
    return { key: JSON.stringify([doc?.documentId, doc?.sourceVersion, doc?.generation, field?.recordId, field?.pointer, editor.segmentOffset]),
      offset: editor.segmentOffset, total, segments: field?.segments.slice(editor.segmentOffset, editor.segmentOffset + 60) || [] };
  }
  const errorText = error => `${error.code || ""} ${error.message}`;
  function scope(single = false) {
    if (!browse.snapshotId || browse.truncated) throw Error(gt("annotation.incomplete"));
    const mode = single ? "page_selected" : batch.scope;
    if (mode === "all_filtered") return { mode, snapshotId: browse.snapshotId };
    if (mode === "page_all") return { mode, snapshotId: browse.snapshotId, pageOffset: browse.offset };
    const itemIds = single ? [browse.itemId] : browse.items.filter(i => browse.selection.has(i.itemId)).map(i => i.itemId);
    if (!itemIds.length || itemIds.some(id => !browse.items.some(i => i.itemId === id))) throw Error(gt("annotation.selectRequired"));
    return { mode, snapshotId: browse.snapshotId, pageOffset: browse.offset, itemIds };
  }
  function selections(all = false) {
    const list = all ? fields() : [fields()[editor.fieldIndex]].filter(Boolean);
    if (!binding() || !list.length) throw Error(gt("annotation.confirmRequired"));
    return [{ itemId: browse.itemId, bindingId: editor.bindingId, recordIds: [...new Set(list.map(f => f.recordId))], pointers: [...new Set(list.map(f => f.pointer))] }];
  }
  function clearEditor() {
    editor.epoch++; editor.document = null; editor.bindingId = null; editor.sourceDraft = null; editor.fieldDrafts.clear(); editor.candidates = []; editor.candidateId = "";
    editor.phase = "closed"; editor.error = ""; editor.draftBlocked = false; editor.plan = null; editor.fieldIndex = 0; editor.segmentOffset = 0; editor.raw = false; translation.reset();
  }
  function invalidatePlan() { translation.invalidateSelection(); editor.plan = null; batch.plan = null; batch.planId = null; batch.rows = null; batch.epoch++; if (batch.phase !== "closed") batch.phase = "editing"; }
  function markDirty() {
    cancelPendingRead(); editor.phase = "dirty"; invalidatePlan(); translation.state.sourceDirty = true; translation.clearHighlight();
    // Input never replaces the editor or interrupts composition/selection.
    const status = root.querySelector('[data-annotation-dirty]'); if (status) status.textContent = gt("annotation.dirty");
    for (const node of root.querySelectorAll('[data-act="annotation-apply"]')) node.disabled = true;
    invalidate();
  }
  async function readPage(offset, fresh = false, group = browse.group, filters = browse.filters, edge = null) {
    if (!live() || !visible) return false;
    translation.invalidateView();
    const epoch = ++browse.epoch, editorEpoch = editor.epoch, oldId = browse.itemId;
    const current = () => live() && visible && epoch === browse.epoch && editorEpoch === editor.epoch;
    browse.phase = "loading"; state.error = ""; draw();
    try {
      let page;
      if (fresh || !browse.snapshotId) page = await call("POST", "snapshots", { group, filters, limit: 60 });
      else if (pages.has(offset)) page = pages.get(offset);
      else page = await call("GET", "snapshots", { snapshotId: browse.snapshotId, offset, limit: 60 });
      if (!current()) return false;
      if (fresh && offset > 0 && page.total > 60) page = await call("GET", "snapshots", {snapshotId: page.snapshotId, offset: Math.min(offset, Math.floor((page.total - 1) / 60) * 60), limit: 60});
      if (!current()) return false;
      // Page, candidate and document are a single view transaction. No old editor,
      // selection, translation draft or media is discarded before every read succeeds.
      const target = edge === "current" ? page.items.find(i => i.itemId === oldId) : edge ? page.items[edge > 0 ? 0 : page.items.length - 1] : null;
      const staged = target ? await stageItem(target, page.snapshotId, current) : null;
      if (!current()) return false;
      if (fresh) { pages.clear(); browse.selection.clear(); batch.scope = "page_selected"; }
      Object.assign(browse, { snapshotId: page.snapshotId, group: page.group, filters: page.filters, offset: page.offset, items: page.items,
        total: page.total, truncated: page.truncated, createdAt: page.createdAt, phase: "ready" });
      pages.delete(page.offset); pages.set(page.offset, page); while (pages.size > 3) pages.delete(pages.keys().next().value);
      invalidatePlan();
      if (staged && edge === "current") {
        editor.candidates = staged.candidates;
        if (staged.doc) commitDocument(staged.doc);
        else { editor.document = null; editor.bindingId = null; editor.candidateId = ""; translation.clearHighlight(); }
      } else if (staged) commitItem(staged);
      else if (oldId && !page.items.some(i => i.itemId === oldId)) { browse.itemId = null; clearEditor(); }
      api.onGroup?.(browse.group); draw(); const committed = context(); if (staged?.doc) await translation.load(); return isCurrent(committed);
    } catch (error) { if (current()) { browse.phase = "error"; state.error = errorText(error); draw(); } return false; }
  }
  async function enter() {
    visible = true; translation.enter();
    if (browse.snapshotId) { draw(); await translation.load(); return; }
    const epoch = ++browse.epoch;
    try {
      const data = await api.get("/api/grabber/library/groups");
      if (!live() || !visible || epoch !== browse.epoch) return;
      browse.groups = data.groups || [];
      if (data.truncated) state.error = gt("annotation.groupsTruncated");
      const group = browse.group || browse.groups[0]?.name;
      if (group) await readPage(0, true, group);
      await translation.load();
    } catch (error) { if (live()) { state.error = errorText(error); draw(); } }
  }
  function commitDocument(doc) {
    if (doc.bindings.some(b => b.spec.kind === "nl" || ["json", "jsonl"].includes(b.spec.format) || b.spec.mapping.mode === "records")) protectedGroups.add(doc.group);
    editor.document = doc; editor.candidateId = doc.documentId; editor.phase = "clean"; editor.error = ""; editor.fieldIndex = 0; editor.segmentOffset = 0;
    const matching = doc.bindings.filter(b => b.itemIds.includes(browse.itemId));
    const current = matching.find(b => b.bindingId === item()?.bindingId) || (matching.length === 1 ? matching[0] : null);
    editor.bindingId = current?.bindingId || null;
    if (current) {
      const spec = current.spec;
      Object.assign(state.binding, { kind: spec.kind, delimiter: spec.delimiter || "comma", mapMode: spec.mapping.mode,
        recordsPointer: spec.mapping.recordsPointer || "", recordIdPointer: spec.mapping.recordIdPointer || "", imagePointer: spec.mapping.imagePointer || "",
        fields: JSON.stringify(spec.fields), recordIds: current.recordIds.join("\n") });
    }
    translation.state.sourceDirty = editor.sourceDraft !== null || editor.fieldDrafts.size > 0;
    translation.clearHighlight();
  }
  async function readDocument(id, switching = false) {
    translation.invalidateView();
    const epoch = ++editor.epoch, pageEpoch = browse.epoch, previousPhase = editor.phase === "loading" ? (dirty() ? "dirty" : editor.document ? "clean" : "closed") : editor.phase;
    editor.phase = "loading"; editor.error = ""; draw();
    const current = () => live() && visible && epoch === editor.epoch && pageEpoch === browse.epoch;
    try {
      const doc = await call("GET", "documents", { documentId: id });
      if (!current()) return false;
      if (switching) { const candidates = editor.candidates; clearEditor(); editor.candidates = candidates; invalidatePlan(); }
      invalidatePlan(); commitDocument(doc); draw(); const committed = context(); await translation.load(); return isCurrent(committed);
    } catch (error) { if (current()) { editor.phase = previousPhase; editor.error = errorText(error); draw(); } return false; }
  }
  async function stageItem(target, snapshotId, current) {
    const data = await call("GET", "candidates", { snapshotId, itemId: target.itemId });
    if (!current()) return null;
    const selected = data.candidates.find(c => c.bindingIds.includes(target.bindingId));
    const doc = selected ? await call("GET", "documents", { documentId: selected.documentId }) : null;
    return current() ? { target, candidates: data.candidates, doc } : null;
  }
  function commitItem(staged) {
    clearEditor(); invalidatePlan(); browse.itemId = staged.target.itemId;
    editor.candidates = staged.candidates; editor.phase = "clean";
    if (staged.doc) commitDocument(staged.doc);
  }
  async function open(itemId, approved = false) {
    if (!approved && !await requestNavigate({ kind: "image", value: itemId })) return false;
    const target = browse.items.find(i => i.itemId === itemId); if (!target) return false;
    translation.invalidateView();
    const epoch = ++editor.epoch, pageEpoch = ++browse.epoch, previousPhase = editor.phase === "loading" ? (dirty() ? "dirty" : editor.document ? "clean" : "closed") : editor.phase;
    editor.phase = "loading"; editor.error = ""; browse.phase = "ready"; draw();
    const current = () => live() && visible && epoch === editor.epoch && pageEpoch === browse.epoch;
    try {
      const staged = await stageItem(target, browse.snapshotId, current);
      if (!staged) return false;
      commitItem(staged); draw(); if (staged.doc) await translation.load(); return true;
    } catch (error) { if (current()) { editor.error = errorText(error); editor.phase = previousPhase; draw(); } return false; }
  }
  function requestNavigate(intent) {
    if (active()) return Promise.resolve(false);
    if (!dirty()) return Promise.resolve(true);
    if (pendingNavigate) return Promise.resolve(false);
    state.gate = true; state.intent = intent; draw();
    return new Promise(resolve => { pendingNavigate = resolve; });
  }
  function resolveGate(allowed) {
    state.gate = false; state.gateSaving = false;
    const resolve = pendingNavigate; pendingNavigate = null; draw(); resolve?.(allowed);
  }
  async function navigate(direction) {
    if (browse.phase === "loading" || !await requestNavigate({ kind: "image", value: String(direction) })) return;
    if (browse.itemId) {
      const index = browse.items.findIndex(i => i.itemId === browse.itemId), absolute = browse.offset + index + direction;
      if (absolute < 0 || absolute >= browse.total) return;
      const next = browse.items[index + direction];
      if (next) await open(next.itemId, true);
      else await readPage(browse.offset + direction * 60, false, browse.group, browse.filters, direction);
    } else {
      const offset = browse.offset + direction * 60;
      if (offset >= 0 && offset < browse.total) await readPage(offset);
    }
  }
  function sourceOperation() {
    const doc = editor.document;
    if (editor.draftBlocked) throw Error(gt("annotation.draftMappingChanged"));
    if (editor.raw) {
      let rawText = editor.sourceDraft ?? doc.rawText;
      if (editor.sourceDraft !== null && doc.newline === "crlf") rawText = rawText.replace(/\r\n|\r|\n/g, "\r\n");
      else if (editor.sourceDraft !== null && doc.newline === "cr") rawText = rawText.replace(/\r\n|\r|\n/g, "\r");
      return { operation: { op: "whole_document", rawText }, selections: selections(true) };
    }
    const list = fields(), changed = [...editor.fieldDrafts];
    if (changed.length === 1) {
      const [index, value] = changed[0], f = list[index];
      return { operation: f.kind === "tags" ? { op: "tag_set", tokens: value } : { op: "whole_text", text: value },
        selections: [{ itemId: browse.itemId, bindingId: editor.bindingId, recordIds: [f.recordId], pointers: [f.pointer] }] };
    }
    if (!changed.length) throw Error(gt("annotation.noChanges"));
    if (!["json", "jsonl"].includes(doc.format)) throw Error(gt("annotation.oneField"));
    let rawText = doc.rawText;
    const patches = changed.map(([index, value]) => {
      const f = list[index];
      const decoded = f.kind === "tags" && f.type === "string" ? value.join(f.delimiter === "space" ? " " : ", ") : value;
      return { ...f.rawRange, value: JSON.stringify(decoded) };
    }).sort((a, b) => b.start - a.start);
    for (const p of patches) rawText = rawText.slice(0, p.start) + p.value + rawText.slice(p.end);
    return { operation: { op: "whole_document", rawText }, selections: selections(true) };
  }
  function batchOperation() {
    const op = batch.operation;
    if (["tag_add", "tag_remove"].includes(op)) return { op, tokens: batch.tokens.split(/\r?\n/).filter(Boolean) };
    if (["tag_replace", "nl_replace"].includes(op)) return { op, match: batch.match, replacement: batch.replacement };
    if (op === "nl_delete") return { op, match: batch.match };
    if (["nl_prepend", "nl_append"].includes(op)) return { op, text: batch.text, separator: batch.separator };
    if (op === "normalize_newlines") return { op, newline: batch.newline };
    return { op };
  }
  async function preview(operation, single = false, extra = {}) {
    if (active()) return false;
    mutation = true; state.error = ""; const c = context();
    if (single) editor.phase = "previewing"; else batch.phase = "previewing"; draw();
    try {
      const plan = await call("POST", "plans", { scope: scope(single), operation, missingPolicy: single ? "error" : batch.missingPolicy, ...extra });
      if (!isCurrent(c)) return false;
      const rows = await call("GET", "plans/rows", { planId: plan.planId, offset: 0, limit: 60, includeDiff: true });
      if (!isCurrent(c)) return false;
      batch.plan = plan; batch.planId = plan.planId; batch.manifestHash = plan.manifestHash; batch.rows = rows; batch.results = null;
      batch.phase = "ready"; batch.single = single;
      if (single) { editor.plan = plan; editor.phase = "preview_ready"; }
      return true;
    } catch (error) { if (isCurrent(c)) { state.error = errorText(error); if (single) editor.phase = dirty() ? "dirty" : "error"; else batch.phase = "error"; } return false; }
    finally { mutation = false; draw(); }
  }
  async function save() { const data = sourceOperation(); return preview(data.operation, true, { selections: data.selections }); }
  async function apply() {
    const plan = batch.plan;
    if (active() || plan?.status !== "ready") return false;
    const c = context(); mutation = true; batch.phase = "applying"; editor.phase = "applying"; draw();
    try {
      const data = await call("POST", "plans/apply", { planId: plan.planId, manifestHash: plan.manifestHash, confirm: true });
      if (!isCurrent(c) || batch.plan !== plan) return false;
      editor.job = data.job; plan.status = "applying"; state.jobGroup = browse.group;
      mutation = false; await poll(); return true;
    } catch (error) { if (isCurrent(c) && batch.plan === plan) { editor.phase = error.status === 409 ? "conflict" : "error"; batch.phase = "error"; state.error = errorText(error); } return false; }
    finally { mutation = false; draw(); }
  }
  async function poll() {
    if (!live() || polling || !editor.job) return;
    polling = true; clearTimeout(timer); const c = context(), jobId = editor.job.jobId;
    try {
      const job = await call("GET", "jobs", { jobId });
      if (!live() || editor.job?.jobId !== jobId) return;
      editor.job = job;
      await scopedRead("results", "GET", "jobs/rows", { jobId, offset: batch.results?.offset || 0, limit: 60 }, () => editor.job?.jobId === jobId, result => { batch.results = result; });
      if (!isCurrent(c) || editor.job?.jobId !== jobId) return;
      if (!ACTIVE_JOBS.has(job.status)) {
        batch.phase = "done"; if (batch.plan) batch.plan.status = "consumed";
        let conflict = job.failed > 0 || job.skipped > 0 || job.status !== "done";
        if (conflict) { editor.phase = "conflict"; state.error = gt("annotation.conflict"); }
        else {
          const refreshEpoch = browse.epoch + 1, refreshEditorEpoch = editor.epoch;
          const refreshed = visible && browse.group === state.jobGroup && await readPage(browse.offset, true, browse.group, browse.filters, "current");
          if (!live() || editor.job?.jobId !== jobId) return;
          if (!refreshed && (!visible || browse.epoch !== refreshEpoch || editor.epoch !== refreshEditorEpoch)) return;
          if (refreshed) {
            editor.sourceDraft = null; editor.fieldDrafts.clear(); translation.state.sourceDirty = false;
            editor.phase = editor.document ? "clean" : "closed";
          } else { conflict = true; editor.phase = "conflict"; }
          // Never clear manual drafts here: old-generation drafts stay orphaned,
          // and the translation service alone owns stored manual/history records.
        }
        if (state.gateSaving) { if (!conflict && !dirty()) resolveGate(true); else { state.gateSaving = false; } }
        jobResolve?.(!conflict); jobResolve = null;
      }
      draw();
    } catch (error) { if (isCurrent(c) && editor.job?.jobId === jobId) { state.error = errorText(error); draw(); } }
    finally { polling = false; if (live() && ACTIVE_JOBS.has(editor.job?.status)) timer = setTimeout(poll, 2000); }
  }
  function spec(format, creation = false) {
    const form = creation ? state.create : state.binding, structured = ["json", "jsonl"].includes(format), kind = form.kind;
    if (!structured) return { format, kind, delimiter: kind === "tags" ? form.delimiter : null, mapping: { mode: "sidecar" }, fields: [{ pointer: "", type: "string" }] };
    const fs = creation ? [{ pointer: kind === "tags" ? "/tags" : "/caption", type: kind === "tags" ? "string_array" : "string" }] : JSON.parse(form.fields);
    return { format, kind, delimiter: kind === "nl" || fs.every(f => f.type === "string_array") ? null : form.delimiter,
      mapping: creation || form.mapMode === "sidecar" ? { mode: "sidecar", recordsPointer: "", recordIdPointer: null, imagePointer: null } : {
        mode: "records", recordsPointer: format === "jsonl" ? "" : form.recordsPointer, recordIdPointer: form.recordIdPointer || null, imagePointer: form.imagePointer }, fields: fs };
  }
  async function confirmKind() {
    if (active() || !editor.document || !await requestNavigate({ kind: "image", value: editor.candidateId })) return;
    const c = context(); mutation = true; draw();
    try {
      await call("POST", "bindings/confirm", { snapshotId: browse.snapshotId, itemId: browse.itemId, documentId: editor.document.documentId,
        expectedVersion: editor.document.sourceVersion, spec: spec(editor.document.format), ...(state.binding.recordIds.trim() ? { recordIds: state.binding.recordIds.trim().split(/\s+/) } : {}) });
      if (!isCurrent(c)) return;
      if (await readPage(browse.offset, true, browse.group, browse.filters, "current")) discardDrafts();
    } catch (error) { if (isCurrent(c)) throw error; }
    finally { mutation = false; draw(); }
  }
  function capture(node) {
    if (translation.capture(node)) { if (node.dataset.field?.startsWith("manual:")) cancelPendingRead(); return true; }
    if (node.dataset.scope !== "annotation") return false;
    const name = node.dataset.field, value = node.type === "checkbox" ? node.checked : node.value;
    if (name === "search") { clearTimeout(searchTimer); searchTimer = setTimeout(() => change(node), 300); invalidate(); return true; }
    if (name === "source") { editor.fieldDrafts.set(editor.fieldIndex, value); markDirty(); }
    else if (name.startsWith("token:")) {
      const f = fields()[editor.fieldIndex], tokens = [...(editor.fieldDrafts.get(editor.fieldIndex) ?? f.segments.map(s => s.text))];
      tokens[Number(name.slice(6))] = value; editor.fieldDrafts.set(editor.fieldIndex, tokens); markDirty();
    } else if (name === "rawText") { editor.sourceDraft = value; markDirty(); }
    else if (name.startsWith("batch:")) { batch[name.slice(6)] = value; invalidatePlan(); }
    else if (name.startsWith("binding:")) state.binding[name.slice(8)] = value;
    else if (name.startsWith("create:")) state.create[name.slice(7)] = value;
    else if (name === "sharedPath") state.sharedPath = value;
    invalidate();
    return true;
  }
  async function change(node) {
    // The password input never enters controller state, so a blur-triggered
    // render would replace it with an empty node before "Save key" can read it.
    if (node.dataset.scope === "translation") {
      if (node.dataset.field === "apiKey") return true;
      invalidate(); translation.capture(node); draw(); return true;
    }
    if (node.dataset.scope !== "annotation") return false;
    invalidate();
    const name = node.dataset.field;
    if (["group", "format", "status", "search"].includes(name)) {
      clearTimeout(searchTimer);
      if (!await requestNavigate({ kind: "group", value: node.value })) { node.value = name === "group" ? browse.group : browse.filters[name]; return true; }
      const filters = { ...browse.filters, ...(name !== "group" ? { [name]: node.value } : {}) };
      if (await readPage(0, true, name === "group" ? node.value : browse.group, filters)) { browse.itemId = null; clearEditor(); draw(); }
    } else if (name.startsWith("selected:")) {
      const id = name.slice(9); if (node.checked) browse.selection.add(id); else browse.selection.delete(id);
      batch.scope = "page_selected"; invalidatePlan(); draw();
    } else if (name === "file") {
      if (await requestNavigate({ kind: "image", value: node.value })) {
        const id = node.value; if (id) await readDocument(id, true); else { clearEditor(); invalidatePlan(); draw(); }
      } else node.value = editor.candidateId;
    } else if (name === "bindingId") {
      if (await requestNavigate({kind: "image", value: node.value})) { discardDrafts(); editor.epoch++; editor.bindingId = node.value || null; editor.fieldIndex = 0; editor.segmentOffset = 0; translation.clearHighlight(); draw(); }
    } else if (name === "fieldIndex") { editor.fieldIndex = Number(node.value); editor.segmentOffset = 0; invalidatePlan(); translation.clearHighlight(); draw(); }
    else if (name === "display") { editor.mode = node.value; translation.clearHighlight(); draw(); }
    else if (name === "raw") {
      if (await requestNavigate({ kind: "image", value: "raw" })) { discardDrafts(); editor.raw = node.checked; draw(); } else node.checked = editor.raw;
    } else { capture(node); draw(); }
    return true;
  }
  async function action(act, target = {}) {
    if (act.startsWith("translation-")) {
      try { return await translation.action(act, target, ["translation-plan", "translation-missing-api"].includes(act) ? scope(Boolean(browse.itemId)) : null, browse.itemId && binding() ? { selections: selections(), segmentIds: segmentWindow().segments.map(s => s.segmentId) } : {}); }
      catch (error) { state.error = errorText(error); draw(); return true; }
    }
    if (act.startsWith("library-") && ["library-next", "library-prev", "library-back", "library-refresh"].includes(act)) {
      if (act === "library-next" || act === "library-prev") await navigate(act === "library-next" ? 1 : -1);
      else if (await requestNavigate({ kind: act === "library-refresh" ? "group" : "back", value: "" })) {
        if (act === "library-back") { browse.epoch++; browse.phase = "ready"; browse.itemId = null; clearEditor(); invalidatePlan(); draw(); }
        else if (await readPage(browse.offset, true, browse.group, browse.filters, "current")) { discardDrafts(); draw(); }
      }
      return true;
    }
    if (!act.startsWith("annotation-")) return false;
    try {
      if (active() && !["annotation-job-stop", "annotation-result-page"].includes(act)) return true;
      if (act === "annotation-open") await open(target.dataset.itemId);
      else if (act === "annotation-retag-focus") { root.querySelector("#annotation-retag")?.scrollIntoView?.({block: "nearest"}); }
      else if (act === "annotation-segment-page") { editor.segmentOffset = Math.max(0, Number(target.dataset.offset)); translation.clearHighlight(); draw(); }
      else if (act === "annotation-toggle-image") { editor.imageCollapsed = !editor.imageCollapsed; draw(); }
      else if (act === "annotation-page-all") { batch.scope = "page_all"; for (const i of browse.items) browse.selection.add(i.itemId); invalidatePlan(); draw(); }
      else if (act === "annotation-all-filtered") { batch.scope = "all_filtered"; browse.selection.clear(); invalidatePlan(); draw(); }
      else if (act === "annotation-select-none") { browse.selection.clear(); batch.scope = "page_selected"; invalidatePlan(); draw(); }
      else if (act === "annotation-batch-open") { batch.phase = "editing"; draw(); }
      else if (act === "annotation-batch-preview") await preview(batchOperation());
      else if (act === "annotation-save") await save();
      else if (act === "annotation-cancel-preview") { invalidatePlan(); editor.phase = dirty() ? "dirty" : "clean"; state.gateSaving = false; draw(); }
      else if (act === "annotation-apply" || act === "annotation-batch-apply") await apply();
      else if (act === "annotation-confirm-kind") await confirmKind();
      else if (act === "annotation-select-file") {
        const b = binding(); if (!b) throw Error(gt("annotation.confirmRequired"));
        if (!await requestNavigate({ kind: "image", value: b.bindingId })) return true;
        const c = context(); mutation = true;
        try {
          await call("POST", "bindings/select", { snapshotId: browse.snapshotId, itemId: browse.itemId, bindingId: b.bindingId, expectedBindingRevision: b.bindingRevision });
          if (!isCurrent(c)) return true;
          if (await readPage(browse.offset, true, browse.group, browse.filters, "current")) discardDrafts();
        } catch (error) { if (isCurrent(c)) throw error; }
        finally { mutation = false; draw(); }
      } else if (act === "annotation-shared") {
        const sharedPath = state.sharedPath;
        await scopedRead("shared", "GET", "candidates", { snapshotId: browse.snapshotId, itemId: browse.itemId, sharedPath }, () => state.sharedPath === sharedPath, result => { editor.candidates = result.candidates; });
      } else if (act === "annotation-create") {
        const form = state.create, bindingSpec = spec(form.format, true);
        await preview({ op: "create", itemId: browse.itemId, path: form.path, spec: bindingSpec,
          initialValue: bindingSpec.fields[0].type === "string_array" ? form.value.split(/\r?\n/).filter(Boolean) : form.value }, true);
      } else if (act === "annotation-clear") await preview({ op: "clear" }, true, { selections: selections() });
      else if (act === "annotation-delete") {
        const b = binding(); if (!b || b.spec.mapping.mode !== "sidecar" || b.itemIds.length !== 1 || editor.document.bindings.length !== 1 || b.recordIds.length !== 1) throw Error(gt("annotation.sharedDelete"));
        await preview({ op: "delete_file", confirmDelete: true }, true, { selections: selections(true) });
      } else if (act === "annotation-undo") {
        const c = context(), jobId = editor.job?.jobId, oldPlan = batch.plan;
        const serial = (requestSerials.get("undo") || 0) + 1; requestSerials.set("undo", serial);
        const current = () => isCurrent(c) && editor.job?.jobId === jobId && oldPlan === batch.plan && serial === requestSerials.get("undo");
        try {
          const plan = await call("POST", "undo/plans", { jobId });
          if (!current()) return true;
          const rows = await call("GET", "plans/rows", { planId: plan.planId, offset: 0, limit: 60, includeDiff: true });
          if (!current()) return true;
          batch.plan = plan; batch.planId = plan.planId; batch.manifestHash = plan.manifestHash;
          batch.rows = rows; batch.phase = "ready"; draw();
        } catch (error) { if (current()) throw error; }
      } else if (act === "annotation-plan-page") {
        const plan = batch.plan; if (!plan) return true;
        await scopedRead("rows", "GET", "plans/rows", { planId: plan.planId, offset: Number(target.dataset.offset), limit: 60, includeDiff: true }, () => batch.plan === plan, result => { batch.rows = result; });
      } else if (act === "annotation-result-page") {
        const jobId = editor.job?.jobId; if (!jobId) return true;
        await scopedRead("results", "GET", "jobs/rows", { jobId, offset: Number(target.dataset.offset), limit: 60 }, () => editor.job?.jobId === jobId, result => { batch.results = result; });
      } else if (act === "annotation-job-stop") {
        const job = editor.job; if (!job) return true;
        await scopedRead("stop", "POST", "jobs/stop", { jobId: job.jobId }, () => editor.job === job, result => { editor.job = result.job; });
      } else if (act === "annotation-token-add" || act === "annotation-token-remove") {
        const f = fields()[editor.fieldIndex], tokens = [...(editor.fieldDrafts.get(editor.fieldIndex) ?? f.segments.map(s => s.text))];
        if (act.endsWith("add")) { tokens.push(""); editor.segmentOffset = Math.floor((tokens.length - 1) / 60) * 60; } else tokens.splice(Number(target.dataset.index), 1);
        editor.fieldDrafts.set(editor.fieldIndex, tokens); markDirty(); draw();
      } else if (act === "annotation-discard") {
        editor.sourceDraft = null; editor.fieldDrafts.clear(); editor.draftBlocked = false; editor.phase = "clean"; invalidatePlan(); translation.state.sourceDirty = false; translation.clearHighlight(); draw();
      } else if (act === "annotation-reload") {
        // Preserve the draft; new source is explicit and requires another preview.
        const raw = editor.sourceDraft, drafts = new Map(editor.fieldDrafts), previousFields = fields();
        if (await readDocument(editor.candidateId)) {
          const currentFields = fields(), remapped = new Map();
          editor.draftBlocked = false;
          for (const [index, value] of drafts) {
            const old = previousFields[index], nextIndex = currentFields.findIndex(f => f.recordId === old.recordId && f.pointer === old.pointer && f.type === old.type && f.kind === old.kind);
            if (nextIndex < 0) editor.draftBlocked = true; else remapped.set(nextIndex, value);
          }
          editor.sourceDraft = raw; editor.fieldDrafts = editor.draftBlocked ? drafts : remapped;
          if (editor.draftBlocked) editor.error = gt("annotation.draftMappingChanged");
          if (raw !== null || drafts.size) markDirty(); draw();
        }
      } else if (act === "annotation-gate-cancel") resolveGate(false);
      else if (act === "annotation-gate-discard") {
        // A failed image/file/group read must retain even explicitly discarded drafts.
        // The successful navigation commit owns their disposal.
        if (!["image", "group"].includes(state.intent?.kind)) discardDrafts();
        resolveGate(true);
      }
      else if (act === "annotation-gate-save") {
        state.gateSaving = true;
        if (translation.state.drafts.size) {
          const wasDirty = translation.state.sourceDirty; translation.state.sourceDirty = false;
          const saved = await translation.saveManual(); translation.state.sourceDirty = wasDirty;
          if (!saved) { state.gateSaving = false; draw(); return true; }
        }
        if (editor.sourceDraft !== null || editor.fieldDrafts.size) { if (!await save()) state.gateSaving = false; }
        else resolveGate(true);
      }
    } catch (error) { if (live()) { state.error = errorText(error); if (error.status === 409) editor.phase = "conflict"; draw(); } }
    return true;
  }
  function sourcePane() {
    const doc = editor.document, list = fields(), f = list[editor.fieldIndex];
    if (!doc) return `<p>${tr("chooseCandidate")}</p>`;
    const window = segmentWindow();
    const ranges = translation.state.sourceDirty || (!editor.raw && !["json", "jsonl"].includes(doc.format)) ? [] : window.segments.flatMap(segment => segment.rawRanges.map(range => ({ ...range, id: segment.segmentId }))).sort((a, b) => a.start - b.start);
    let rawMarkup = "", cursor = 0;
    for (const range of ranges) {
      if (range.start < cursor) continue;
      rawMarkup += esc(doc.rawText.slice(cursor, range.start)) + `<span tabindex="0" data-segment-id="${esc(range.id)}" data-side="source">${esc(doc.rawText.slice(range.start, range.end))}</span>`; cursor = range.end;
    }
    rawMarkup += esc(doc.rawText.slice(cursor));
    const raw = `<details data-details="annotation-raw"><summary>${tr("rawContext")}</summary><p class="hint">${tr("rawHint")}</p><pre class="code" data-annotation-raw-context>${rawMarkup}</pre></details>`;
    if (doc.readOnly || !binding() || binding().status !== "confirmed") return `<p role="status">${esc(doc.readOnlyReason || "SCHEMA_REQUIRED")}</p>${raw}`;
    const values = editor.fieldDrafts.get(editor.fieldIndex) ?? (f?.kind === "tags" ? f.segments.map(s => s.text) : f?.value);
    return `<div class="annotation-source"><h3>${tr("source")}</h3><p data-annotation-dirty>${tr(editor.sourceDraft !== null || editor.fieldDrafts.size ? "dirty" : "clean")}</p>

      ${["json", "jsonl"].includes(doc.format) ? `<label><input type="checkbox" data-scope="annotation" data-field="raw" ${editor.raw ? "checked" : ""}> ${tr("rawEdit")}</label><p class="hint">${tr("rawHint")}</p>` : ""}
      ${editor.raw ? text("rawText", editor.sourceDraft ?? doc.rawText) : f?.kind === "tags" ? `<div class="annotation-tokens">${(values || []).slice(window.offset, window.offset + 60).map((value, localIndex) => { const index = window.offset + localIndex; return `<div><input class="field" data-scope="annotation" data-field="token:${index}" value="${esc(value)}" aria-label="${tr("token")}" ${!translation.state.sourceDirty && f.segments[index] ? `data-segment-id="${esc(f.segments[index].segmentId)}" data-side="source"` : ""}>${button("token-remove", "removeToken", false, `data-index="${index}"`)}</div>`; }).join("")}</div>${button("token-add", "addToken")}` : `<label>${tr("source")}<textarea class="field" data-scope="annotation" data-field="source" data-record-id="${esc(f?.recordId || "")}" data-pointer="${esc(f?.pointer || "")}">${esc(values ?? "")}</textarea></label>`}
      ${!translation.state.sourceDirty && !editor.raw && f && f.kind !== "tags" ? `<div class="annotation-segments">${window.segments.map(s => `<p tabindex="0" data-segment-id="${esc(s.segmentId)}" data-side="source">${esc(s.text)}</p>`).join("")}</div>` : ""}
      ${raw}${button("save", "preview", active())}${button("discard", "discard", active())}${button("reload", "reload", active())}</div>`;
  }
  function pager(data, act) {
    return data ? `<div class="acts">${button(act, "previous", data.offset === 0, `data-offset="${Math.max(0, data.offset - 60)}"`)}<span>${data.offset + 1}–${Math.min(data.offset + 60, data.total)} / ${data.total}</span>${button(act, "next", data.offset + 60 >= data.total, `data-offset="${data.offset + 60}"`)}</div>` : "";
  }
  function planPane() {
    const p = batch.plan, job = editor.job;
    return `${p ? `<section class="box"><h3>${tr("diff")} · ${tr(`status.${p.status}`)}</h3><p>${Object.entries(p.summary).map(([key, value]) => `${tr(`summary.${key}`)} ${value}`).join(" · ")}</p>${(batch.rows?.rows || []).map(row => `<article><h4>${esc(row.path || row.itemIds.join(", "))} · ${tr(`status.${row.status}`)} ${esc(row.code || "")}</h4><p>${esc(row.itemIds.join(", "))}</p><div class="annotation-columns"><pre class="code">${esc(row.diff?.before ?? "")}</pre><pre class="code">${esc(row.diff?.after ?? "")}</pre></div></article>`).join("")}${pager(batch.rows, "plan-page")}<p class="hint">${tr("writeWarning")}</p>${button("apply", "apply", active() || p.status !== "ready")}${button("cancel-preview", "cancelPreview", active())}</section>` : ""}
      ${job ? `<section class="box" role="status"><p>${esc(job.jobId)} · ${tr(`status.${job.status}`)} · ${tr("processed")} ${job.processed}/${job.total} · ${tr("written")} ${job.written} · ${tr("skipped")} ${job.skipped} · ${tr("failed")} ${job.failed}</p>${job.error ? `<p role="alert">${esc(job.error.code)} ${esc(job.error.message)}</p>` : ""}${button("job-stop", "stop", !ACTIVE_JOBS.has(job.status) || job.status === "stopping")}${button("undo", "undo", active() || !job.undoAvailable)}${(batch.results?.rows || []).map(row => `<p>${esc(row.documentId || "")} · ${tr(`status.${row.status}`)} ${esc(row.code || "")} · ${esc(row.backupId || "")}</p>`).join("")}${pager(batch.results, "result-page")}</section>` : ""}`;
  }
  function pane() {
    const segmentPage = segmentWindow(); translation.setWindow(segmentPage);
    const current = item(), index = browse.items.findIndex(i => i.itemId === browse.itemId);
    const groupOptions = browse.groups.map(g => `<option value="${esc(g.name)}" ${g.name === browse.group ? "selected" : ""}>${esc(g.name)}</option>`).join("");
    const controls = `<div class="annotation-filters"><label>${tr("group")}<select class="field" data-scope="annotation" data-field="group">${groupOptions}</select></label>${select("format", browse.filters.format, ["any", "txt", "caption", "json", "jsonl"])}${select("status", browse.filters.status, ["any", "missing", "unconfirmed", "confirmed", "stale", "invalid", "ambiguous"])}${input("search", browse.filters.search)}</div>`;
    const selectedHere = browse.items.filter(i => browse.selection.has(i.itemId)).length;
    const scopeCount = batch.scope === "all_filtered" ? browse.total : batch.scope === "page_all" ? browse.items.length : selectedHere;
    const toolbar = `<div class="acts">${button("page-all", "pageAll", active())}${button("all-filtered", "allFiltered", active() || browse.truncated)}${button("select-none", "selectNone", active())}<span>${tr("selected")} ${scopeCount} · ${tr("filteredTotal")} ${browse.total} · ${tr("physical")} ${batch.plan?.summary.documents ?? "—"} · ${tr("rememberedSelection")} ${browse.selection.size} · ${tr("selectedHere")} ${selectedHere}</span></div><p>${tr(`option.${batch.scope}`)} · ${tr("pageScopeHint")} · ${tr("snapshot")} ${esc(browse.createdAt || "")} ${browse.truncated ? tr("incomplete") : ""}</p><div class="acts">${button("batch-open", "batchEdit", active() || browse.truncated)}<button class="btn" type="button" data-act="translation-plan">${tr("translate")}</button>${button("retag-focus", "retagGroup")}<details data-details="annotation-more"><summary>${tr("more")}</summary>${button("select-none", "selectNone")}<button class="btn" type="button" data-act="library-refresh">${tr("refresh")}</button></details></div>`;
    const batchPane = batch.phase === "closed" ? "" : `<section class="box">${select("batch:scope", batch.scope, ["page_selected", "page_all", "all_filtered"])}${select("batch:operation", batch.operation, OPERATIONS)}${select("batch:missingPolicy", batch.missingPolicy, ["skip", "error"])}${input("batch:match", batch.match)}${input("batch:replacement", batch.replacement)}${text("batch:tokens", batch.tokens)}${text("batch:text", batch.text)}${select("batch:separator", batch.separator, ["", " ", "\n"])}${select("batch:newline", batch.newline, ["lf", "crlf"])}${button("batch-preview", "preview", active() || browse.truncated)}</section>`;
    const navigation = `<div class="acts"><button type="button" class="btn" data-act="library-prev" ${browse.phase === "loading" || (current ? browse.offset + index === 0 : browse.offset === 0) ? "disabled" : ""}>${tr(current ? "previousImage" : "previous")}</button><span>${current ? browse.offset + index + 1 : `${browse.total ? browse.offset + 1 : 0}–${browse.offset + browse.items.length}`} / ${browse.total}</span><button type="button" class="btn" data-act="library-next" ${browse.phase === "loading" || (current ? browse.offset + index + 1 >= browse.total : browse.offset + 60 >= browse.total) ? "disabled" : ""}>${tr(current ? "nextImage" : "next")}</button></div>`;
    const status = `${browse.phase === "loading" || editor.phase === "loading" ? `<p role="status">${tr("loading")}</p>` : ""}${state.error || editor.error ? `<p role="alert">${esc(state.error || editor.error)}</p>` : ""}`;
    let detail = "";
    if (current) {
      const b = state.binding, c = state.create;
      detail = `<div class="grabber-detail"><button type="button" class="btn" data-act="library-back">${tr("back")}</button>${button("toggle-image", editor.imageCollapsed ? "showImage" : "hideImage")}<h3>${esc(current.imagePath)}</h3><div ${editor.imageCollapsed ? "hidden" : ""}>${media.markup(current.imageUrl, "detail", current.imagePath)}</div>${media.original(current.imageUrl)}
      <label>${tr("candidate")}<select class="field" data-scope="annotation" data-field="file"><option value="">${tr("chooseCandidate")}</option>${editor.candidates.map(candidate => `<option value="${esc(candidate.documentId)}" ${candidate.documentId === editor.candidateId ? "selected" : ""}>${esc(candidate.path)} · ${esc(candidate.format)} · ${esc(candidate.status)} · ${esc(candidate.sourceVersion)}</option>`).join("")}</select></label>
      ${editor.document ? `<label>${tr("binding")}<select class="field" data-scope="annotation" data-field="bindingId"><option value="">${tr("confirmRequired")}</option>${editor.document.bindings.filter(b => b.itemIds.includes(browse.itemId)).map(b => `<option value="${esc(b.bindingId)}" ${b.bindingId === editor.bindingId ? "selected" : ""}>${esc(b.bindingId)} · ${b.bindingRevision} · ${esc(b.spec.kind)} · ${b.recordIds.length}</option>`).join("")}</select></label>` : ""}
      <details data-details="annotation-binding"><summary>${tr("binding")}</summary>${input("sharedPath", state.sharedPath)}${button("shared", "findShared")}${select("binding:kind", b.kind, ["tags", "nl"])}${select("binding:delimiter", b.delimiter, ["comma", "space"])}${select("binding:mapMode", b.mapMode, ["sidecar", "records"])}${input("binding:recordsPointer", b.recordsPointer)}${input("binding:recordIdPointer", b.recordIdPointer)}${input("binding:imagePointer", b.imagePointer)}${text("binding:fields", b.fields)}${text("binding:recordIds", b.recordIds)}<p class="hint">${tr("mappingHint")}</p>${button("confirm-kind", "confirmKind", active() || !editor.document)}${button("select-file", "selectFile", active() || !binding())}</details>
      <details data-details="annotation-create"><summary>${tr("create")}</summary>${input("create:path", c.path)}${select("create:format", c.format, ["txt", "caption", "json", "jsonl"])}${select("create:kind", c.kind, ["tags", "nl"])}${select("create:delimiter", c.delimiter, ["comma", "space"])}${text("create:value", c.value)}${button("create", "previewCreate", active())}</details>
      <div class="acts">${button("clear", "clear", active() || !binding())}${button("delete", "delete", active() || !binding())}${select("display", editor.mode, ["both", "source", "translation"])}</div>
      ${editor.document && binding() ? `      <label>${tr("field")}<select class="field" data-scope="annotation" data-field="fieldIndex">${fields().map((field, index) => `<option value="${index}" ${index === editor.fieldIndex ? "selected" : ""}>${esc(field.recordId)} · ${esc(field.pointer || "/")} · ${esc(field.kind)}</option>`).join("")}</select></label>
      <p class="hint">${tr("segmentPageHint")}</p>${pager(segmentPage, "segment-page")}` : ""}
      <div class="annotation-columns" data-mode="${editor.mode}">${editor.mode !== "translation" ? sourcePane() : ""}${editor.mode !== "source" ? translation.pane() : ""}</div></div>`;
    }
    const grid = `<div class="gallery">${browse.items.map(i => `<div class="shot"><label><input type="checkbox" data-scope="annotation" data-field="selected:${esc(i.itemId)}" ${browse.selection.has(i.itemId) ? "checked" : ""} aria-label="${esc(i.imagePath)}"></label><button type="button" class="shot" data-act="annotation-open" data-item-id="${esc(i.itemId)}">${media.markup(i.imageUrl, "thumb", i.imagePath)}<span class="cap">${esc(i.imagePath)} · ${tr(`option.${i.annotationStatus}`)}</span></button></div>`).join("")}</div>`;
    return controls + (current ? detail : toolbar + batchPane + status + grid) + navigation + (current ? status : "") + planPane() + (!current ? translation.pane() : "") +
      (state.gate ? `<section class="box annotation-navigation" role="dialog" aria-modal="true" aria-label="${tr("unsaved")}"><h3>${tr("unsaved")}</h3><p>${tr("saveBeforeLeave")}</p>${button("gate-save", "saveLeave", state.gateSaving)}${button("gate-discard", "discardLeave", state.gateSaving)}${button("gate-cancel", "cancel", active())}</section>` : "");
  }
  function beforeUnload(event) { if (dirty() || active()) { event.preventDefault(); event.returnValue = ""; } }
  globalThis.window?.addEventListener("beforeunload", beforeUnload);
  return { state, pane, open, requestNavigate, capture, change, action, enter, active, dirty, translation, scope,
    retagProtected: () => protectedGroups.has(browse.group),
    refresh: () => action("library-refresh"),
    leave() { visible = false; browse.epoch++; editor.epoch++; batch.epoch++; clearTimeout(searchTimer); translation.leave(); browse.phase = "ready"; if (editor.phase === "loading") editor.phase = dirty() ? "dirty" : editor.document ? "clean" : "closed"; },
    dispose() { disposed = true; visible = false; browse.epoch++; editor.epoch++; batch.epoch++; clearTimeout(timer); clearTimeout(searchTimer);
      pendingNavigate?.(false); pendingNavigate = null; jobResolve?.(false); jobResolve = null; pages.clear(); protectedGroups.clear(); requestSerials.clear(); editor.job = null; editor.candidates = []; batch.plan = null; batch.rows = null; batch.results = null; editor.plan = null; editor.sourceDraft = null; browse.items = []; browse.groups = []; browse.selection.clear(); editor.document = null; editor.fieldDrafts.clear(); translation.dispose(); globalThis.window?.removeEventListener("beforeunload", beforeUnload); } };
}
