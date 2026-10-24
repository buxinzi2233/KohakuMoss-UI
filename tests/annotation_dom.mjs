/* Real grabberView, real media/controller DOM, fake wire only. No browser/API IO. */
import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { installDOM } from "./grabber_dom.mjs";
import { id, hash, docs, candidates, images, largeDoc, manyRecords } from "./annotation_fixture.mjs";
const web = new URL("../src/kohakumoss_ui/web/", import.meta.url);
const tmp = await mkdtemp(join(tmpdir(), "annotation-dom-"));
const stats = { rootWrites: 0, removed: 0, imageSources: [] };
const dom = installDOM(stats), timers = new Map(); let timerId = 0;
globalThis.setTimeout = fn => { timers.set(++timerId, fn); return timerId; };
globalThis.clearTimeout = id => timers.delete(id);
const windowListeners = new Map();
globalThis.window = { addEventListener(type, fn) { windowListeners.set(type, fn); }, removeEventListener(type) { windowListeners.delete(type); } };
globalThis.location = { hash: "#/grabber", search: "" };
globalThis.history = { replaceState(_state, _title, hash) { location.hash = hash; } };
const dictionary = JSON.parse(await readFile(new URL("lang/en.json", web), "utf8"));
const missingKeys = new Set();
let root, view; const main = { scrollTop: 0, replaceChildren(node) { root = node; dom.mount(node); } };
stats.paneReplaced = (host, hadDetail) => { if (hadDetail && host.querySelector(".grabber-media-detail")) main.scrollTop = Math.max(0, main.scrollTop - 50); };
assert.ok(largeDoc.bytes<65536 && manyRecords.bytes<65536);
let delayTranslationPlan=false, pendingTranslationPlan=null;
const requests = [], snapshots = new Map(), plans = new Map(), jobs = new Map();
let serial = 100, failPlan = false, applyConflict = false, timeoutApply = false, delayPage = null, pendingPage = null, delayDocument = null, pendingDocument = null;
let activeSnapshot, manualRevision = 4, remoteFail = false, manyPlanRows = false, mutateSource = false;
const failures = new Map(); let hold = null;
function holdNext(domain, method, route, matches = () => true) {
  let release; const ready = new Promise(resolve => { release = resolve; });
  const pending = { domain, method, route, matches, ready, release, reached: false };
  hold = pending; return pending;
}
const translationViews = new Map();
let settings = { configVersion: null, endpoint: "", model: "", sourceLanguage: "en", targetLanguage: "zh-CN", timeoutSeconds: 30, maxOutputTokens: 2048, temperature: 0, concurrency: 2, enabled: false, hasKey: false, keyHint: "" };
const dictionaryState = { status: "ready", dictionaryVersion: `dict_${"a".repeat(64)}`, tableName: "fixture_dictionary", rows: 2, ref: "fixture", repositoryUrl: "https://example.invalid", license: "MIT", error: null };
function viewFor(documentId) {
  const doc = docs.get(documentId);
  const segments = doc.fields.flatMap(f => f.segments).map((s, index) => ({ segmentId:s.segmentId, recordId:s.recordId, pointer:s.pointer, ordinal:s.ordinal, sourceHash:s.sourceHash, sourceVersion:s.sourceVersion, generation:s.generation, sourceText: s.text, targetText: index === 2 ? s.text : `译${index}`, segmentRevision: 1,
    status: index === 2 ? "missing" : "translated", engine: "dictionary", dictionaryVersion: dictionaryState.dictionaryVersion, configVersion: null, model: null,
    sourceLanguage: "en", targetLanguage: "zh-CN", manual: false, staleReason: null, updatedAt: 10, code: index === 2 ? "MISSING_TRANSLATION" : null }));
  return { documentId, sourceLanguage: "en", targetLanguage: "zh-CN", sourceVersion: doc.sourceVersion, generation: doc.generation, revision: manualRevision,
    snapshotRevision: 2, snapshotSourceVersion: doc.sourceVersion, snapshotGeneration: doc.generation, segments, historyCount: 0, history: [] };
}
const clone = value => JSON.parse(JSON.stringify(value));
function job(planId, kind = "apply", status = "done") { return { jobId: id("job", ++serial), planId, kind, status, total: 1, processed: 1, written: applyConflict ? 0 : 1, skipped: applyConflict ? 1 : 0, failed: 0,
  currentDocumentId: null, cancelRequested: false, createdAt: 1, updatedAt: 2, finishedAt: 2, error: null, undoAvailable: kind === "apply" }; }
function page(snap, offset = 0) { return { ...snap, offset, items: images.slice(offset, offset + 60) }; }
function fail(code, status = 409) { return { status, body: { ok: false, error: { code, message: `fixture ${code}`, details: {}, retryable: false } } }; }
async function wire(domain, method, route, payload) {
  requests.push({ domain, method, route, payload: clone(payload) });
  if (method === "POST") assert.match(payload.requestId, /^req_[0-9a-f]{32}$/);
  const failureKey = `${domain}:${method}:${route}`;
  if (failures.has(failureKey)) { const code = failures.get(failureKey); failures.delete(failureKey); return fail(code, code === "UPSTREAM_TIMEOUT" ? 504 : 409); }
  if (domain === "annotation") {
    if (route === "snapshots" && method === "POST") {
      assert.equal(payload.limit, 60); activeSnapshot = { snapshotId: id("snap", ++serial), libraryId: id("lib", 1), group: payload.group, filters: payload.filters,
        total: 64, limit: 60, truncated: false, createdAt: 100, expiresAt: 1000 }; snapshots.set(activeSnapshot.snapshotId, activeSnapshot); return { status: 201, data: page(activeSnapshot) };
    }
    if (route === "snapshots") {
      assert.equal(Number(payload.limit), 60);
      if (Number(payload.offset) === delayPage) { delayPage = null; return new Promise((resolve, reject) => { pendingPage = { resolve: () => resolve({ data: page(snapshots.get(payload.snapshotId), Number(payload.offset)) }), reject }; }); }
      return { data: page(snapshots.get(payload.snapshotId), Number(payload.offset)) };
    }
    if (route === "candidates") return { data: { itemId: payload.itemId, candidates: candidates.get(payload.itemId) || [] } };
    if (route === "documents") {
      if (payload.documentId === delayDocument) { delayDocument = null; return new Promise(resolve => { pendingDocument = () => resolve({ data: docs.get(payload.documentId) }); }); }
      return { data: docs.get(payload.documentId) };
    }
    if (route === "bindings/confirm") { const doc = docs.get(payload.documentId); doc.bindings[0].spec = payload.spec; return { data: { bindingId: doc.bindings[0].bindingId, bindingRevision: 2, document: doc } }; }
    if (route === "bindings/select") return { data: { itemId: payload.itemId, bindingId: payload.bindingId, bindingRevision: payload.expectedBindingRevision } };
    if (route === "plans" && method === "POST" || route === "undo/plans") {
      if (failPlan) return fail("VERSION_CONFLICT");
      const p = { planId: id("plan", ++serial), libraryId: id("lib", 1), requestId: payload.requestId, kind: route === "undo/plans" ? "undo" : "edit", status: "ready",
        scope: payload.scope || null, operation: payload.operation || { op: "restore_job", jobId: payload.jobId }, summary: { images: payload.scope?.mode === "all_filtered" ? 64 : payload.scope?.mode === "page_all" ? 60 : payload.scope?.itemIds.length || 1,
          documents: 1, rows: 1, fields: 1, matchedOccurrences: 2, changedOccurrences: 1, duplicateOccurrences: 1, ready: 1, skipped: 0, errors: 0 }, createdAt: 100, expiresAt: 1000, manifestHash: hash(serial) };
      if (manyPlanRows) Object.assign(p.summary, { images: 64, documents: 64, rows: 64, fields: 64, ready: 64 });
      plans.set(p.planId, p); return { status: 201, data: p };
    }
    if (route === "plans/rows") {
      const total = plans.get(payload.planId).summary.rows, offset = Number(payload.offset);
      return { data: { planId: payload.planId, total, offset, limit: 60, rows: Array.from({length: Math.max(0, Math.min(60, total - offset))}, (_, index) => {
        const n = offset + index + 1;
        return { documentId: id("doc", n), path: docs.get(id("doc", n)).path, itemIds: [id("item", n)], beforeHash: hash(n), afterHash: hash(n + 1), beforeVersion: id("ver", n), bindingRevisions: [{bindingId:id("bind",n),bindingRevision:1}], action: "write", status: "ready", code: null, matchedOccurrences: 2, changedOccurrences: 1, duplicateOccurrences: 1, diff: { before: "cat, cat", after: "cat, changed" } };
      }) } };
    }
    if (route === "plans/apply") {
      assert.deepEqual(Object.keys(payload).sort(), ["requestId", "planId", "manifestHash", "confirm"].sort()); assert.equal(payload.confirm, true);
      if (timeoutApply) { timeoutApply = false; return fail("UPSTREAM_TIMEOUT", 504); }
      if (mutateSource) {
        const plan = plans.get(payload.planId), n = 8, doc = docs.get(id("doc", n));
        assert.equal(plan.operation.op, "tag_set"); assert.deepEqual(plan.scope.itemIds, [id("item",n)]);
        const tokens = plan.operation.tokens; doc.generation++; doc.sourceVersion=id("ver",++serial); doc.sourceHash=hash(serial);
        doc.rawText=tokens.join(", "); doc.bytes=Buffer.byteLength(doc.rawText); const f=doc.fields[0]; f.value=doc.rawText; f.rawRange={start:0,end:doc.rawText.length};
        let start=0; f.segments=tokens.map((text,ordinal)=>{ const range={start,end:start+text.length}; start+=text.length+2;
          return {segmentId:id("seg",++serial),recordId:f.recordId,pointer:f.pointer,ordinal,kind:f.kind,text,sourceHash:doc.sourceHash,sourceVersion:doc.sourceVersion,generation:doc.generation,rawRanges:[range],valueRange:range,elementIndex:null}; });
        Object.assign(candidates.get(id("item",n))[0],{sourceVersion:doc.sourceVersion,sourceHash:doc.sourceHash,bytes:doc.bytes});
      }
      const j = job(payload.planId); jobs.set(j.jobId, j); return { status: 202, data: { job: j } };
    }
    if (route === "jobs") return { data: jobs.get(payload.jobId) };
    if (route === "jobs/rows") return { data: { jobId: payload.jobId, total: 1, offset: Number(payload.offset), limit: 60, rows: [{ documentId: id("doc", 1), status: applyConflict ? "skipped" : "written", code: applyConflict ? "VERSION_CONFLICT" : null, beforeHash: hash(1), afterHash: hash(2), beforeVersion: id("ver", 1), afterVersion: id("ver", 2), backupId: `${payload.jobId}:${id("doc", 1)}` }] } };
    if (route === "jobs/stop") return { data: { job: { ...jobs.get(payload.jobId), status: "stopped" } } };
  } else {
    if (route === "settings") { if (method === "POST") { assert.ok(!("settings" in payload)); const {requestId,expectedConfigVersion,...fields}=payload; settings = { ...settings, ...fields, configVersion: id("cfg", ++serial) }; } return { data: settings }; }
    if (route === "credentials") { settings = { ...settings, configVersion: id("cfg", ++serial), hasKey: !payload.clear, keyHint: payload.clear ? "" : "configured" }; return { data: { configVersion: settings.configVersion, hasKey: settings.hasKey, keyHint: settings.keyHint } }; }
    if (route === "dictionary") return { data: dictionaryState };
    if (route.startsWith("dictionary/")) { const j = job(null, "dictionary"); jobs.set(j.jobId, j); return { status: 202, data: { job: j } }; }
    if (route === "view") {
      const saved = translationViews.get(payload.documentId), doc = docs.get(payload.documentId);
      if (saved && saved.sourceVersion !== doc.sourceVersion) return { data: { ...saved, sourceVersion: doc.sourceVersion, generation: doc.generation,
        segments: saved.segments.map(s => ({ ...s, status: "stale", staleReason: "source_changed" })) } };
      return { data: saved || viewFor(payload.documentId) };
    }
    if (route === "manual") {
      assert.equal(payload.expectedRevision, manualRevision, "manual CAS uses root revision, not history snapshot revision");
      const v = translationViews.get(payload.documentId) || viewFor(payload.documentId), old = v.segments.find(s => s.segmentId === payload.segmentId);
      const segment = { ...old, targetText: payload.targetText, manual: true, engine: "manual", status: "translated", dictionaryVersion: null, configVersion: null, model: null, staleReason: null, code: null, segmentRevision: old.segmentRevision + 1 };
      v.revision = ++manualRevision; v.segments = v.segments.map(s => s.segmentId === payload.segmentId ? segment : s); translationViews.set(payload.documentId, v); return { data: { revision: manualRevision, segment } };
    }
    if (route === "plans" && method === "POST") {
      const p = { planId: id("plan", ++serial), requestId: payload.requestId, status: "ready", engine: payload.engine, scope: payload.scope,
        sourceLanguage: payload.sourceLanguage, targetLanguage: payload.targetLanguage, dictionaryVersion: payload.engine === "dictionary" ? dictionaryState.dictionaryVersion : null,
        configVersion: settings.configVersion, model: settings.model, replaceManual: payload.replaceManual, targetSegments: 1, sourceCharacters: 3, cacheHits: 0, remoteBatches: payload.engine === "api" ? 1 : 0,
        maximumOutputTokens: payload.engine === "api" ? 2048 : 0, manifestHash: hash(serial), createdAt: 1, expiresAt: 1000 }; plans.set(p.planId, p); if(delayTranslationPlan) { delayTranslationPlan=false; return new Promise(resolve=>{pendingTranslationPlan=()=>resolve({status:201,data:p});}); } return { status: 201, data: p };
    }
    if (route === "plans/segments") return { data: { planId: payload.planId, total: 1, offset: Number(payload.offset), limit: 60, segments: [{ documentId: id("doc", 1), itemIds: [id("item", 1)], segment: docs.get(id("doc", 1)).fields[0].segments[0], expectedTranslationRevision: manualRevision, expectedSegmentRevision: 1, status: "ready", code: null }] } };
    if (route === "jobs/start") {
      const p = plans.get(payload.planId); assert.equal(payload.acceptMaximumOutputTokens, p.maximumOutputTokens); assert.equal(payload.confirmRemote, p.engine === "api"); assert.equal(payload.confirmCost, p.engine === "api");
      const j = job(payload.planId, "translation"); if (remoteFail) { j.failed = 1; j.written = 0; } jobs.set(j.jobId, j); return { status: 202, data: { job: j } };
    }
    if (route === "jobs") return { data: jobs.get(payload.jobId) };
    if (route === "jobs/segments") return { data: { jobId: payload.jobId, total: 0, offset: 0, limit: 60, segments: [] } };
    if (route === "jobs/stop") return { data: { job: { ...jobs.get(payload.jobId), status: "stopped" } } };
  }
  throw Error(`Unexpected ${domain} ${method} ${route}`);
}
globalThis.fetch = async (url, options = {}) => {
  const parsed = new URL(url, "http://localhost"), domain = parsed.pathname.startsWith("/api/translation/") ? "translation" : "annotation";
  const route = parsed.pathname.replace(/^\/api\/(?:grabber\/annotations|translation)\//, "");
  assert.equal(options.headers["X-UI-Token"], "fixture-token");
  const method = options.method || "GET", payload = method === "POST" ? JSON.parse(options.body) : Object.fromEntries(parsed.searchParams);
  const pending = hold?.domain === domain && hold.method === method && hold.route === route && hold.matches(payload) ? hold : null;
  if (pending) hold = null;
  const result = clone(await wire(domain, method, route, payload));
  if (pending) { pending.reached = true; await pending.ready; }
  const status = result.status || 200; return { status, ok: status < 400, json: async () => clone(result.body || { ok: true, data: result.data }) };
};
globalThis.testApi = { get: async (path) => {
  if (path.endsWith("/status")) return { available: true, running: true };
  if (path.endsWith("/sources")) return { sources: [{ id: "yandere", label: "fixture", auth_fields: [] }], capabilities: { library: true, retag: true, annotations_v1: true } };
  if (path.endsWith("/progress")) return { status: "idle", items: [], logs: [], item_count: 0, log_count: 0 };
  if (path.endsWith("/library/groups")) return { groups: [{ name: "alpha", image_count: 64 }, { name: "beta", image_count: 1 }], truncated: false };
  if (path.endsWith("/credentials")) return { values: {}, configured: [] };
  throw Error(path);
}, post: async () => ({ ok: true }) };
try {
  for (const name of ["grabber", "grabber-library", "grabber-media", "grabber-annotations", "grabber-translation", "annotation-api", "format"]) {
    let source = await readFile(new URL(`${name}.js`, web), "utf8");
    source = source.replace(/from "\.\/(.*?)\.js"/g, 'from "./$1.mjs"');
    if (name === "grabber") source = source.replace('  render();\n  try { await connect(); }', '  ctx.expose({action,capture,change,render,annotations,library});\n  render();\n  try { await connect(); }');
    await writeFile(join(tmp, `${name}.mjs`), source);
  }
  await writeFile(join(tmp, "api.mjs"), 'export const TOKEN="fixture-token"; export const get=(...args)=>globalThis.testApi.get(...args); export const post=(...args)=>globalThis.testApi.post(...args);');
  const { grabberView } = await import(pathToFileURL(join(tmp, "grabber.mjs")));
  const cleanup = await grabberView(main, { t(key) { if (!(key in dictionary)) missingKeys.add(key); return dictionary[key] || key; }, expose(v) { view = v; } });
  const pane = () => root.querySelector("#grabber-pane");
  const button = (act, tab) => root.querySelectorAll("[data-act]").find(n => n.dataset.act === act && (!tab || n.dataset.tab === tab));
  const control = (name, scope = "annotation") => root.querySelectorAll("[data-field]").find(n => n.dataset.field === name && n.dataset.scope === scope);
  const click = async (act, tab) => { const node = button(act, tab); assert.ok(node, act); assert.equal(node.disabled, false, `${act} enabled`); await view.action({ target: node }); };
  const fill = async (name, value, scope = "annotation", inputOnly = false) => { const node = control(name, scope); assert.ok(node, name); if (node.type === "checkbox") node.checked = value; else node.value = value; view.capture({ target: node }); if (!inputOnly) await view.change({ target: node }); return node; };
  const flush = async () => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
  await click("tab", "library");
  assert.equal(pane().querySelectorAll("img").length, 60);
  assert.equal(requests.filter(r => r.domain === "annotation" && r.route === "snapshots").length, 1);
  assert.equal(requests.filter(r => r.route === "jobs/start").length, 0, "browsing never translates");
  await fill(`selected:${id("item", 1)}`, true); assert.equal(view.annotations.state.browse.itemId, null, "checkbox does not open detail");
  await click("library-next");
  assert.equal(view.annotations.state.browse.selection.has(id("item",1)),true,"first-page identity is remembered");
  assert.throws(()=>view.annotations.scope(),/Select at least one/,"off-page selection is not a current-page scope");
  await fill(`selected:${id("item",61)}`,true);
  assert.deepEqual(view.annotations.scope().itemIds,[id("item",61)]); assert.equal(view.annotations.scope().pageOffset,60);
  await click("annotation-batch-open"); await fill("batch:tokens","page-two"); await click("annotation-batch-preview");
  assert.deepEqual(requests.findLast(r=>r.route==="plans").payload.scope.itemIds,[id("item",61)]);
  await click("translation-plan"); assert.deepEqual(requests.findLast(r=>r.route==="plans").payload.scope.itemIds,[id("item",61)]);
  await click("library-prev");
  assert.equal(control(`selected:${id("item",1)}`).checked,true); assert.equal(view.annotations.state.browse.selection.size,2);
  assert.equal(view.annotations.state.batch.plan,null); assert.equal(view.annotations.state.translation.plan,null,"old-page plans are invalidated");
  assert.deepEqual(view.annotations.scope().itemIds,[id("item",1)]); assert.equal(view.annotations.scope().pageOffset,0);
  await click("annotation-batch-open"); await fill("batch:tokens", "new_tag"); await click("annotation-batch-preview");
  assert.equal(requests.findLast(r => r.route === "plans" && r.domain === "annotation").payload.scope.mode, "page_selected");
  assert.match(pane().textContent, /cat, changed/);
  await fill("batch:tokens", "different"); assert.equal(view.annotations.state.batch.plan, null, "changed form invalidates plan");
  await click("annotation-page-all"); await click("annotation-batch-preview"); assert.equal(requests.findLast(r => r.route === "plans").payload.scope.mode, "page_all");
  await click("annotation-all-filtered"); await click("annotation-batch-preview"); assert.deepEqual(requests.findLast(r => r.route === "plans").payload.scope, { mode: "all_filtered", snapshotId: view.annotations.state.browse.snapshotId });
  await click("annotation-cancel-preview"); await click("annotation-open");
  assert.equal(pane().querySelectorAll("img").length, 1);
  const image = pane().querySelector("img"), sid0 = id("seg", 10), sid1 = id("seg", 11);
  const segNode = (id, side) => pane().querySelectorAll("[data-segment-id]").find(n => n.dataset.segmentId === id && n.dataset.side === side);
  const callsBefore = requests.length, writes = pane().writes;
  root.dispatchEvent({ type: "pointerover", target: segNode(sid1, "source") });
  assert.equal(segNode(sid1, "translation").classList.contains("annotation-linked"), true);
  assert.equal(segNode(sid0, "translation").classList.contains("annotation-linked"), false, "duplicate tags have independent IDs");
  root.dispatchEvent({ type: "focusin", target: segNode(sid0, "translation") });
  assert.equal(segNode(sid0, "source").classList.contains("annotation-linked"), true, "keyboard focus wins hover");
  root.dispatchEvent({ type: "focusout", target: segNode(sid0, "translation") });
  root.dispatchEvent({ type: "pointerleave", target: root });
  assert.equal(pane().querySelectorAll(".annotation-linked").length, 0);
  assert.equal(requests.length, callsBefore); assert.equal(pane().writes, writes); assert.equal(pane().querySelector("img"), image);
  // A locally edited draft never re-rendered the pane; discard must still replace
  // the stale DOM instead of trusting the pre-draft HTML cache.
  const discardToken = control("token:0");
  await fill("token:0", "discard probe", "annotation", true);
  assert.match(pane().textContent, /Unsaved source/);
  await click("annotation-discard");
  assert.notEqual(control("token:0"), discardToken, "discard repaints the stale editor");
  assert.equal(control("token:0").value, "cat");
  assert.match(pane().textContent, /Saved source/);
  // Translation view work belongs to the tab lifetime, not only document identity.
  const lateViewHold=holdNext("translation","GET","view"); const lateTabView=view.annotations.translation.load(); await flush(); assert.equal(lateViewHold.reached,true);
  root.dispatchEvent({type:"focusin",target:segNode(sid0,"translation")});
  await click("tab","results"); assert.equal(view.annotations.state.translation.focusedSegmentId,null); assert.equal(view.annotations.state.translation.hoveredSegmentId,null);
  await click("tab","library"); const returnedView=view.annotations.state.translation.view; lateViewHold.release(); await lateTabView; assert.equal(view.annotations.state.translation.view,returnedView); assert.equal(pane().querySelectorAll(".annotation-linked").length,0);
  for(const route of ["plans","plans/segments","jobs/start"]) {
    if(route !== "plans") await click("translation-plan");
    const pendingHold=holdNext("translation",route === "plans/segments"?"GET":"POST",route);
    const pendingAction=route === "plans/segments"?view.annotations.translation.action("translation-target-page",{dataset:{offset:"0"}}):click(route === "plans"?"translation-plan":"translation-start");
    await flush();assert.equal(pendingHold.reached,true);await click("tab","results");await click("tab","library");
    pendingHold.release();await pendingAction;assert.equal(view.annotations.state.translation.plan,null);assert.equal(view.annotations.state.translation.targets,null);assert.equal(view.annotations.state.translation.jobId,null);
  }
  // A navigation read clears highlights immediately but failure keeps the old editor.
  root.dispatchEvent({type:"focusin",target:segNode(sid0,"translation")});
  const navDoc=view.annotations.state.editor.document,navImage=pane().querySelector("img");
  failures.set("annotation:GET:documents","UPSTREAM_TIMEOUT");const navHold=holdNext("annotation","GET","documents");const failedNav=click("library-next");await flush();assert.equal(navHold.reached,true);
  assert.equal(pane().querySelectorAll(".annotation-linked").length,0);assert.equal(view.annotations.state.editor.document,navDoc);
  navHold.release();await failedNav;assert.equal(view.annotations.state.editor.document,navDoc);assert.equal(pane().querySelector("img"),navImage);
  const token = control("token:1"), oldWrites = pane().writes;
  await fill("token:1", "changed", "annotation", true);
  assert.equal(control("token:1"), token); assert.equal(pane().writes, oldWrites, "source input never re-renders editor");
  root.dispatchEvent({ type: "pointerover", target: segNode(sid1, "source") }); assert.equal(pane().querySelectorAll(".annotation-linked").length, 0);
  main.scrollTop = 710;
  const cancelled = click("library-next"); await flush(); assert.equal(view.annotations.state.gate, true); await click("annotation-gate-cancel"); await cancelled;
  assert.equal(view.annotations.state.browse.itemId, id("item", 1)); assert.equal(main.scrollTop, 710);
  await click("annotation-save"); assert.deepEqual(requests.findLast(r => r.route === "plans").payload.operation, { op: "tag_set", tokens: ["cat", "changed", "dog"] });
  await click("annotation-cancel-preview"); assert.equal(view.annotations.state.editor.fieldDrafts.size, 1);
  failPlan = true; await click("annotation-save"); assert.equal(view.annotations.state.editor.fieldDrafts.size, 1); failPlan = false;
  await click("annotation-save"); timeoutApply = true; await click("annotation-apply"); const request = requests.findLast(r => r.route === "plans/apply").payload;
  await click("annotation-apply"); assert.equal(requests.findLast(r => r.route === "plans/apply").payload.requestId, request.requestId, "explicit retry reconciles same requestId");
  assert.equal(view.annotations.state.editor.fieldDrafts.size, 0);
  await fill(`manual:${sid0}`, "手工", "translation"); await click("translation-save-manual"); assert.equal(requests.findLast(r => r.route === "manual").payload.expectedRevision, 4);
  await fill(`missing:${id("seg", 12)}`, true, "translation"); await click("translation-missing-api");
  assert.deepEqual(requests.findLast(r => r.route === "plans").payload.segmentIds, [id("seg", 12)]);
  assert.equal(button("translation-start").disabled, true); await fill("confirmCost", true, "translation"); await click("translation-start");
  assert.equal(requests.findLast(r => r.route === "jobs/start").payload.confirmRemote, true);
  await fill("settings:endpoint", "http://localhost:9999/v1/chat/completions", "translation"); await fill("settings:model", "fixture", "translation"); await click("translation-save-settings");
  const keyInput = control("apiKey", "translation");
  keyInput.value = "private-fixture-key"; view.capture({ target: keyInput });
  await view.change({ target: keyInput });
  assert.equal(control("apiKey", "translation"), keyInput, "blur/change never replaces the password input before save");
  assert.equal(keyInput.value, "private-fixture-key");
  await click("translation-save-key");
  assert.equal(requests.findLast(r => r.route === "credentials").payload.apiKey, "private-fixture-key", "the typed key is read before the input is cleared");
  assert.equal(control("apiKey", "translation").value, ""); assert.ok(!pane().innerHTML.includes("private-fixture-key")); await click("translation-clear-key");
  await fill("install:path", "/tmp/fixture-only.sqlite", "translation"); await click("translation-dictionary-import"); assert.equal(requests.findLast(r => r.route === "dictionary/import").payload.path, "/tmp/fixture-only.sqlite");
  await click("library-next"); assert.equal(view.annotations.state.editor.document.format, "caption"); assert.equal(button("library-start").disabled, true, "known NL blocks group retag");
  await fill("source", "New paragraph"); const leaving = click("library-next"); await flush(); await click("annotation-gate-save");
  assert.equal(view.annotations.state.browse.itemId, id("item", 2), "save waits for explicit diff confirmation");
  await click("annotation-apply"); await leaving; assert.equal(view.annotations.state.browse.itemId, id("item", 3));
  await fill("raw", true); const raw = control("rawText");
  root.dispatchEvent({ type: "focusin", target: segNode(id("seg", 30), "translation") });
  assert.equal(raw.selectionStart, 15, "raw UTF16 offset maps CRLF to textarea LF");
  await fill("rawText", raw.value.replace("😀", "✨")); await click("annotation-save"); assert.equal(requests.findLast(r => r.route === "plans").payload.operation.op, "whole_document");
  await click("annotation-discard");
  await click("library-next"); const shared = view.annotations.state.editor.document; shared.bindings[0].itemIds.push(id("item", 5));
  const beforeDelete = requests.length; await click("annotation-delete"); assert.equal(requests.length, beforeDelete, "shared deletion rejected locally");
  await click("annotation-clear"); assert.equal(requests.findLast(r => r.route === "plans").payload.operation.op, "clear");
  await click("annotation-cancel-preview"); await click("library-back");
  await click("library-next"); assert.equal(pane().querySelectorAll("img").length, 4); await click("annotation-open"); assert.equal(view.annotations.state.browse.itemId, id("item", 61));
  main.scrollTop = 500; await click("library-prev"); assert.equal(view.annotations.state.browse.itemId, id("item", 60)); assert.equal(main.scrollTop, 500);
  await click("library-next"); assert.equal(view.annotations.state.browse.itemId, id("item", 61)); assert.equal(main.scrollTop, 500);
  await click("annotation-toggle-image"); assert.equal(pane().querySelector("img").parentNode.parentNode.hidden, true); await click("annotation-toggle-image");
  await click("library-back"); await click("library-prev");
  delayDocument = id("doc", 1); const late = click("annotation-open"); await flush(); await view.annotations.action("library-back"); const closedWrites = pane().writes; pendingDocument(); await late;
  assert.equal(view.annotations.state.browse.itemId, null); assert.equal(pane().writes, closedWrites, "late document cannot reopen detail");
  await click("annotation-open"); await fill("token:0", "route draft");
  location.hash = "#/settings"; let stopped = false; const route = windowListeners.get("hashchange")({ stopImmediatePropagation() { stopped = true; } }); await flush();
  assert.equal(stopped, true); assert.equal(location.hash, "#/grabber"); await click("annotation-gate-cancel"); await route; assert.equal(location.hash, "#/grabber");
  const discard = click("library-back"); await flush(); await click("annotation-gate-discard"); await discard;
  assert.equal(view.annotations.dirty(), false);
  // Binding/create/delete/undo stay on the server plan protocol, never per-image writes.
  await click("annotation-open"); await fill("binding:kind", "tags"); await click("annotation-confirm-kind");
  const confirmation = requests.findLast(r => r.route === "bindings/confirm").payload;
  assert.equal(confirmation.expectedVersion, id("ver", 1)); assert.deepEqual(confirmation.spec.fields, [{pointer:"",type:"string"}]);
  await click("annotation-select-file");
  await fill("create:path", "image-1.caption"); await fill("create:value", "New caption"); await click("annotation-create");
  const creation = requests.findLast(r => r.route === "plans").payload;
  assert.equal(creation.operation.op, "create"); assert.equal(creation.missingPolicy, "error"); assert.deepEqual(creation.scope.itemIds, [id("item",1)]);
  await click("annotation-cancel-preview"); await click("annotation-delete"); assert.equal(requests.findLast(r => r.route === "plans").payload.operation.confirmDelete, true);
  await click("annotation-cancel-preview"); await click("annotation-undo"); assert.equal(requests.findLast(r => r.route === "undo/plans").domain, "annotation");
  await click("annotation-cancel-preview"); await fill("token:0", "conflict draft"); await click("annotation-save"); applyConflict = true; await click("annotation-apply");
  assert.equal(view.annotations.state.editor.phase, "conflict"); assert.equal(view.annotations.state.editor.fieldDrafts.get(0)[0], "conflict draft"); assert.match(pane().textContent, /VERSION_CONFLICT/);
  applyConflict = false; await click("annotation-discard");
  const savedView = view.annotations.state.translation.view, firstSegment = savedView.segments.find(s => s.segmentId === sid0);
  firstSegment.status = "stale"; firstSegment.staleReason = "source_changed";
  root.dispatchEvent({type:"pointerover",target:segNode(sid0,"source")}); assert.equal(pane().querySelectorAll(".annotation-linked").length,0);
  firstSegment.staleReason = "dictionary_changed";
  root.dispatchEvent({type:"pointerover",target:segNode(sid0,"source")}); assert.equal(segNode(sid0,"translation").classList.contains("annotation-linked"),true);
  windowListeners.get("blur")({type:"blur"}); root.dispatchEvent({type:"pointerleave",target:root});
  const missingSegment = savedView.segments.find(s => s.segmentId === id("seg",12)); missingSegment.status = "missing";
  root.dispatchEvent({type:"pointerover",target:segNode(id("seg",12),"source")}); assert.equal(pane().querySelectorAll(".annotation-linked").length,0);
  await fill("engine", "dictionary", "translation"); await click("translation-plan"); await click("translation-start");
  assert.equal(requests.findLast(r=>r.route==="jobs/start").payload.acceptMaximumOutputTokens,0);
  await fill("engine", "api", "translation"); await click("translation-plan"); await fill("confirmCost",true,"translation");
  remoteFail=true; await click("translation-start"); remoteFail=false;
  assert.equal(view.annotations.state.translation.job.failed,1); assert.equal(view.annotations.state.translation.view.segments.find(s=>s.segmentId===sid0).targetText,"手工");
  await fill("install:downloadUrl","https://raw.githubusercontent.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table/fixed/tag.sqlite","translation");
  await fill("install:ref","fixed","translation"); await fill("install:expectedSha256","a".repeat(64),"translation");
  const noNetwork=requests.length; await click("translation-dictionary-install"); assert.equal(requests.length,noNetwork,"download needs explicit network consent");
  await fill("install:confirmNetwork",true,"translation"); await click("translation-dictionary-install"); await click("translation-dictionary-update");
  assert.equal(requests.findLast(r=>r.route==="dictionary/update").payload.expectedDictionaryVersion,dictionaryState.dictionaryVersion);
  // New snapshot prevents a cached page from hiding cross-page failure/ABA tests.
  await click("library-back"); await click("library-refresh");
  await view.action({target:pane().querySelectorAll('[data-act="annotation-open"]')[59]});
  delayPage=60; const crossing=click("library-next"); await flush(); const oldItem=view.annotations.state.browse.itemId;
  main.scrollTop=333; pendingPage.reject(Error("fixture page unavailable")); await crossing;
  assert.equal(view.annotations.state.browse.itemId,oldItem); assert.equal(main.scrollTop,333);
  delayPage=60; const crossingAgain=click("library-next"); await flush(); main.scrollTop=222; pendingPage.resolve(); await crossingAgain;
  assert.equal(view.annotations.state.browse.itemId,id("item",61)); assert.equal(main.scrollTop,222);
  await click("library-back"); await click("library-prev"); await click("library-refresh");
  delayPage=60; const aba=click("library-next"); await flush(); await click("tab","results"); await click("tab","library"); pendingPage.resolve(); await aba;
  assert.equal(view.annotations.state.browse.offset,0,"late ABA page remains cancelled");
  // F5: staged candidates/document reads preserve the committed page and both drafts.
  await fill(`selected:${id("item",60)}`,true);
  await view.annotations.open(id("item",60));
  assert.equal(view.annotations.state.browse.selection.size,1);
  const preserved = () => ({ item:view.annotations.state.browse.itemId, offset:view.annotations.state.browse.offset,
    doc:view.annotations.state.editor.document, view:view.annotations.state.translation.view, selection:[...view.annotations.state.browse.selection],
    source:[...view.annotations.state.editor.fieldDrafts], manual:[...view.annotations.state.translation.drafts], image:pane().querySelector("img") });
  await fill(`manual:${id("seg",600)}`,"manual retained","translation",true);
  await fill("token:0","source retained","annotation",true);
  const oldState=preserved(); assert.equal(oldState.manual.length,1); assert.equal(oldState.source.length,1);
  for (const routeName of ["candidates","documents"]) {
    failures.set(`annotation:GET:${routeName}`,"UPSTREAM_TIMEOUT");
    const crossingFailure=click("library-next"); await flush(); await click("annotation-gate-discard"); await crossingFailure;
    assert.deepEqual(preserved(),oldState,`${routeName} failure preserves complete committed editor`);
    assert.equal(view.annotations.state.browse.offset,0);
  }
  // Explicit file selection has the same transactional dirty gate.
  failures.set("annotation:GET:documents","UPSTREAM_TIMEOUT");
  const fileFailure=fill("file",id("doc",60)); await flush(); await click("annotation-gate-discard"); await fileFailure;
  assert.deepEqual(preserved(),oldState);
  const crossingHold=holdNext("annotation","GET","documents",p=>p.documentId===id("doc",61));
  const crossingCommit=click("library-next"); await flush(); await click("annotation-gate-discard"); await flush();
  assert.equal(crossingHold.reached,true); assert.deepEqual(preserved(),oldState);
  main.scrollTop=447; crossingHold.release(); await crossingCommit;
  assert.equal(view.annotations.state.browse.itemId,id("item",61)); assert.equal(view.annotations.state.browse.offset,60); assert.equal(main.scrollTop,447);
  assert.equal(view.annotations.dirty(),false);
  await click("library-prev"); await click("library-back");
  // A newer open or a newly typed draft cancels an earlier read without abort support.
  const staleOpen=holdNext("annotation","GET","documents",p=>p.documentId===id("doc",1));
  const firstOpen=view.annotations.open(id("item",1)); await flush(); await view.annotations.open(id("item",2));
  const latestDoc=view.annotations.state.editor.document; staleOpen.release(); await firstOpen;
  assert.equal(view.annotations.state.editor.document,latestDoc);
  const typedRead=holdNext("annotation","GET","documents",p=>p.documentId===id("doc",3));
  const typedOpen=click("library-next"); await flush(); await fill("source","typed during pending navigation","annotation",true);
  typedRead.release(); await typedOpen; assert.equal(view.annotations.state.browse.itemId,id("item",2));
  assert.equal(view.annotations.state.editor.fieldDrafts.get(0),"typed during pending navigation"); await click("annotation-discard");
  // Both-dirty save: manual CAS failure and source apply conflict never navigate.
  await fill(`manual:${id("seg",20)}`,"double dirty manual","translation",true); await fill("source","double dirty source");
  const bothLeave=click("library-next"); await flush(); failures.set("translation:POST:manual","VERSION_CONFLICT"); await click("annotation-gate-save");
  assert.equal(view.annotations.state.browse.itemId,id("item",2)); assert.equal(view.annotations.state.editor.fieldDrafts.size,1); assert.equal(view.annotations.state.translation.drafts.size,1);
  await click("annotation-gate-save"); applyConflict=true; await click("annotation-apply"); applyConflict=false;
  assert.equal(view.annotations.state.browse.itemId,id("item",2)); assert.equal(view.annotations.state.gate,true); assert.equal(view.annotations.state.editor.fieldDrafts.size,1);
  await click("annotation-gate-cancel"); await bothLeave; await click("annotation-discard");
  await click("library-back");
  // F6: two pages of the same >60-row plan, invalidation, undo/shared/results and leave.
  await click("annotation-all-filtered"); manyPlanRows=true; await click("annotation-batch-open"); await click("annotation-batch-preview"); manyPlanRows=false;
  assert.equal(view.annotations.state.batch.rows.rows.length,60);
  const oldRows=holdNext("annotation","GET","plans/rows",p=>Number(p.offset)===60);
  const firstRows=view.annotations.action("annotation-plan-page",{dataset:{offset:"60"}}); await flush();
  await view.annotations.action("annotation-plan-page",{dataset:{offset:"0"}}); const currentRows=view.annotations.state.batch.rows;
  oldRows.release(); await firstRows; assert.equal(view.annotations.state.batch.rows,currentRows,"older page cannot replace newer page");
  await view.annotations.action("annotation-plan-page",{dataset:{offset:"60"}}); assert.equal(view.annotations.state.batch.rows.rows.length,4);
  const invalidRows=holdNext("annotation","GET","plans/rows"); const invalidPage=view.annotations.action("annotation-plan-page",{dataset:{offset:"0"}}); await flush();
  await fill("batch:tokens","invalidate row result"); invalidRows.release(); await invalidPage; assert.equal(view.annotations.state.batch.rows,null);
  const oldUndo=holdNext("annotation","POST","undo/plans"); const undoPending=click("annotation-undo"); await flush();
  await fill("batch:tokens","invalidate undo"); oldUndo.release(); await undoPending; assert.equal(view.annotations.state.batch.plan,null);
  await view.annotations.open(id("item",1)); await fill("sharedPath","shared.jsonl");
  const sharedHold=holdNext("annotation","GET","candidates"); const sharedPending=click("annotation-shared"); await flush();
  await click("library-next"); const newerCandidates=view.annotations.state.editor.candidates; sharedHold.release(); await sharedPending;
  assert.equal(view.annotations.state.editor.candidates,newerCandidates);
  const resultHold=holdNext("annotation","GET","jobs/rows"); const resultPending=view.annotations.action("annotation-result-page",{dataset:{offset:"0"}}); await flush();
  await click("library-next"); const newerResults=view.annotations.state.batch.results; resultHold.release(); await resultPending; assert.equal(view.annotations.state.batch.results,newerResults);
  // Default JSON raw context exposes server ranges without enabling whole-raw editing.
  assert.equal(control("rawText"),undefined); const rawContext=pane().querySelector('[data-annotation-raw-context]');
  assert.equal(rawContext.querySelectorAll('[data-segment-id]').length,1); assert.equal(rawContext.textContent,docs.get(id("doc",3)).rawText);
  const rawOnly=rawContext.querySelector('[data-segment-id]'); root.dispatchEvent({type:"focusin",target:rawOnly});
  assert.equal(segNode(id("seg",30),"translation").classList.contains("annotation-linked"),true);
  await click("library-back"); await click("annotation-batch-preview");
  const leaveRows=holdNext("annotation","GET","plans/rows"); const hiddenPage=view.annotations.action("annotation-plan-page",{dataset:{offset:"0"}}); await flush();
  await click("tab","results"); const beforeHiddenRows=view.annotations.state.batch.rows; leaveRows.release(); await hiddenPage; assert.equal(view.annotations.state.batch.rows,beforeHiddenRows);
  await click("tab","library");
  // Saved source generation changes never overwrite or migrate old manual records/drafts.
  await view.annotations.open(id("item",8)); const oldSegment=id("seg",80);
  await fill(`manual:${oldSegment}`,"saved manual generation one","translation",true); await click("translation-save-manual");
  const storedManual=clone(translationViews.get(id("doc",8)));
  await fill(`manual:${oldSegment}`,"unsaved manual generation one","translation",true); await fill("token:0","new source generation","annotation",true);
  await click("annotation-save"); mutateSource=true; await click("annotation-apply"); mutateSource=false;
  assert.equal(view.annotations.state.editor.document.generation,2); assert.equal(view.annotations.state.translation.drafts.get(oldSegment),"unsaved manual generation one");
  assert.deepEqual(translationViews.get(id("doc",8)),storedManual); assert.equal(view.annotations.state.translation.view.segments[0].staleReason,"source_changed");
  assert.equal(view.annotations.state.editor.fieldDrafts.size,0); await click("translation-discard-manual"); await click("library-back");
  // Source and translation share a <=60-segment window, never all 2,000 nodes.
  await view.action({target:pane().querySelectorAll('[data-act="annotation-open"]')[5]});
  const segmentIds=side=>pane().querySelectorAll('[data-segment-id]').filter(n=>n.dataset.side===side).map(n=>n.dataset.segmentId);
  const boundedPair=expected=>{
    assert.equal(segmentIds("translation").length,expected); assert.ok(new Set(segmentIds("source")).size<=60);
    assert.ok(segmentIds("source").length<=120,"field inputs plus raw-context spans share the same bounded segment window");
    assert.deepEqual([...new Set(segmentIds("source"))],[...new Set(segmentIds("translation"))]);
    assert.ok(pane().querySelectorAll('[data-field]').filter(n=>n.dataset.field.startsWith("token:")).length<=60);
  };
  const segmentPage=async offset=>{const node=pane().querySelectorAll('[data-act="annotation-segment-page"]').find(n=>Number(n.dataset.offset)===offset&&!n.disabled);assert.ok(node,`segment page ${offset}`);await view.action({target:node});};
  boundedPair(60); const largeImage=pane().querySelector("img"), segmentCalls=requests.length, imageSources=stats.imageSources.length;
  const draftId=id("seg",100001), missingId=id("seg",100002);
  await fill(`manual:${draftId}`,"retained manual page 1","translation",true);
  root.dispatchEvent({type:"focusin",target:segNode(draftId,"translation")}); assert.equal(segNode(draftId,"source").classList.contains("annotation-linked"),true);
  await fill(`missing:${missingId}`,true,"translation"); await segmentPage(60); boundedPair(60);
  assert.equal(pane().querySelectorAll(".annotation-linked").length,0); assert.equal(button("translation-missing-api").disabled,true);
  assert.equal(view.annotations.state.translation.drafts.get(draftId),"retained manual page 1"); assert.equal(view.annotations.state.translation.missing.has(missingId),true);
  assert.ok(!pane().textContent.includes(dictionary["grabber.translation.orphanDraft"]),"off-page drafts are not orphaned");
  assert.equal(requests.length,segmentCalls,"local segment pages and manual input issue no API"); assert.equal(pane().querySelector("img"),largeImage); assert.equal(stats.imageSources.length,imageSources);
  await fill("token:60","changed at 60","annotation",true); await segmentPage(0);
  assert.equal(control(`manual:${draftId}`,"translation").value,"retained manual page 1");
  await fill("token:0","changed at 0","annotation",true); await segmentPage(60); assert.equal(control("token:60").value,"changed at 60");
  await click("annotation-save"); const wholeField=requests.findLast(r=>r.domain==="annotation"&&r.route==="plans").payload.operation;
  assert.equal(wholeField.op,"tag_set"); assert.equal(wholeField.tokens.length,1000); assert.equal(wholeField.tokens[0],"changed at 0"); assert.equal(wholeField.tokens[60],"changed at 60");
  await click("annotation-cancel-preview"); await click("annotation-discard"); boundedPair(60);
  await click("translation-plan"); const visiblePlan=requests.findLast(r=>r.domain==="translation"&&r.route==="plans").payload;
  assert.deepEqual(visiblePlan.segmentIds,largeDoc.fields[0].segments.slice(60,120).map(s=>s.segmentId)); assert.deepEqual(visiblePlan.selections[0].pointers,["/tags"]);
  delayTranslationPlan=true; const oldSegmentPlan=click("translation-plan"); await flush(); await segmentPage(120); pendingTranslationPlan(); await oldSegmentPlan;
  assert.equal(view.annotations.state.translation.plan,null,"late prior-window plan cannot re-enable start"); assert.equal(view.annotations.state.editor.segmentOffset,120); boundedPair(60);
  await fill("fieldIndex","1"); boundedPair(60); assert.equal(segmentIds("translation")[0],id("seg",101000));
  assert.equal(view.annotations.state.translation.drafts.get(draftId),"retained manual page 1");
  await fill("fieldIndex","0"); assert.equal(control(`manual:${draftId}`,"translation").value,"retained manual page 1");
  await click("translation-missing-api"); assert.deepEqual(requests.findLast(r=>r.route==="plans").payload.segmentIds,[missingId]);
  await click("translation-discard-manual"); await fill("raw",true); boundedPair(60);
  await segmentPage(60); boundedPair(60); root.dispatchEvent({type:"focusin",target:segNode(id("seg",100061),"translation")});
  assert.equal(pane().querySelectorAll(".annotation-linked").length,2); assert.equal(control("rawText").selectionStart,largeDoc.fields[0].segments[61].rawRanges[0].start);
  await click("library-back"); await view.action({target:pane().querySelectorAll('[data-act="annotation-open"]')[6]});
  boundedPair(10); await fill("fieldIndex","199"); boundedPair(10);
  assert.equal(segmentIds("translation")[0],id("seg",201990),"JSONL record selection renders only the selected record field");
  await click("translation-plan"); const recordPlan=requests.findLast(r=>r.route==="plans").payload;
  assert.deepEqual(recordPlan.selections[0].recordIds,[id("rec",7199)]); assert.equal(recordPlan.segmentIds.length,10);
  await click("library-back");
  await view.annotations.open(id("item",2));
  const focusedSource=control("source"); focusedSource.focus(); focusedSource.setSelectionRange(2,7,"backward"); focusedSource.scrollTop=98; focusedSource.scrollLeft=11;
  main.scrollTop=681; failures.set("annotation:GET:documents","UPSTREAM_TIMEOUT"); await click("library-next");
  assert.equal(document.activeElement,control("source")); assert.equal(control("source").selectionStart,2); assert.equal(control("source").selectionEnd,7);
  assert.equal(control("source").selectionDirection,"backward"); assert.equal(control("source").scrollTop,98); assert.equal(control("source").scrollLeft,11); assert.equal(main.scrollTop,681);
  // A successful apply followed by a failed re-read is not a successful save-and-leave.
  await fill("source","saved but not re-read"); const rereadLeave=click("library-next"); await flush(); await click("annotation-gate-save");
  failures.set("annotation:GET:documents","UPSTREAM_TIMEOUT"); await click("annotation-apply");
  assert.equal(view.annotations.state.gate,true); assert.equal(view.annotations.state.browse.itemId,id("item",2)); assert.equal(view.annotations.state.editor.fieldDrafts.get(0),"saved but not re-read");
  await click("annotation-gate-cancel"); await rereadLeave; await click("annotation-discard"); await click("library-back");
  await view.annotations.open(id("item",2)); await click("annotation-clear");
  const refreshHold=holdNext("annotation","POST","snapshots"); const refreshingApply=click("annotation-apply"); await flush(); assert.equal(refreshHold.reached,true);
  await click("library-back"); const phaseAfterBack=view.annotations.state.editor.phase;
  refreshHold.release(); await refreshingApply; assert.equal(view.annotations.state.editor.phase,phaseAfterBack,"late post-job refresh cannot mark a newer view conflicted");
  delayDocument=id("doc",1); const disposedLoad=click("annotation-open"); await flush();
  const disposedRowsHold=holdNext("annotation","GET","jobs/rows"); const disposedRows=view.annotations.action("annotation-result-page",{dataset:{offset:"0"}}); await flush();
  const disposedStopHold=holdNext("annotation","POST","jobs/stop"); const disposedStop=view.annotations.action("annotation-job-stop"); await flush();
  assert.equal(disposedRowsHold.reached,true); assert.equal(disposedStopHold.reached,true);
  assert.ok(stats.imageSources.every(url => !url.includes("/api/grabber/file?")));
  assert.deepEqual([...missingKeys], [], "every rendered label exists in bilingual dictionary");
  const cleanupWrites=pane().writes; cleanup(); pendingDocument(); disposedRowsHold.release(); disposedStopHold.release(); await Promise.all([disposedLoad,disposedRows,disposedStop]);
  assert.equal(view.annotations.state.batch.results,null); assert.equal(view.annotations.state.editor.job,null);
  assert.equal(pane().writes,cleanupWrites); assert.equal(timers.size, 0); assert.equal(windowListeners.size, 0); assert.equal(view.annotations.state.browse.items.length, 0);
  console.log("annotation DOM: scopes, diff/apply/idempotent retry, dirty cancel/save/discard, multi-format CRUD, duplicate hover/focus, raw ranges, translation consent/manual/settings, media/scroll and cleanup passed");
} finally { await rm(tmp, { recursive: true, force: true }); }
