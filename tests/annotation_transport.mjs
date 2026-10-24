/* node tests/annotation_transport.mjs: real ESM transport, memory-only fetch.
 * No service, user state, credentials storage, provider or download access. */
import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { webcrypto } from "node:crypto";
import { id, hash, settings, dictionary, documentFixture, structuredFixture, viewFixture, planFixture, jobFixture, targetFixture, translationSegment } from "./translation_fixture.mjs";

const tmp = await mkdtemp(join(tmpdir(), "annotation-transport-"));
const originalFetch = globalThis.fetch;
if (!globalThis.crypto) globalThis.crypto = webcrypto;
// A storage access would fail even if hidden behind an optional chaining check.
for (const name of ["localStorage", "sessionStorage"]) Object.defineProperty(globalThis, name, { configurable: true, get() { throw Error(`forbidden ${name}`); } });
let count = 0, calls = [];
const reply = (status, envelope) => ({ status, ok: status >= 200 && status < 300, json: async () => envelope });
const failure = (status, code, retryable = false, details = {}) => reply(status, { ok: false, error: { code, message: `fixture ${code}`, retryable, details } });
const success = (data, status = 200) => reply(status, { ok: true, data });
function mock(fn) {
  calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return fn(url, options); };
}
async function test(name, fn) { await fn(); console.log(`ok ${++count} - ${name}`); }
async function rejects(promise, status, code, retryable = false, name) {
  await assert.rejects(promise, error => {
    assert.ok(error instanceof Error); assert.equal(typeof error.message, "string");
    assert.equal(error.status, status); assert.equal(error.code, code); assert.equal(error.retryable, retryable);
    assert.ok(error.details && typeof error.details === "object" && !Array.isArray(error.details));
    if (name) assert.equal(error.name, name);
    return true;
  });
}
const bodyOf = call => JSON.parse(call.options.body);
const apply = { planId: id("plan", 1), manifestHash: hash(999), confirm: true };
const start = { planId: id("plan", 1), manifestHash: hash(999), confirmRemote: false, confirmCost: false, acceptMaximumOutputTokens: 0 };
try {
  const source = await readFile(new URL("../src/kohakumoss_ui/web/annotation-api.js", import.meta.url), "utf8");
  await writeFile(join(tmp, "annotation-api.mjs"), source.replace('"./api.js"', '"./api.mjs"'));
  await writeFile(join(tmp, "api.mjs"), 'export const TOKEN="synthetic-token";');
  mock(() => { throw Error("import/client creation must not fetch"); });
  const { requestAnnotation: annotation, requestTranslation: translation, createAnnotationClient, wireError } = await import(pathToFileURL(join(tmp, "annotation-api.mjs")));
  await test("import and client creation perform no IO or secret storage", async () => {
    const client = createAnnotationClient(); client.dispose(); assert.equal(calls.length, 0);
    for (const error of [null, "legacy", [], 4]) assert.equal(wireError(502, error).code, "UPSTREAM_PROTOCOL");
  });
  await test("fetch network, abort and timeout have complete safe error shapes", async () => {
    for (const request of [annotation, translation]) for (const [error, status, code, retryable] of [
      [new TypeError("secret-bearing native diagnostics"), 503, "UPSTREAM_UNAVAILABLE", true],
      [new DOMException("cancel", "AbortError"), 0, "REQUEST_ABORTED", false],
      [new DOMException("timeout", "TimeoutError"), 504, "UPSTREAM_TIMEOUT", true],
    ]) {
      mock(() => { throw error; });
      await rejects(request("GET", request === annotation ? "documents" : "view", { documentId: id("doc", 1) }), status, code, retryable, error.name === "AbortError" ? "AbortError" : undefined);
      assert.equal(calls.length, 1);
    }
  });
  await test("body read network/abort/timeout remain transport failures", async () => {
    for (const [error, status, code, retryable] of [[new TypeError("stream interrupted"), 503, "UPSTREAM_UNAVAILABLE", true], [new DOMException("abort", "AbortError"), 0, "REQUEST_ABORTED", false], [new DOMException("timeout", "TimeoutError"), 504, "UPSTREAM_TIMEOUT", true]]) {
      mock(() => ({ status: 200, ok: true, json: async () => { throw error; } }));
      await rejects(translation("GET", "settings"), status, code, retryable);
    }
  });
  await test("non-JSON, missing data and malformed envelopes always become 502", async () => {
    const invalid = [null, [], true, "html", {}, { ok: true }, { ok: true, data: null }, { ok: true, data: [] }, { ok: true, data: 1 }, { ok: "true", data: {} }, { ok: true, data: {}, error: {} }, { ok: false, error: "legacy" }, { ok: false, error: { code: "BUSY", message: "busy", details: [], retryable: true } }, { ok: false, error: { code: "BUSY", message: "busy", details: {}, retryable: "true" } }];
    for (const status of [200, 409, 504]) {
      for (const envelope of invalid) {
        mock(() => reply(status, envelope)); await rejects(translation("GET", "settings"), 502, "UPSTREAM_PROTOCOL");
      }
      mock(() => new Response("<html>not JSON</html>", { status }));
      await rejects(translation("GET", "settings"), 502, "UPSTREAM_PROTOCOL");
    }
    mock(() => failure(200, "BUSY", true)); await rejects(translation("GET", "settings"), 502, "UPSTREAM_PROTOCOL");
    mock(() => success(settings, 409)); await rejects(translation("GET", "settings"), 502, "UPSTREAM_PROTOCOL");
  });
  await test("valid backend error status/message/code/details/retryable survive", async () => {
    for (const [status, code, retryable] of [[400, "INVALID_REQUEST", false], [403, "AUTH_REQUIRED", false], [404, "NOT_FOUND", false], [409, "BUSY", true], [409, "VERSION_CONFLICT", false], [410, "PLAN_EXPIRED", false], [422, "INVALID_DOCUMENT", false], [429, "JOB_LIMIT", true], [500, "INTERNAL", false], [502, "UPSTREAM_PROTOCOL", false], [503, "UPSTREAM_UNAVAILABLE", true], [504, "UPSTREAM_TIMEOUT", true]]) {
      const details = { requestId: id("req", 1) };
      mock(() => failure(status, code, retryable, details));
      try { await annotation("GET", "plans", { planId: id("plan", 1) }); assert.fail("expected backend error"); }
      catch (error) { assert.equal(error.status, status); assert.equal(error.code, code); assert.equal(error.message, `fixture ${code}`); assert.equal(error.details, details); assert.equal(error.retryable, retryable); }
    }
  });
  await test("fixed route, payload and requestId checks reject before fetch", async () => {
    mock(() => { throw Error("must not fetch"); });
    const client = createAnnotationClient();
    for (const [method, route] of [["DELETE", "jobs"], ["get", "jobs"], ["toString", "jobs"], ["GET", "../settings"], ["GET", "jobs?jobId=x"], ["GET", "https://example.invalid"], ["GET", "jobs/start"], ["GET", ["jobs"]]]) await rejects(annotation(method, route, {}), 400, "INVALID_REQUEST");
    for (const payload of [null, [], "body", 1]) await rejects(client.annotation("POST", "plans/apply", payload), 400, "INVALID_REQUEST");
    for (const requestId of ["", null, 1, "req_short", `req_${"A".repeat(32)}`, `req_${"a".repeat(33)}`]) await rejects(client.annotation("POST", "plans/apply", { ...apply, requestId }), 400, "INVALID_REQUEST");
    await rejects(annotation("POST", "plans/apply", apply), 400, "INVALID_REQUEST");
    await rejects(translation("GET", "settings", { requestId: id("req", 1) }), 400, "INVALID_REQUEST");
    const circular = { ...apply }; circular.self = circular;
    await rejects(client.annotation("POST", "plans/apply", circular), 400, "INVALID_REQUEST");
    assert.equal(calls.length, 0); client.dispose();
  });
  await test("complete legal document/view/plan/job responses pass unchanged", async () => {
    const doc = structuredFixture(), plan = planFixture(doc), job = jobFixture();
    for (const [request, route, payload, data] of [
      [annotation, "documents", { documentId: doc.documentId, expectedVersion: doc.sourceVersion }, doc],
      [translation, "settings", {}, settings], [translation, "dictionary", {}, dictionary],
      [translation, "view", { documentId: doc.documentId, sourceLanguage: "en", targetLanguage: "zh-CN" }, viewFixture(doc)],
      [translation, "plans", { planId: plan.planId }, plan],
      [translation, "plans/segments", { planId: plan.planId, offset: 0, limit: 60 }, { planId: plan.planId, total: 1, offset: 0, limit: 60, segments: [targetFixture(doc, doc.fields[0].segments[0])] }],
      [translation, "jobs", { jobId: job.jobId }, job],
    ]) {
      const envelope = { ok: true, data }; mock(() => reply(200, envelope));
      assert.equal(await request("GET", route, payload), envelope);
      const { url, options } = calls[0];
      assert.ok(url.startsWith(request === annotation ? "/api/grabber/annotations/" : "/api/translation/"));
      assert.equal(options.headers["X-UI-Token"], "synthetic-token"); assert.equal(options.credentials, "same-origin"); assert.equal(options.cache, "no-store"); assert.equal(options.body, undefined);
      assert.deepEqual(Object.fromEntries(new URL(url, "http://fixture.invalid").searchParams), Object.fromEntries(Object.entries(payload).map(([k, v]) => [k, String(v)])));
    }
  });
  await test("complete POST payloads stay flat and explicit IDs are preserved", async () => {
    const doc = documentFixture(), segment = doc.fields[0].segments[0], requestId = id("req", 42);
    const client = createAnnotationClient();
    for (const [call, route, payload, data, status] of [
      [client.translation, "settings", { expectedConfigVersion: settings.configVersion, targetLanguage: "ja", enabled: false }, settings, 200],
      [client.translation, "credentials", { expectedConfigVersion: settings.configVersion, apiKey: "synthetic-secret", clear: false }, { configVersion: id("cfg", 2), hasKey: true, keyHint: "configured" }, 200],
      [client.translation, "manual", { documentId: doc.documentId, expectedSourceVersion: doc.sourceVersion, expectedRevision: 4, segmentId: segment.segmentId, targetText: "manual", sourceLanguage: "en", targetLanguage: "zh-CN" }, { revision: 5, segment: translationSegment(segment, { engine: "manual", manual: true, dictionaryVersion: null, targetText: "manual" }) }, 200],
      [client.translation, "jobs/start", start, { job: jobFixture() }, 202],
      [client.annotation, "plans/apply", apply, { job: jobFixture("running", "apply") }, 202],
    ]) {
      mock(() => success(data, status)); const input = { ...payload, requestId };
      assert.deepEqual(await call("POST", route, input), { ok: true, data });
      assert.deepEqual(bodyOf(calls[0]), input); assert.equal(calls[0].options.headers["Content-Type"], "application/json"); assert.equal(calls[0].options.signal, undefined);
    }
    client.dispose();
  });
  await test("unknown POST reconciles only on explicit same-payload retry, then GETs known job", async () => {
    for (const [domain, route, payload] of [["annotation", "plans/apply", apply], ["translation", "jobs/start", start]]) {
      for (const outcome of [() => { throw new TypeError("offline"); }, () => { throw new DOMException("timeout", "TimeoutError"); }, () => { throw new DOMException("aborted", "AbortError"); }, () => failure(504, "UPSTREAM_TIMEOUT", true), () => failure(409, "BUSY", true), () => failure(409, "RECOVERY_REQUIRED"), () => reply(200, { ok: true })]) {
        const client = createAnnotationClient(), job = jobFixture("running", domain === "annotation" ? "apply" : "translation");
        let mode = "unknown";
        mock((_url, options) => mode === "unknown" ? outcome() : success(options.method === "POST" ? { job } : job, options.method === "POST" ? 202 : 200));
        await assert.rejects(client[domain]("POST", route, payload)); await Promise.resolve(); assert.equal(calls.length, 1, "no automatic mutation retry");
        const first = bodyOf(calls[0]); assert.match(first.requestId, /^req_[0-9a-f]{32}$/); assert.ok(!Object.hasOwn(payload, "requestId"));
        mode = "receipt";
        assert.deepEqual((await client[domain]("POST", route, { ...payload })).data, { job }); assert.deepEqual(bodyOf(calls[1]), first);
        assert.deepEqual((await client[domain]("GET", "jobs", { jobId: job.jobId })).data, job); assert.equal(calls.length, 3);
        // A successful receipt ends the uncertain operation; a later explicit call is new.
        await client[domain]("POST", route, payload); assert.notEqual(bodyOf(calls[3]).requestId, first.requestId); client.dispose();
      }
    }
  });
  await test("terminal client errors release generated key; domain/route failures never share IDs", async () => {
    const client = createAnnotationClient(); mock(() => failure(409, "VERSION_CONFLICT"));
    await assert.rejects(client.annotation("POST", "plans/apply", apply)); await assert.rejects(client.annotation("POST", "plans/apply", apply));
    assert.notEqual(bodyOf(calls[0]).requestId, bodyOf(calls[1]).requestId);
    mock(() => failure(504, "UPSTREAM_TIMEOUT", true));
    for (const domain of ["annotation", "translation"]) await assert.rejects(client[domain]("POST", "jobs/stop", { jobId: id("job", 1) }));
    assert.notEqual(bodyOf(calls[0]).requestId, bodyOf(calls[1]).requestId); client.dispose();
  });
  await test("dispose aborts pending reads, keeps submitted write and prevents subsequent IO", async () => {
    const client = createAnnotationClient(); let finishWrite;
    mock((_url, options) => options.method === "GET" ? new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true })) : new Promise(resolve => { finishWrite = resolve; }));
    const read = client.translation("GET", "settings"), readCheck = rejects(read, 0, "REQUEST_ABORTED", false, "AbortError");
    const write = client.translation("POST", "jobs/start", start); client.dispose();
    await readCheck; assert.equal(calls[0].options.signal.aborted, true); assert.equal(calls[1].options.signal, undefined);
    const job = jobFixture(); finishWrite(success({ job }, 202)); assert.deepEqual((await write).data, { job });
    await rejects(client.translation("GET", "settings"), 0, "REQUEST_ABORTED", false, "AbortError"); assert.equal(calls.length, 2);
  });
  console.log(`annotation transport: PASS ${count} groups (network/abort/protocol/status/full wire/reconciliation)`);
} finally {
  globalThis.fetch = originalFetch;
  for (const name of ["localStorage", "sessionStorage"]) delete globalThis[name];
  await rm(tmp, { recursive: true, force: true });
}
