/* Immutable typed draft workflow, deliberately isolated from legacy overrides. */
import { TOKEN } from "./api.js";
import { esc } from "./format.js";
import { renderTypedDraft } from "./knobform.js";
import { assertDraft, createGbcApi, draftRef, errorText, GbcError } from "./gbc-api.js";

const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
function checkObject(value, allowed, path, required = []) {
  if (!object(value) || Object.keys(value).some((key) => !allowed.includes(key)) || required.some((key) => !(key in value))) throw new GbcError("INVALID_REQUEST", path);
}
function range(value, path, min = 0, max = 1) { if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) throw new GbcError("INVALID_REQUEST", path); }
function rate(value, path) { if (Array.isArray(value)) { if (value.length !== 2) throw new GbcError("INVALID_REQUEST", path); range(value[0], path); range(value[1], path); if (value[0] > value[1]) throw new GbcError("INVALID_REQUEST", path); return; } range(value, path); }
export function validateCaption(value) {
  checkObject(value, ["template", "formats", "meta", "tags", "nl", "graph", "fit", "prefix", "suffix", "list_sep", "section_sep", "text_sep", "underscores"], "captionOptions", ["template", "formats"]);
  if (value.template !== "gbc") throw new GbcError("INVALID_REQUEST", "captionOptions.template");
  if (!Array.isArray(value.formats) || !value.formats.length || value.formats.length > 128) throw new GbcError("INVALID_REQUEST", "captionOptions.formats");
  if (value.meta !== undefined && !object(value.meta)) throw new GbcError("INVALID_REQUEST", "captionOptions.meta");
  const metaSections = new Set(Object.keys(value.meta || {}));
  const sections = new Set(["tags", "nl", "graph", ...metaSections]);
  value.formats.forEach((fmt, i) => {
    checkObject(fmt, ["weight", "sections", "requires", "drop"], `formats[${i}]`, ["weight", "sections"]); range(fmt.weight, `formats[${i}].weight`, 1e-12, 1e12);
    if (!Array.isArray(fmt.sections) || !fmt.sections.length || fmt.sections.some((key) => !sections.has(key))) throw new GbcError("INVALID_REQUEST", `formats[${i}].sections`);
    if (fmt.requires !== undefined && (!Array.isArray(fmt.requires) || fmt.requires.some((key) => typeof key !== "string" || !metaSections.has(key)))) throw new GbcError("INVALID_REQUEST", `formats[${i}].requires`);
    if (fmt.drop !== undefined) { if (!object(fmt.drop)) throw new GbcError("INVALID_REQUEST", `formats[${i}].drop`); Object.entries(fmt.drop).forEach(([key, dropRate]) => { if (key !== "tags" && !metaSections.has(key)) throw new GbcError("INVALID_REQUEST", `formats[${i}].drop`); rate(dropRate, `formats[${i}].drop.${key}`); }); }
  });
  checkObject(value.tags || {}, ["sources", "drop", "shuffle", "exclude", "dedupe"], "tags");
  const tags = value.tags || {};
  if (tags.drop !== undefined) rate(tags.drop, "tags.drop");
  if (tags.shuffle !== undefined && typeof tags.shuffle !== "boolean") throw new GbcError("INVALID_REQUEST", "tags.shuffle");
  if (tags.sources !== undefined) {
    if (!Array.isArray(tags.sources) || !tags.sources.length) throw new GbcError("INVALID_REQUEST", "tags.sources");
    tags.sources.forEach((source) => { checkObject(source, ["from", "weight"], "tags.sources", ["from"]); if (typeof source.from !== "string" || !/^(graph:root|meta:[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)$/.test(source.from)) throw new GbcError("INVALID_REQUEST", "tags.sources.from"); if (source.weight !== undefined) range(source.weight, "tags.sources.weight", 1e-12, 1e12); });
  }
  for (const key of ["exclude", "dedupe"]) if (tags[key] !== undefined && (!Array.isArray(tags[key]) || tags[key].some((s) => typeof s !== "string" || !s))) throw new GbcError("INVALID_REQUEST", `tags.${key}`);
  checkObject(value.nl || {}, ["short"], "nl"); Object.entries(value.nl || {}).forEach(([field, short]) => range(short, `nl.${field}`));
  checkObject(value.graph || {}, ["keep", "tag_drop"], "graph"); Object.entries(value.graph || {}).forEach(([field, graphRate]) => rate(graphRate, `graph.${field}`));
  checkObject(value.fit || {}, ["tag_drop_step", "tag_drop_max", "keep_step", "keep_min", "margin", "fallback"], "fit");
  Object.entries(value.fit || {}).forEach(([key, number]) => { if (key === "fallback") { if (!Array.isArray(number) || number.some((s) => !sections.has(s))) throw new GbcError("INVALID_REQUEST", "fit.fallback"); } else range(number, `fit.${key}`, key.endsWith("step") || key === "margin" ? 0.001 : 0); });
  for (const key of ["prefix", "suffix", "list_sep", "section_sep", "text_sep"]) if (value[key] !== undefined && typeof value[key] !== "string") throw new GbcError("INVALID_REQUEST", key);
  if (value.underscores !== undefined && typeof value.underscores !== "boolean") throw new GbcError("INVALID_REQUEST", "underscores");
  // Metadata rules and Python-regex semantics are authoritatively checked by the service.
  return value;
}
export function draftUrl(value) { return `#/new?${new URLSearchParams(draftRef(value))}`; }
export function geometryPatch(patch, resolution) {
  if (!object(patch) || !["resize", "bucket", "square_crop"].includes(resolution)) throw new GbcError("INVALID_REQUEST", "knobPatch");
  return { ...patch, CROP: "center", FLIP: false, RESOLUTION_MODE: resolution };
}
const DEFAULT_CAPTION = { template: "gbc", formats: [{ weight: 1, sections: ["graph"] }] };

export function mountDraftEditor(host, ctx, { api, getSelection, onBusy = () => {} }) {
  const tr = (key, vars) => ctx.t(`gbc.${key}`, vars), label = (key) => esc(tr(key));
  const controller = new AbortController();
  let dead = false, busy = false, draft = null, dirty = false, caption = structuredClone(DEFAULT_CAPTION);
  const input = (name, key, value, min = 0, max = 1, step = "0.05") => `<label class="gbc-field">${label(key)}<input class="field" name="${name}" type="number" min="${min}" max="${max}" step="${step}" value="${value}"></label>`;
  host.innerHTML = `<h2>${label("draft.title")}</h2><p class="hint">${label("draft.note")}</p><p class="hint" data-draft-selection></p>
    <div class="gbc-form"><label class="gbc-field">${label("draft.base")}<select class="field" name="baseConfig"><option value="">${label("choose")}</option></select></label><button type="button" class="btn" data-draft-act="configs">${label("draft.loadConfigs")}</button><label class="gbc-field">${label("draft.template")}<select class="field" disabled><option>gbc</option></select></label></div>
    <h3>${label("draft.formats")}</h3><div data-formats></div><button class="btn" type="button" data-draft-act="format-add">${label("draft.addFormat")}</button>
    <div class="gbc-form">
      <label class="gbc-field">${label("draft.tagSource")}<input class="field" name="tagSource" value="graph:root"></label>${input("tagWeight", "draft.weight", 1, 0.000001, 1000000)}${input("tagDrop", "draft.tagDrop", 0)}<label class="gbc-check"><input type="checkbox" name="shuffle">${label("draft.shuffle")}</label>
      ${input("nlShort", "draft.nlShort", 0)}${input("graphKeep", "draft.graphKeep", 1)}${input("graphDrop", "draft.graphDrop", 0)}${input("fitMargin", "draft.fitMargin", 0.9, 0.001)}${input("fitDropStep", "draft.fitDropStep", 0.1, 0.001)}${input("fitDropMax", "draft.fitDropMax", 1)}${input("fitKeepStep", "draft.fitKeepStep", 0.1, 0.001)}${input("fitKeepMin", "draft.fitKeepMin", 0)}
      <label class="gbc-field">${label("draft.fallback")}<select class="field" name="fallback"><option value="nl">${label("draft.nl")}</option><option value="tags">${label("draft.tags")}</option><option value="graph">${label("draft.graph")}</option><option value="">${label("draft.noFallback")}</option></select></label>
    </div>
    <details><summary>${label("draft.advanced")}</summary><label class="gbc-check"><input name="advanced" type="checkbox">${label("draft.useJson")}</label><textarea class="field gbc-json" name="captionJson" rows="12">${esc(JSON.stringify(caption, null, 2))}</textarea><button type="button" class="btn" data-draft-act="json-from-controls">${label("draft.copyControls")}</button><p class="hint">${label("draft.jsonNote")}</p></details>
    <label class="gbc-field">${label("draft.knobPatch")}<textarea class="field gbc-json" name="knobPatch" rows="6">{}</textarea></label>
    <div class="gbc-form"><label class="gbc-field">${label("draft.resolution")}<select class="field" name="resolution">${["resize", "bucket", "square_crop"].map((v) => `<option value="${v}">${label(`option.${v}`)}</option>`).join("")}</select></label><button type="button" class="btn" data-draft-act="geometry">${label("draft.fixGeometry")}</button></div><p class="hint">${label("draft.geometryNote")}</p>
    <label class="gbc-check"><input type="checkbox" name="confirmConfig">${label("draft.confirmConfig")}</label><button class="btn pri" type="button" data-draft-act="create">${label("draft.create")}</button>
    <div data-draft-status role="status" class="okline" tabindex="-1">${label("draft.empty")}</div><div data-draft-result></div>
    <h3>${label("draft.preview")}</h3><div class="gbc-form">${input("seed", "draft.seed", 0, 0, 9007199254740991, "1")}${input("variant", "draft.variant", 0, 0, 9007199254740991, "1")}${input("targetWidth", "draft.targetWidth", "", 1, 65536, "1")}${input("targetHeight", "draft.targetHeight", "", 1, 65536, "1")}<button class="btn" type="button" data-draft-act="preview">${label("draft.preview")}</button></div><p class="hint">${label("draft.budgetNote")}</p><pre class="code gbc-json" data-caption-text></pre><details><summary>${label("draft.geometryBudget")}</summary><pre class="code gbc-json" data-caption-detail></pre></details>`;
  const q = (selector) => host.querySelector(selector), val = (name) => q(`[name="${name}"]`).value;
  function formats() {
    q("[data-formats]").innerHTML = caption.formats.map((fmt, i) => `<div class="gbc-form" data-format="${i}"><label class="gbc-field">${label("draft.weight")}<input class="field" data-weight type="number" min="0.000001" step="0.1" value="${fmt.weight}"></label>${["tags", "nl", "graph"].map((key) => `<label class="gbc-check"><input type="checkbox" data-section="${key}" ${fmt.sections.includes(key) ? "checked" : ""}>${label(`draft.${key}`)}</label>`).join("")}<button type="button" class="btn" data-draft-act="format-remove" data-index="${i}">${label("draft.removeFormat")}</button></div>`).join("");
  }
  function readControls() {
    return validateCaption({ template: "gbc", formats: [...host.querySelectorAll("[data-format]")].map((el) => ({ weight: Number(el.querySelector("[data-weight]").value), sections: [...el.querySelectorAll("[data-section]:checked")].map((el) => el.dataset.section) })),
      tags: { sources: [{ from: val("tagSource"), weight: Number(val("tagWeight")) }], drop: Number(val("tagDrop")), shuffle: q('[name="shuffle"]').checked }, nl: { short: Number(val("nlShort")) }, graph: { keep: Number(val("graphKeep")), tag_drop: Number(val("graphDrop")) },
      fit: { margin: Number(val("fitMargin")), tag_drop_step: Number(val("fitDropStep")), tag_drop_max: Number(val("fitDropMax")), keep_step: Number(val("fitKeepStep")), keep_min: Number(val("fitKeepMin")), fallback: val("fallback") ? [val("fallback")] : [] } });
  }
  function json(name) { const field = q(`[name="${name}"]`); try { const result = JSON.parse(field.value); if (!object(result)) throw new Error(); field.removeAttribute("aria-invalid"); return result; } catch { field.setAttribute("aria-invalid", "true"); field.focus(); throw new GbcError("INVALID_REQUEST", name); } }
  function refreshControls() {
    if (dead) return;
    q('[data-draft-act="preview"]').disabled = busy || !draft || dirty || !getSelection().sampleId;
  }
  function selectionChanged() {
    const selection = getSelection();
    q("[data-draft-selection]").textContent = `${tr("draft.selection")}: ${selection.dataset?.datasetId || "—"} · ${selection.shards.length} · ${selection.sampleId || "—"}`;
    if (draft && (selection.dataset?.datasetId !== draft.datasetRef.datasetId || selection.dataset?.revision !== draft.datasetRef.revision || selection.shards.length !== draft.shards.length || selection.shards.some((s) => !draft.shards.includes(s)))) dirty = true;
    refreshControls();
  }
  async function run(fn) {
    if (busy || dead) return; busy = true; onBusy(true); refreshControls(); q("[data-draft-status]").textContent = tr("working");
    try { await fn(); }
    catch (error) { if (!dead && error.name !== "AbortError") { q("[data-draft-status]").textContent = errorText(error, ctx.t); q("[data-draft-status]").focus(); } }
    finally { busy = false; onBusy(false); refreshControls(); }
  }
  async function configs() {
    const result = await fetch("/api/configs", { signal: controller.signal, cache: "no-store", credentials: "omit", headers: { "X-UI-Token": TOKEN } });
    if (!result.ok) throw new GbcError(result.status === 403 ? "AUTH_REQUIRED" : "NETWORK_ERROR");
    const data = await result.json(); if (dead) return;
    const choices = data.configs.filter((item) => /^configs\/(moss|user)\/.+\.py$/.test(item.path));
    q('[name="baseConfig"]').innerHTML = `<option value="">${label("choose")}</option>` + choices.map((item) => `<option value="${esc(item.path)}">${esc(item.path)}</option>`).join("");
    q("[data-draft-status]").textContent = tr("draft.chooseBase");
  }
  async function create() {
    const selection = getSelection();
    if (!selection.dataset || !selection.shards.length || !val("baseConfig") || !q('[name="confirmConfig"]').checked) throw new GbcError("INVALID_REQUEST", tr("draft.required"));
    const captionOptions = q('[name="advanced"]').checked ? validateCaption(json("captionJson")) : readControls();
    const knobPatch = json("knobPatch");
    if (("FLIP" in knobPatch && knobPatch.FLIP !== false) || ("CROP" in knobPatch && knobPatch.CROP !== "center") || knobPatch.RESOLUTION_MODE === "shifted_square") throw new GbcError("VALIDATION_FAILED", tr("draft.geometryNote"));
    const result = await api.post("training-drafts", { datasetId: selection.dataset.datasetId, datasetRevision: selection.dataset.revision, shards: selection.shards, baseConfigId: val("baseConfig"), captionOptions, knobPatch, confirmExecution: true });
    if (dead) return; draftRef(result.draft); draft = result.draft; dirty = false;
    q("[data-draft-result]").innerHTML = `<p><a class="btn" href="${esc(draftUrl(draft))}">${label("draft.open")}</a></p><details><summary>${label("draft.typed")}</summary>${renderTypedDraft(draft.values)}</details>`;
    q("[data-draft-status]").textContent = tr("draft.created", { id: draft.draftRef.draftId }); q('[name="confirmConfig"]').checked = false;
    q("[data-caption-text]").textContent = ""; q("[data-caption-detail]").textContent = "";
  }
  async function preview() {
    if (!draft || dirty) throw new GbcError("VERSION_CONFLICT");
    const sampleId = getSelection().sampleId; if (!sampleId || !draft.shards.includes(sampleId.split("/")[0])) throw new GbcError("INVALID_REQUEST", tr("draft.sampleRequired"));
    const seed = Number(val("seed")), variant = Number(val("variant"));
    if (![seed, variant].every((v) => Number.isSafeInteger(v) && v >= 0)) throw new GbcError("INVALID_REQUEST", "seed / variant");
    const payload = { draftRef: draftRef(draft), sampleId, seed, variant };
    if (val("targetWidth") || val("targetHeight")) { const width = Number(val("targetWidth")), height = Number(val("targetHeight")); if (![width, height].every((v) => Number.isSafeInteger(v) && v > 0)) throw new GbcError("INVALID_REQUEST", "target"); payload.target = { width, height }; }
    const result = await api.post("captions/preview", payload); if (dead) return;
    assertDraft(result.preview.draftRef, draft); q("[data-caption-text]").textContent = result.preview.text;
    const { text, ...detail } = result.preview; q("[data-caption-detail]").textContent = JSON.stringify(detail, null, 2); q("[data-draft-status]").textContent = tr("draft.previewReady");
  }
  function click(event) {
    const target = event.target.closest("[data-draft-act]"); if (!target || busy) return;
    const act = target.dataset.draftAct;
    run(async () => {
      if (act === "configs") await configs();
      else if (act === "create") await create();
      else if (act === "preview") await preview();
      else if (act === "geometry") { q('[name="knobPatch"]').value = JSON.stringify(geometryPatch(json("knobPatch"), val("resolution")), null, 2); dirty = true; q("[data-draft-status]").textContent = tr("draft.geometryFixed"); }
      else if (act === "json-from-controls") { q('[name="captionJson"]').value = JSON.stringify(readControls(), null, 2); q("[data-draft-status]").textContent = tr("ready"); }
      else if (act === "format-add" || act === "format-remove") { caption = readControls(); if (act === "format-add") caption.formats.push({ weight: 1, sections: ["graph"] }); else if (caption.formats.length > 1) caption.formats.splice(Number(target.dataset.index), 1); formats(); dirty = true; q("[data-draft-status]").textContent = tr("draft.edited"); }
    });
  }
  function onInput(event) { if (!["seed", "variant", "targetWidth", "targetHeight", "confirmConfig"].includes(event.target.name)) { dirty = true; if (draft) q("[data-draft-status]").textContent = tr("draft.edited"); } refreshControls(); }
  host.addEventListener("click", click); host.addEventListener("input", onInput); formats(); selectionChanged();
  return { selectionChanged, cleanup() { dead = true; controller.abort(); host.removeEventListener("click", click); host.removeEventListener("input", onInput); } };
}

export async function gbcDraftView(main, ctx) {
  main.__studioCleanup?.();
  const controller = new AbortController(), api = createGbcApi({ signal: controller.signal });
  let dead = false, busy = false, ref = null;
  const tr = (key) => ctx.t(`gbc.${key}`);
  main.innerHTML = `<section class="gbc-workspace"><div class="vhead surface"><h1>${esc(tr("draft.review"))}</h1><a class="btn" href="#/datasets/gbc">${esc(tr("back"))}</a></div><p class="hint">${esc(tr("draft.readonly"))}</p><button class="btn" data-draft-check disabled>${esc(tr("draft.check"))}</button><div data-draft-review-status class="okline surface" role="status" tabindex="-1">${esc(tr("loading"))}</div><div data-typed></div><pre class="code gbc-json" data-check-output></pre></section>`;
  const root = main.querySelector(".gbc-workspace"), button = root.querySelector("[data-draft-check]"), status = root.querySelector("[data-draft-review-status]");
  async function check() {
    if (busy || !ref || dead || !window.confirm(tr("draft.confirmCheck"))) return;
    busy = true; button.disabled = true; status.textContent = tr("working");
    try { const result = await api.post("training-drafts/check", { draftRef: ref, confirmExecution: true }); if (dead) return; assertDraft(result.check.draftRef, ref); if (result.check.effectiveHash !== ref.effectiveHash) throw new GbcError("VERSION_CONFLICT"); status.textContent = tr(result.check.ok === true && result.check.validation?.status === "valid" ? "draft.checkPassed" : "draft.checkFailed"); root.querySelector("[data-check-output]").textContent = JSON.stringify(result.check, null, 2); }
    catch (error) { if (!dead && error.name !== "AbortError") status.textContent = errorText(error, ctx.t); }
    finally { busy = false; if (!dead) button.disabled = false; }
  }
  button.addEventListener("click", check);
  function cleanup() { dead = true; controller.abort(); button.removeEventListener("click", check); if (main.__studioCleanup === cleanup) main.__studioCleanup = null; }
  main.__studioCleanup = cleanup;
  try {
    const query = ctx.query(); ref = draftRef({ draftId: query.get("draftId"), revision: Number(query.get("revision")), effectiveHash: query.get("effectiveHash") });
    const result = await api.get("training-drafts", { draftId: ref.draftId, revision: ref.revision }); if (dead) return; assertDraft(result.draft, ref);
    root.querySelector("[data-typed]").innerHTML = `<pre class="code gbc-json">${esc(JSON.stringify(ref, null, 2))}</pre>${renderTypedDraft(result.draft.values)}`;
    status.textContent = tr("draft.readyCheck"); button.disabled = false;
  } catch (error) { if (!dead && error.name !== "AbortError") status.textContent = errorText(error, ctx.t); }
}
