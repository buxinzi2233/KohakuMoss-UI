/* Real media adapter and graph lifecycle; memory-only wire, no local service. */
import assert from "node:assert/strict";
import { readFile, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { installDOM } from "./grabber_dom.mjs";
const web = new URL("../src/kohakumoss_ui/web/", import.meta.url);
const tmp = await mkdtemp(join(tmpdir(), "gbc-media-ui-"));
const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aVxkAAAAASUVORK5CYII=";
const media = { mimeType: "image/png", encoding: "base64", base64: png, width: 1, height: 1, sourceWidth: 1, sourceHeight: 1, size: "detail", orientation: "stored-image" };
const graph = { img_size: [1, 1], nodes: [{ id: "", label: "root", bbox: { left: 0, top: 0, right: 1, bottom: 1 }, displayBbox: { left: 0, top: 0, right: 1, bottom: 1 }, descriptions: [] }], edges: [] };
const reply = (data) => ({ ok: true, json: async () => ({ apiVersion: "gbc-ui-v1", ok: true, data }) });
let count = 0;
async function test(name, fn) { await fn(); console.log(`ok ${++count} - ${name}`); }
try {
  for (const name of ["gbc-api", "gbc-media", "gbc-graph", "format"]) await writeFile(join(tmp, `${name}.mjs`), (await readFile(new URL(`${name}.js`, web), "utf8")).replaceAll(/"\.\/([^"/]+)\.js"/g, '"./$1.mjs"'));
  await writeFile(join(tmp, "api.mjs"), 'export const TOKEN="fixture-token";');
  const { mediaBlob, loadGraphMedia } = await import(pathToFileURL(join(tmp, "gbc-media.mjs")));
  const { mountGraph } = await import(pathToFileURL(join(tmp, "gbc-graph.mjs")));
  await test("Dataset/artifact media use exact documented references and header-only auth", async () => {
    const calls = [], controller = new AbortController();
    const options = { fetcher: async (url, request) => { calls.push({ url, request }); return reply({ media }); } };
    const blob = await loadGraphMedia({ graph, shard: "shard_00000", key: "100", img_path: "https://must-not-fetch.invalid" }, { kind: "dataset", dataset: { datasetId: "dataset_1" }, dataRuntimeId: "runtime_1" }, controller.signal, options);
    assert.equal(blob.type, "image/png"); assert.equal(blob.size, Buffer.from(png, "base64").length);
    await loadGraphMedia({ graph, artifactId: "artifact_1", sampleId: "sample_1" }, { kind: "artifact", job: { resultArtifactId: "artifact_1" } }, controller.signal, options);
    assert.equal(calls[0].url, "/api/gbc/v1/datasets/media?datasetId=dataset_1&dataRuntimeId=runtime_1&shard=shard_00000&key=100&size=detail");
    assert.equal(calls[1].url, "/api/gbc/v1/graphs/media?artifactId=artifact_1&sampleId=sample_1&size=detail");
    for (const { url, request } of calls) { assert.equal(request.method, "GET"); assert.equal(request.headers["X-UI-Token"], "fixture-token"); assert.equal(request.credentials, "omit"); assert.equal(request.signal, controller.signal); assert.ok(!/token|path|https/.test(url)); }
    await assert.rejects(loadGraphMedia({ graph, artifactId: "wrong", sampleId: "sample_1" }, { kind: "artifact", job: { resultArtifactId: "artifact_1" } }, controller.signal, options));
    assert.equal(calls.length, 2);
  });
  await test("Media DTO bounds, MIME, encoding, PNG dimensions and original source geometry fail closed", () => {
    assert.equal(mediaBlob(media, graph).type, "image/png");
    for (const patch of [{ mimeType: "image/svg+xml" }, { encoding: "html" }, { orientation: "rotated" }, { width: 1601 }, { height: 0 }, { width: true }, { base64: "<img onerror=alert(1)>" }, { base64: "A".repeat(5592412) }, { sourceWidth: 2 }, { size: "thumb" }]) assert.throws(() => mediaBlob({ ...media, ...patch }, graph));
    const wrongSize = Buffer.from(png, "base64"); wrongSize.writeUInt32BE(2, 16); assert.throws(() => mediaBlob({ ...media, base64: wrongSize.toString("base64") }, graph));
    assert.throws(() => mediaBlob({ ...media, base64: btoa("<html>".repeat(10)) }, graph));
  });
  await test("Media failures and canceled response do not fall back to source paths", async () => {
    let calls = 0; const controller = new AbortController();
    const options = { fetcher: async () => { calls++; return { ok: false, json: async () => ({ apiVersion: "gbc-ui-v1", ok: false, error: { code: "DEPENDENCY_MISSING" } }) }; } };
    const row = { graph, artifactId: "artifact_1", sampleId: "sample_1", img_path: "/private/file.png" }, context = { kind: "artifact" };
    await assert.rejects(loadGraphMedia(row, context, controller.signal, options), (error) => error.code === "DEPENDENCY_MISSING"); assert.equal(calls, 1);
    await assert.rejects(loadGraphMedia(row, context, controller.signal, { fetcher: async () => { controller.abort(); return reply({ media }); } }), (error) => error.name === "AbortError");
  });
  const stats = { rootWrites: 0, removed: 0, imageSources: [] }, dom = installDOM(stats);
  const dictionaries = JSON.parse(await readFile(new URL("lang/en.json", web), "utf8"));
  const ctx = { t(key) { assert.ok(dictionaries[key], key); return dictionaries[key]; } };
  const makeRoot = () => { const el = document.createElement("section"); dom.mount(el); return el; };
  let serial = 0; const revoked = []; URL.createObjectURL = () => `blob:fixture-${++serial}`; URL.revokeObjectURL = (url) => revoked.push(url);
  await test("Graph clears old images on loading/failure, handles image decode failure and revokes Blob URLs", async () => {
    const host = makeRoot(); let fail = false, signal;
    const view = mountGraph(host, ctx, { media: async (_row, _context, currentSignal) => { signal = currentSignal; if (fail) throw Object.assign(Error("missing"), { code: "NOT_FOUND" }); return mediaBlob(media, graph); } });
    await view.show({ graph }); const image = host.querySelector("img"), firstUrl = image.src;
    assert.equal(image.hidden, true); assert.match(host.querySelector("[data-media-status]").textContent, /Loading/);
    image.dispatchEvent({ type: "load" }); assert.equal(image.hidden, false);
    fail = true; await view.show({ graph }); assert.ok(revoked.includes(firstUrl)); assert.equal(host.querySelector("img").src, undefined); assert.equal(host.querySelector("img").hidden, true); assert.match(host.querySelector("[data-media-status]").textContent, /not found/);
    fail = false; await view.show({ graph }); const badImage = host.querySelector("img"), badUrl = badImage.src; badImage.dispatchEvent({ type: "error" }); assert.equal(badImage.hidden, true); assert.equal(badImage.src, undefined); assert.ok(revoked.includes(badUrl));
    await view.show({ graph }); const lastUrl = host.querySelector("img").src; view.cleanup(); assert.ok(signal.aborted); assert.ok(revoked.includes(lastUrl));
  });
  await test("Late media cannot replace a newer sample or departed route", async () => {
    const host = makeRoot(); let release, captured, attempt = 0;
    const view = mountGraph(host, ctx, { media: (_row, _context, signal) => { if (++attempt === 1) { captured = signal; return new Promise((resolve) => { release = resolve; }); } return Promise.resolve(mediaBlob(media, graph)); } });
    const first = view.show({ graph }); await view.show({ graph }); const currentUrl = host.querySelector("img").src; assert.ok(captured.aborted); release(mediaBlob(media, graph)); await first; assert.equal(host.querySelector("img").src, currentUrl);
    view.cleanup(); const host2 = makeRoot(); let late;
    const view2 = mountGraph(host2, ctx, { media: () => new Promise((resolve) => { late = resolve; }) });
    const pending = view2.show({ graph }); view2.cleanup(); host2.innerHTML = "<p>Next route</p>"; late(mediaBlob(media, graph)); await pending; assert.equal(host2.textContent, "Next route");
  });
  console.log(`${count} GBC media UI tests passed`);
} finally { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; await rm(tmp, { recursive: true, force: true }); }
