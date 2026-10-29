/* node tests/gbc_ui.mjs — real ESM/DOM, mocked wire only, no processes/models. */
import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { webcrypto } from "node:crypto";
import { installDOM } from "./grabber_dom.mjs";
const web = new URL("../src/kohakumoss_ui/web/", import.meta.url);
const tmp = await mkdtemp(join(tmpdir(), "gbc-ui-"));
const originalFetch = globalThis.fetch;
globalThis.crypto ||= webcrypto;
let tests = 0;
async function test(name, fn) { await fn(); console.log(`ok ${++tests} - ${name}`); }
const ref = { draftId: `draft-${"a".repeat(32)}`, revision: 1, effectiveHash: `sha256:${"b".repeat(64)}` };
const success = (data) => ({ ok: true, status: 200, json: async () => ({ apiVersion: "gbc-ui-v1", ok: true, data }) });
const failure = (code, status = 409) => ({ ok: false, status, json: async () => ({ apiVersion: "gbc-ui-v1", ok: false, error: { code, message: code, details: {} } }) });
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
try {
  for (const file of ["gbc-api", "gbc-draft", "gbc-graph", "gbc-media", "gbc", "knobform", "format"]) {
    const source = await readFile(new URL(`${file}.js`, web), "utf8");
    await writeFile(join(tmp, `${file}.mjs`), source.replaceAll(/"\.\/([^"/]+)\.js"/g, '"./$1.mjs"'));
  }
  await writeFile(join(tmp, "api.mjs"), 'export const TOKEN="fixture-token";');
  const { createGbcApi, draftRef, assertDraft, countText, nextPageOffset } = await import(pathToFileURL(join(tmp, "gbc-api.mjs")));
  const { validateCaption, geometryPatch, gbcDraftView } = await import(pathToFileURL(join(tmp, "gbc-draft.mjs")));
  const { renderTypedDraft } = await import(pathToFileURL(join(tmp, "knobform.mjs")));
  const { overlayBox, mountGraph } = await import(pathToFileURL(join(tmp, "gbc-graph.mjs")));
  const { gbcView } = await import(pathToFileURL(join(tmp, "gbc.mjs")));
  await test("Header-only token, API version, unique request IDs and no eager IO", async () => {
    const calls = []; const client = createGbcApi({ fetcher: async (url, options) => { calls.push({ url, options }); return success({ check: { ok: false } }); } });
    assert.equal(calls.length, 0); await client.get("jobs/logs", { cursor: 0, jobId: "job_a" });
    await client.post("training-drafts/check", { draftRef: ref, confirmExecution: true }); await client.post("training-drafts/check", { draftRef: ref, confirmExecution: true });
    for (const call of calls) { assert.match(call.url, /^\/api\/gbc\/v1\//); assert.ok(!call.url.includes("token")); assert.equal(call.options.headers["X-UI-Token"], "fixture-token"); assert.equal(call.options.credentials, "omit"); }
    const first = JSON.parse(calls[1].options.body), second = JSON.parse(calls[2].options.body);
    assert.equal(first.apiVersion, "gbc-ui-v1"); assert.notEqual(first.requestId, second.requestId); assert.deepEqual(first.draftRef, ref);
  });
  await test("No POST retries, inner errors, incompatible version and abort fencing", async () => {
    let calls = 0; const client = createGbcApi({ fetcher: async () => { calls++; throw Error("network secret"); } });
    await assert.rejects(client.post("tasks", {}), (error) => error.code === "OUTCOME_UNKNOWN" && error.requestId && !error.message.includes("secret")); assert.equal(calls, 1);
    await assert.rejects(createGbcApi({ fetcher: async () => failure("BUSY", 200) }).get("settings"), (e) => e.code === "BUSY");
    await assert.rejects(createGbcApi({ fetcher: async () => ({ ok: true, json: async () => ({ apiVersion: "old", ok: true, data: {} }) }) }).get("settings"), (e) => e.code === "VERSION_INCOMPATIBLE");
    const controller = new AbortController(); await assert.rejects(createGbcApi({ signal: controller.signal, fetcher: async () => { controller.abort(); return success({}); } }).get("settings"), (e) => e.name === "AbortError");
    const inner = await createGbcApi({ fetcher: async () => success({ check: { ok: false } }) }).post("training-drafts/check", {}); assert.equal(inner.check.ok, false);
  });
  await test("Typed refs and unknown counters are lossless; byte and row cursors are server-owned", () => {
    assert.deepEqual(draftRef({ draftRef: ref }), ref); assertDraft(ref, ref);
    assert.throws(() => assertDraft(ref, { ...ref, revision: 2 })); assert.throws(() => draftRef({ ...ref, effectiveHash: "bad" }));
    assert.equal(countText(null), "—"); assert.equal(countText(0), "0");
    assert.equal(nextPageOffset({ nextOffset: 4791 }, 0), 4791); assert.equal(nextPageOffset({ nextOffset: 20 }, 0), 20); assert.equal(nextPageOffset({ nextOffset: null }, 20), null); assert.throws(() => nextPageOffset({ nextOffset: 19 }, 20));
    const long = "<script>alert(1)</script>" + "x".repeat(500); const output = renderTypedDraft({ DATASETS: [{ caption: { prefix: long } }], FLIP: false });
    assert.ok(output.includes("x".repeat(500))); assert.ok(output.includes("false")); assert.ok(!output.includes("<script>")); assert.ok(!output.includes("data-knob"));
  });
  await test("Caption schema rejects invalid formats/types, geometry corrections are explicit", () => {
    const cap = { template: "gbc", formats: [{ weight: 1, sections: ["graph", "tags", "nl"] }], tags: { sources: [{ from: "meta:tags.general", weight: 1 }], shuffle: false }, fit: { margin: 0.9, fallback: ["nl"] } };
    assert.equal(validateCaption(cap), cap);
    for (const invalid of [{ ...cap, formats: [] }, { ...cap, formats: [{ weight: 0, sections: ["graph"] }] }, { ...cap, tags: { shuffle: "false" } }, { ...cap, nl: { short: 2 } }, { ...cap, graph: { keep: NaN } }, { ...cap, fit: { margin: 0 } }, { ...cap, template: "a.b" }]) assert.throws(() => validateCaption(invalid));
    const advanced = { ...cap, meta: { artist: { field: "artist", drop: [0.1, 0.3] } }, formats: [{ weight: 4, sections: ["tags", "artist"], requires: ["artist"], drop: { artist: 0, tags: [0.2, 0.6] } }, { weight: 1, sections: ["graph"] }], tags: { ...cap.tags, drop: [0, 0.5] }, graph: { keep: [0.4, 0.9], tag_drop: 0.5 } };
    assert.equal(validateCaption(advanced), advanced);
    for (const invalid of [{ ...cap, tags: { ...cap.tags, drop: [0.6, 0.2] } }, { ...cap, graph: { keep: [-0.1, 0.5] } }, { ...cap, graph: { tag_drop: [0.2, 0.4, 0.6] } }, { ...cap, formats: [{ weight: 1, sections: ["graph"], requires: "graph" }] }, { ...cap, formats: [{ weight: 1, sections: ["graph"], requires: ["artist"] }] }, { ...cap, formats: [{ weight: 1, sections: ["graph"], drop: { nl: 0.2 } }] }, { ...cap, formats: [{ weight: 1, sections: ["graph"], drop: { tags: [1.5, 0.2] } }] }, { ...cap, formats: [{ weight: 1, sections: ["graph"], drop: { tags: 2 } }] }]) assert.throws(() => validateCaption(invalid));
    const patch = { CROP: "random", FLIP: true, PARALLEL: "none", DEVICES: [0] }, fixed = geometryPatch(patch, "square_crop");
    assert.equal(patch.FLIP, true); assert.equal(fixed.FLIP, false); assert.equal(fixed.CROP, "center"); assert.deepEqual(fixed.DEVICES, [0]);
  });
  const stats = { rootWrites: 0, removed: 0, imageSources: [] }; const dom = installDOM(stats);
  const proto = Object.getPrototypeOf(document.createElement("div"));
  Object.defineProperty(proto, "name", { get() { return this.attributes.name || ""; } });
  Object.defineProperty(proto, "style", { get() { return this._style ||= {}; } });
  proto.hasAttribute = function (key) { return key in this.attributes; };
  const originalMatches = proto.matches;
  proto.matches = function (selector) { if (selector.endsWith(":checked")) return this.checked && originalMatches.call(this, selector.slice(0, -8)); return originalMatches.call(this, selector); };
  const listeners = new Map(); document.addEventListener = (type, fn) => listeners.set(type, fn); document.removeEventListener = (type) => listeners.delete(type); document.hidden = false;
  globalThis.window = { confirm: () => true };
  const dictionary = JSON.parse(await readFile(new URL("lang/en.json", web), "utf8")); const missing = new Set();
  const ctx = { t(key, vars = {}) { if (!dictionary[key]) missing.add(key); return (dictionary[key] || key).replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? ""); }, query: () => new URLSearchParams(ref), state: {} };
  const makeRoot = () => { const root = document.createElement("main"); dom.mount(root); return root; };
  const click = (root, selector) => { const target = root.querySelector(selector); assert.ok(target, selector); target.dispatchEvent({ type: "click", bubbles: true, preventDefault() {} }); };
  const timers = new Map(); let timerId = 0;
  globalThis.setTimeout = (fn) => { timers.set(++timerId, fn); return timerId; }; globalThis.clearTimeout = (id) => timers.delete(id);
  await test("Graph handles empty root ID, links edges and clips only displayBbox; no executable graph text", async () => {
    const host = makeRoot(); const graph = { nodes: [{ id: "", label: "root", bbox: { left: -0.001, top: 0, right: 1, bottom: 1 }, displayBbox: { left: 0, top: 0, right: 1, bottom: 1 }, descriptions: ["<img onerror=alert(1)>"] }, { id: "child", label: "entity", bbox: {}, displayBbox: { left: 0.1, top: 0.1, right: 0.4, bottom: 0.4 } }], edges: [{ source: "", target: "child", text: "<script>alert(1)</script>" }], img_size: [100, 80] };
    const saved = JSON.stringify(graph); assert.equal(overlayBox(graph.nodes[0]).left, 0);
    const view = mountGraph(host, ctx); await view.show({ graph });
    assert.ok(!host.querySelector("script")); assert.equal(host.querySelector('[data-node="0"]').getAttribute("aria-pressed"), "true");
    click(host, '[data-node="1"]'); assert.equal(host.querySelector('[data-node="1"]').getAttribute("aria-pressed"), "true"); assert.equal(JSON.stringify(graph), saved); view.cleanup();
  });
  await test("Dedicated new draft renders full typed values, explicit failed check stays failed", async () => {
    const calls = []; globalThis.fetch = async (url, options) => { calls.push({ url, options }); return options.method === "POST" ? success({ check: { draftRef: ref, effectiveHash: ref.effectiveHash, ok: false, validation: { status: "invalid" }, output: "Missing real weights" } }) : success({ draft: { draftRef: ref, values: { DATASETS: [{ source: "gbc", caption: { prefix: "x".repeat(500) } }] } } }); };
    const root = makeRoot(); await gbcDraftView(root, ctx); assert.equal(calls.length, 1); assert.ok(root.textContent.includes("x".repeat(500)));
    click(root, "[data-draft-check]"); click(root, "[data-draft-check]"); await tick(); assert.equal(calls.filter((c) => c.options.method === "POST").length, 1); assert.match(root.querySelector("[data-draft-review-status]").textContent, /validation failed/);
    assert.ok(!calls.some((c) => /run\/start|queue|notebook/.test(c.url))); root.__studioCleanup();
  });
  await test("Late new-draft response cannot replace another route", async () => {
    let release; globalThis.fetch = () => new Promise((resolve) => { release = resolve; });
    const root = makeRoot(), pending = gbcDraftView(root, ctx); root.__studioCleanup(); root.innerHTML = "<p>Another route</p>"; release(success({ draft: { draftRef: ref, values: {} } })); await pending; assert.equal(root.textContent, "Another route");
  });
  await test("Workbench loads only safe GET metadata, gates trust and cancels late refresh", async () => {
    const calls = []; const settings = { revision: 0, roots: [], runtimes: [] };
    let hold = null;
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options }); const route = url.split("/v1/")[1].split("?")[0];
      if (hold && route === "capabilities") return new Promise((resolve) => { hold.resolve = resolve; });
      return success(route === "capabilities" ? { features: { replay: true, trainingDrafts: true } } : route === "settings" ? { settings } : { [route]: [] });
    };
    const root = makeRoot(); await gbcView(root, ctx); assert.equal(calls.length, 5); assert.ok(calls.every((c) => c.options.method === "GET"));
    click(root, '[data-act="root-add"]'); await tick(); assert.equal(calls.length, 5); assert.match(root.querySelector("[data-status]").textContent, /trust confirmation/);
    const pending = hold = {}; click(root, '[data-act="refresh"]'); await tick(); root.__studioCleanup(); root.innerHTML = "<p>Other view</p>"; pending.resolve(success({ features: {} })); await tick(); assert.equal(root.textContent, "Other view"); assert.equal(listeners.size, 0); assert.equal(timers.size, 0);
  });
  await test("Explicit task/check/start/stop, byte paging, dataset rows, immutable drafts and preview", async () => {
    const requests = [], settings = { revision: 4, roots: [{ rootId: "root_in", purpose: "input", path: "/fixture/input" }, { rootId: "root_out", purpose: "output", path: "/fixture/output" }], runtimes: [{ runtimeId: "runtime_gen", role: "generator", python: "/fixture/gen/python" }, { runtimeId: "runtime_data", role: "data", python: "/fixture/data/python" }] };
    const fixtureGraph = { nodes: [{ id: "", label: "image", descriptions: [{ text: "fixture" }], bbox: { left: 0, top: 0, right: 1, bottom: 1 }, displayBbox: { left: 0, top: 0, right: 1, bottom: 1 } }], edges: [], img_size: [100, 100] };
    const ds = { datasetId: "dataset_a", revision: 1, manifestHash: ref.effectiveHash, declaredSamples: 2, manifestRows: 2, validatedSamples: 2, actualBytes: 100, validation: { state: "valid", complete: true } };
    let currentTask = null, currentJob = null, createdDraft = null, draftSerial = 0;
    globalThis.fetch = async (url, options) => {
      const u = new URL(url, "http://localhost"), route = u.pathname.split("/v1/")[1], body = options.body ? JSON.parse(options.body) : null;
      requests.push({ route, query: u.searchParams, body }); let data;
      if (route === "capabilities") data = { features: { replay: true, trainingDrafts: true, imports: true, exports: true } };
      else if (route === "settings") data = { settings };
      else if (route === "tasks" && body) { currentTask = { taskId: "task_a", revision: 1, inputManifestHash: ref.effectiveHash, validationId: null, total: 2 }; data = { task: currentTask }; }
      else if (route === "tasks") data = u.searchParams.has("taskId") ? { task: currentTask } : { tasks: [] };
      else if (route === "jobs") data = u.searchParams.has("jobId") ? { job: currentJob } : { jobs: [] };
      else if (route === "tasks/check") { currentJob = { jobId: "job_check", revision: 1, taskId: "task_a", status: "running", kind: "check", counts: {} }; data = { job: currentJob }; }
      else if (route === "jobs/start") { assert.equal(body.expectedRevision, 3); assert.equal(body.validationId, "validation_a"); assert.equal(body.manifestHash, ref.effectiveHash); currentJob = { jobId: "job_gen", revision: 1, taskId: "task_a", status: "running", kind: "generate", counts: { total: 2, observedRecords: 0, validated: null } }; data = { job: currentJob }; }
      else if (route === "jobs/stop") { assert.equal(body.force, false); currentJob = { ...currentJob, revision: 2, status: "stopping" }; data = { job: currentJob }; }
      else if (route === "jobs/logs") { const cursor = Number(u.searchParams.get("cursor")); data = { text: cursor === 0 ? "safe process log\n" : "", nextCursor: cursor === 0 ? 17 : cursor, dropped: 0, terminal: !["running", "stopping"].includes(currentJob.status) }; }
      else if (route === "graphs") data = { items: [{ sampleId: "sample_a", graph: fixtureGraph }], nextOffset: Number(u.searchParams.get("offset")) === 0 ? 500 : null };
      else if (route === "datasets") data = u.searchParams.has("datasetId") ? { dataset: ds } : { datasets: [ds] };
      else if (route === "datasets/shards") data = { shards: [{ name: "shard_00000", declaredSamples: 2, manifestRows: 2, actualBytes: 100 }], nextOffset: null };
      else if (route === "datasets/page") data = { items: [{ key: "key_a", shard: "shard_00000", graph: fixtureGraph }], nextOffset: Number(u.searchParams.get("offset")) === 0 ? 2 : null };
      else if (route === "training-drafts") { assert.equal(body.knobPatch.FLIP, false); assert.equal(body.knobPatch.CROP, "center"); assert.ok(!("DEVICES" in body.knobPatch)); createdDraft = { draftRef: { ...ref, draftId: `draft-${String(++draftSerial).padStart(32, "0")}` }, datasetRef: { datasetId: ds.datasetId, revision: ds.revision }, shards: body.shards, values: { DATASETS: [{ source: "gbc", caption: body.captionOptions }] } }; data = { draft: createdDraft }; }
      else if (route === "captions/preview") { assert.deepEqual(body.draftRef, createdDraft.draftRef); assert.equal(body.sampleId, "shard_00000/key_a"); data = { preview: { draftRef: createdDraft.draftRef, text: "<script>plain caption</script>", geometry: { target: { width: 100, height: 100 } }, budget: { hardTokenLimit: false } } }; }
      else throw Error(`unexpected ${url}`);
      return success(structuredClone(data));
    };
    const root = makeRoot(); await gbcView(root, ctx);
    const field = (name) => root.querySelector(`[name="${name}"]`);
    const act = async (name) => { click(root, `[data-act="${name}"]`); await tick(); };
    const runTimer = async () => { const entry = [...timers.entries()].at(-1); assert.ok(entry); timers.delete(entry[0]); await entry[1](); await tick(); };
    for (const [name, v] of Object.entries({ taskInput: "root_in", taskPath: "trace", taskOutput: "root_out", taskRuntime: "runtime_gen", dataRuntime: "runtime_data" })) field(name).value = v;
    await act("task-create"); assert.equal(requests.filter((r) => r.body).length, 1); assert.equal(root.querySelector('[data-act="task-start"]').disabled, true);
    await act("task-check"); currentTask.revision = 3; currentTask.validationId = "validation_a"; currentJob.status = "done"; currentJob.revision = 2; await runTimer();
    assert.equal(root.querySelector('[data-act="task-start"]').disabled, false); await act("task-start"); await act("job-stop"); assert.match(root.querySelector("[data-job]").textContent, /Stop requested/);
    currentJob.status = "stopped"; currentJob.revision = 3; currentJob.resultArtifactId = "artifact_a"; await runTimer(); assert.match(root.querySelector("[data-job]").textContent, /canceled/); assert.ok(!root.querySelector("[data-job]").textContent.includes("%"));
    await act("graphs"); await act("samples-next"); assert.equal(requests.filter((r) => r.route === "graphs").at(-1).query.get("offset"), "500");
    field("dataset").value = "dataset_a"; await act("dataset-select"); assert.ok(root.querySelector("[data-shards]").textContent.includes("—"));
    const shard = root.querySelector('[data-shard="0"]'); shard.checked = true; shard.dispatchEvent({ type: "change", bubbles: true });
    await act("samples"); await act("samples-next"); assert.equal(requests.filter((r) => r.route === "datasets/page").at(-1).query.get("offset"), "2");
    field("sample").value = "0"; await act("sample-select");
    field("baseConfig").value = "configs/user/fixture.py"; field("advanced").checked = true; field("captionJson").value = "{bad"; field("confirmConfig").checked = true;
    const before = requests.length; click(root, '[data-draft-act="create"]'); await tick(); assert.equal(requests.length, before); assert.equal(field("captionJson").value, "{bad");
    field("captionJson").value = JSON.stringify({ template: "gbc", formats: [{ weight: 1, sections: ["graph"] }], prefix: "x".repeat(500) });
    click(root, '[data-draft-act="geometry"]'); await tick(); click(root, '[data-draft-act="create"]'); click(root, '[data-draft-act="create"]'); await tick(); assert.equal(draftSerial, 1);
    assert.ok(root.querySelector("[data-draft-result]").innerHTML.includes("effectiveHash")); click(root, '[data-draft-act="preview"]'); await tick(); assert.ok(root.querySelector("[data-caption-text]").textContent.includes("<script>")); assert.ok(!root.querySelector("[data-caption-text]").querySelector("script"));
    field("captionJson").dispatchEvent({ type: "input", bubbles: true }); assert.equal(root.querySelector('[data-draft-act="preview"]').disabled, true);
    field("confirmConfig").checked = true; click(root, '[data-draft-act="create"]'); await tick(); assert.equal(draftSerial, 2);
    await act("dataset-select"); assert.equal(root.querySelector('[data-draft-act="preview"]').disabled, true); assert.equal(field("sample").innerHTML.includes("key_a"), false);
    assert.ok(!requests.some((r) => /run\/start|queue|notebook/.test(r.route))); root.__studioCleanup(); assert.equal(timers.size, 0);
  });
  await test("New GBC visible keys exist in EN and ZH", async () => {
    assert.deepEqual([...missing], []); const zh = JSON.parse(await readFile(new URL("lang/zh.json", web), "utf8")); assert.deepEqual(Object.keys(dictionary).sort(), Object.keys(zh).sort());
  });
  console.log(`${tests} GBC UI tests passed`);
} finally { globalThis.fetch = originalFetch; await rm(tmp, { recursive: true, force: true }); }
