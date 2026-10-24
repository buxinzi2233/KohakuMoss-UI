/* Real shell entrypoints + real grabber/annotation/translation controllers.
   Only network/appearance and DOM layout are replaced; no server or user state. */
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { installDOM } from "./grabber_dom.mjs";
import { id, hash, docs, candidates, images } from "./annotation_fixture.mjs";

const base = "/tmp/kohaku-annotation-continuation/navigation-tests";
await mkdir(base, { recursive: true });
const tmp = await mkdtemp(join(base, "modules-"));
const web = new URL("../src/kohakumoss_ui/web/", import.meta.url);
const stats = { rootWrites: 0, removed: 0, imageSources: [] };
const dom = installDOM(stats);
const shell = document.createElement("div");
shell.innerHTML = '<main id="main"></main>' + ["crumb", "chip", "chipText", "runner", "modeBtn", "langBtn", "f-theme", "f-kogine", "f-knobs", "f-gpu", "f-torch", "toast"].map(id => `<div id="${id}"></div>`).join("");
dom.mount(shell);
document.getElementById = id => shell.querySelector(`#${id}`);
document.querySelectorAll = selector => shell.querySelectorAll(selector);
document.documentElement = { dataset: {}, style: { setProperty() {} } };
const main = document.getElementById("main");
document.getElementById("toast").appendChild = node => document.getElementById("toast").append(node);
const listeners = new Map();
globalThis.window = {
  addEventListener(type, fn, capture = false) { const list = listeners.get(type) || []; list.push({ fn, capture }); listeners.set(type, list); },
  removeEventListener(type, fn) { listeners.set(type, (listeners.get(type) || []).filter(entry => entry.fn !== fn)); },
};
globalThis.location = { hash: "#/grabber", pathname: "/", search: "?test=navigation" };
const historyCalls = [];
globalThis.history = Object.fromEntries(["pushState", "replaceState"].map(method => [method, (_state, _title, url) => {
  historyCalls.push({ method, url });
  const parsed = new URL(url, "http://localhost");
  Object.assign(location, { hash: parsed.hash, pathname: parsed.pathname, search: parsed.search });
}]));
globalThis.setTimeout = globalThis.setInterval = () => 1;
globalThis.clearTimeout = globalThis.clearInterval = () => {};
globalThis.localStorage = { getItem() { return null; }, setItem() {} };
const dictionary = JSON.parse(await readFile(new URL("lang/en.json", web), "utf8"));
let currentView, currentCtx, mounts = 0, cleanups = 0, fail = null, hold = null;
const requests = [], patches = [], plans = new Map();
let serial = 200, revision = 1, job;
const doc = docs.get(id("doc", 2)), item = images[1], segment = doc.fields[0].segments[0];
const clone = value => JSON.parse(JSON.stringify(value));
function translationSegment(source, overrides = {}) {
  return { ...source, sourceText: source.text, targetText: "translated", segmentRevision: 1,
    sourceLanguage: "en", targetLanguage: "zh-CN", status: "translated", engine: "dictionary",
    dictionaryVersion: "dict_fixture", configVersion: null, model: null, manual: false, staleReason: null, updatedAt: 1, code: null, ...overrides };
}
function translationView() {
  return { documentId: doc.documentId, sourceLanguage: "en", targetLanguage: "zh-CN", sourceVersion: doc.sourceVersion, generation: doc.generation,
    revision, snapshotRevision: revision, snapshotSourceVersion: doc.sourceVersion, snapshotGeneration: doc.generation,
    segments: doc.fields[0].segments.map(s => translationSegment(s)), historyCount: 0, history: [] };
}
async function wire(domain, method, route, payload) {
  requests.push({ domain, method, route, payload: clone(payload) });
  if (hold?.route === route) { const pending = hold; hold = null; pending.reached = true; await pending.promise; }
  if (fail === `${domain}:${route}`) throw Object.assign(Error("fixture failure"), { code: "VERSION_CONFLICT", status: 409 });
  if (domain === "translation") {
    if (route === "settings") return { configVersion: null, sourceLanguage: "en", targetLanguage: "zh-CN", enabled: false, endpoint: "", model: "", hasKey: false };
    if (route === "dictionary") return { status: "missing" };
    if (route === "view") return translationView();
    if (route === "manual") return { revision: ++revision, segment: translationSegment(segment, { targetText: payload.targetText, engine: "manual", manual: true, segmentRevision: revision }) };
  } else {
    if (route === "snapshots") return { snapshotId: id("snap", 1), libraryId: doc.libraryId, group: "alpha", filters: { format: "any", status: "any", search: "" }, total: 1, offset: 0, limit: 60, items: [item], truncated: false, createdAt: 1, expiresAt: 9999999999 };
    if (route === "candidates") return { itemId: item.itemId, candidates: candidates.get(item.itemId) };
    if (route === "documents") return doc;
    if (route === "plans" && method === "POST") {
      const plan = { planId: id("plan", ++serial), libraryId: doc.libraryId, requestId: payload.requestId, kind: "edit", status: "ready", scope: payload.scope, operation: payload.operation,
        summary: { images: 1, documents: 1, rows: 1, fields: 1, matchedOccurrences: 1, changedOccurrences: 1, duplicateOccurrences: 0, ready: 1, skipped: 0, errors: 0 }, createdAt: 1, expiresAt: 9999999999, manifestHash: hash(serial) };
      plans.set(plan.planId, plan); return plan;
    }
    if (route === "plans/rows") return { planId: payload.planId, total: 1, offset: 0, limit: 60, rows: [{ documentId: doc.documentId, path: doc.path, itemIds: [item.itemId], action: "write", status: "ready", code: null, diff: { before: doc.rawText, after: "navigation source draft" } }] };
    if (route === "plans/apply") {
      assert.equal(payload.confirm, true);
      job = { jobId: id("job", ++serial), planId: payload.planId, kind: "apply", status: "done", total: 1, processed: 1, written: 1, skipped: 0, failed: 0, undoAvailable: true };
      return { job };
    }
    if (route === "jobs") return job;
    if (route === "jobs/rows") return { jobId: job.jobId, total: 1, offset: 0, limit: 60, rows: [{ documentId: doc.documentId, status: "written", code: null }] };
  }
  throw Error(`Unexpected ${domain}:${method}:${route}`);
}
globalThis.fetch = async (url, options = {}) => {
  if (url.startsWith("/assets/lang/")) return { ok: true, json: async () => dictionary };
  const parsed = new URL(url, "http://localhost"), domain = parsed.pathname.startsWith("/api/translation/") ? "translation" : "annotation";
  assert.equal(options.headers["X-UI-Token"], "fixture-token");
  const route = parsed.pathname.replace(/^\/api\/(?:grabber\/annotations|translation)\//, "");
  const method = options.method || "GET", payload = method === "POST" ? JSON.parse(options.body) : Object.fromEntries(parsed.searchParams);
  try { const data = clone(await wire(domain, method, route, payload)); return { ok: true, status: 200, json: async () => ({ ok: true, data }) }; }
  catch (error) { if (!error.code) throw error; return { ok: false, status: error.status, json: async () => ({ ok: false, error: { code: error.code, message: error.message, details: {}, retryable: false } }) }; }
};
globalThis.navigationTest = {
  expose(view, ctx) { currentView = view; currentCtx = ctx; mounts++; },
  cleanup() { cleanups++; },
  patches,
  async get(path) {
    if (path.endsWith("/status")) return { available: true, running: true };
    if (path.endsWith("/sources")) return { sources: [{ id: "yandere", label: "fixture", auth_fields: [] }], capabilities: { library: true, retag: true, annotations_v1: true } };
    if (path.endsWith("/progress")) return { status: "idle", items: [], logs: [], item_count: 0, log_count: 0 };
    if (path.endsWith("/library/groups")) return { groups: [{ name: "alpha", image_count: 1 }], truncated: false };
    if (path.endsWith("/credentials")) return { values: {}, configured: [] };
    throw Error(`Unexpected legacy GET ${path}`);
  },
};
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
function dispatchHash() { return Promise.all((listeners.get("hashchange") || []).map(({ fn }) => fn({ type: "hashchange", stopImmediatePropagation() { throw Error("shell should own the gate"); } }))); }
const clickLanguage = () => document.getElementById("langBtn").listeners.get("click").handler();
function count(route) { return requests.filter(r => r.method === "POST" && r.route === route).length; }
let cases = 0;
try {
  for (const name of ["app", "i18n", "grabber", "grabber-library", "grabber-media", "grabber-annotations", "grabber-translation", "annotation-api", "format"]) {
    let source = await readFile(new URL(`${name}.js`, web), "utf8");
    source = source.replace(/(from |import\()"\.\/(.*?)\.js"/g, '$1"./$2.mjs"');
    if (name === "app") source = source.replace(/\nboot\(\);\s*$/, '\nexport { route, navigate, setLanguage };\n');
    if (name === "grabber") {
      source = source.replace('  render();\n  try { await connect(); }', '  ctx.expose({action,capture,change,render,annotations,library});\n  render();\n  try { await connect(); }');
      source = source.replace('  const cleanup = () => {', '  const cleanup = () => {\n    globalThis.navigationTest.cleanup();');
    }
    await writeFile(join(tmp, `${name}.mjs`), source);
  }
  await writeFile(join(tmp, "api.mjs"), 'export const TOKEN="fixture-token"; export const get=(...args)=>globalThis.navigationTest.get(...args); export const post=()=>{throw Error("unexpected legacy POST")};');
  await writeFile(join(tmp, "appearance.mjs"), 'const state={lang:"en",mode:"light",theme:"kohaku"}; export const getAppearance=()=>state; export const patch=(value)=>{Object.assign(state,value);globalThis.navigationTest.patches.push(value)}; export const toggleMode=()=>{};');
  await writeFile(join(tmp, "views.mjs"), 'export async function settingsView(main){main.innerHTML="settings";}');
  await writeFile(join(tmp, "studio.mjs"), 'import {grabberView as view} from "./grabber.mjs"; export const grabberView=(main,ctx)=>view(main,{...ctx,expose:v=>globalThis.navigationTest.expose(v,ctx)});');
  const app = await import(pathToFileURL(join(tmp, "app.mjs")));
  const i18n = await import(pathToFileURL(join(tmp, "i18n.mjs")));
  await i18n.setLang("en");
  async function prepare(kind) {
    fail = null;
    await app.navigate("#/grabber");
    const tab = main.querySelectorAll('[data-act="tab"]').find(node => node.dataset.tab === "library");
    assert.ok(tab);
    await currentView.action({ target: tab });
    await currentView.annotations.open(item.itemId);
    assert.ok(currentView.annotations.translation.state.view, "real translation view loaded");
    if (kind !== "manual") currentView.annotations.capture({ dataset: { scope: "annotation", field: "source" }, value: "navigation source draft" });
    if (kind !== "source") currentView.annotations.capture({ dataset: { scope: "translation", field: `manual:${segment.segmentId}` }, value: "navigation manual draft" });
    assert.equal(currentView.annotations.dirty(), true);
    return { view: currentView, root: main.children[0], mounts, cleanups, language: i18n.lang(), patches: patches.length, writes: requests.length, toast: document.getElementById("toast").children.length };
  }
  const entries = {
    direct: () => app.route(),
    sameHash: () => app.navigate("#/grabber"),
    navigate: () => currentCtx.navigate("#/settings"),
    hashchange: () => { location.hash = "#/settings"; return dispatchHash(); },
    languageButton: clickLanguage,
    languageContext: () => currentCtx.setLanguage(i18n.lang() === "en" ? "zh" : "en"),
    rerender: () => currentCtx.rerender(),
  };
  function retained(before, kind) {
    assert.equal(main.children[0], before.root);
    assert.equal(mounts, before.mounts); assert.equal(cleanups, before.cleanups);
    assert.equal(location.hash, "#/grabber"); assert.equal(i18n.lang(), before.language);
    assert.equal(patches.length, before.patches);
    if (kind !== "manual") assert.equal(before.view.annotations.state.editor.fieldDrafts.get(0), "navigation source draft");
    if (kind !== "source") assert.equal(before.view.annotations.translation.state.drafts.get(segment.segmentId), "navigation manual draft");
  }
  for (const kind of ["source", "manual", "both"]) for (const [entry, trigger] of Object.entries(entries)) for (const choice of ["cancel", "discard", "save"]) {
    const before = await prepare(kind), a = before.view.annotations;
    const counts = { manual: count("manual"), plans: count("plans"), apply: count("plans/apply") };
    const pending = trigger(); await flush();
    assert.equal(a.state.gate, true, `${kind}/${entry}/${choice} opens real gate`);
    retained(before, kind);
    await a.action(`annotation-gate-${choice}`);
    if (choice === "save" && kind !== "manual") {
      retained({ ...before, patches: patches.length }, "source");
      assert.equal(a.state.batch.phase, "ready", "save waits for diff approval");
      await a.action("annotation-apply");
    }
    await pending; await flush();
    if (choice === "cancel") {
      retained(before, kind);
      assert.equal(document.getElementById("toast").children.length, before.toast, "cancel does not claim language success");
      a.state.editor.fieldDrafts.clear(); a.translation.state.drafts.clear(); a.translation.state.sourceDirty = false;
    } else {
      assert.equal(cleanups, before.cleanups + 1, "exactly one destruction");
      assert.equal(location.hash, ["navigate", "hashchange"].includes(entry) ? "#/settings" : "#/grabber");
      assert.equal(i18n.lang(), entry.startsWith("language") ? before.language === "en" ? "zh" : "en" : before.language);
      const rendered = mounts; await dispatchHash(); assert.equal(mounts, rendered, "queued stale hash events do not render twice");
    }
    assert.equal(count("manual") - counts.manual, choice === "save" && kind !== "source" ? 1 : 0);
    assert.equal(count("plans") - counts.plans, choice === "save" && kind !== "manual" ? 1 : 0);
    assert.equal(count("plans/apply") - counts.apply, choice === "save" && kind !== "manual" ? 1 : 0);
    cases++;
  }
  // Fail each save stage through the actual global entries. Repeated navigation
  // must neither replace the pending intent nor submit another save.
  for (const [entry, trigger] of Object.entries(entries)) for (const kind of ["source", "manual", "both"]) {
    const before = await prepare(kind), a = before.view.annotations;
    const pending = trigger(); await flush();
    fail = kind === "source" ? "annotation:plans" : "translation:manual";
    await a.action("annotation-gate-save");
    retained(before, kind); assert.equal(a.state.gate, true);
    const n = requests.length;
    assert.equal(await app.navigate("#/settings"), false);
    assert.equal(await app.setLanguage("en"), false);
    assert.equal(await app.route(), false);
    location.hash = "#/settings"; await dispatchHash();
    retained(before, kind); assert.equal(requests.length, n, "no retry on repeated routes");
    await a.action("annotation-gate-cancel"); await pending; await flush();
    retained(before, kind);
    a.state.editor.fieldDrafts.clear(); a.translation.state.drafts.clear(); a.translation.state.sourceDirty = false;
    cases++;
  }
  // Hold an apply response, then reject it: language and route intents cannot
  // tear down the live editor while its existing save is in flight.
  for (const stage of ["plans/apply", "documents"]) {
    const before = await prepare("source"), a = before.view.annotations;
    const pending = clickLanguage(); await flush(); await a.action("annotation-gate-save");
    let release; const promise = new Promise(resolve => { release = resolve; });
    const held = { route: stage, promise, reached: false }; hold = held; fail = `annotation:${stage}`;
    const applying = a.action("annotation-apply"); await flush(); assert.equal(held.reached, true);
    const n = requests.length;
    assert.equal(await app.navigate("#/settings"), false); assert.equal(await app.setLanguage("zh"), false);
    retained(before, "source"); assert.equal(requests.length, n);
    release(); await applying; await flush(); retained(before, "source");
    await a.action("annotation-gate-cancel"); await pending;
    a.state.editor.fieldDrafts.clear(); a.translation.state.sourceDirty = false;
    cases++;
  }
  assert.ok(historyCalls.every(call => call.url.includes("?test=navigation")), "URL search survives rollback and commit");
  main.__studioCleanup?.(); assert.equal(main.__studioBeforeNavigate, null);
  console.log(`app navigation: ${cases} behavioral cases passed; real route/language/grabber gates; no network or user state`);
} finally {
  await rm(tmp, { recursive: true, force: true });
}
