/* ASG workspace. No credentials or task history in browser storage. */
import * as api from "./api.js";
import { esc } from "./format.js";
import { createLibrary } from "./grabber-library.js";
import { createGrabberMedia } from "./grabber-media.js";
import { createAnnotations } from "./grabber-annotations.js";
import { createAnnotationClient } from "./annotation-api.js";

const ACTIVE = new Set(["preparing", "running"]);
const TERMINAL = new Set(["done", "stopped", "error"]);
const SECRET = /api.?key|token|password|phpsessid|cookie|^ct0$|authorization/i;
const TAG_FIELDS = {
  openai_compatible: [
    ["endpoint", "text", ""], ["model", "text", ""], ["api_key", "password", ""],
    ["prompt", "textarea", ""], ["timeout", "number", 120, 1],
    ["max_tokens", "number", 512, 1], ["temperature", "number", 0.1],
  ],
  local_onnx: [
    ["model_path", "text", ""], ["tags_path", "text", ""],
    ["general_threshold", "number", 0.35, 0, 1], ["character_threshold", "number", 0.85, 0, 1],
    ["input_size", "number", 448, 1], ["providers", "text", "CUDAExecutionProvider,CPUExecutionProvider"],
    ["categories", "text", "0,4"],
  ],
};

export function safeLink(value) {
  try {
    const url = new URL(String(value));
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return "";
    // Never repeat query credentials in external links.
    for (const key of [...url.searchParams.keys()]) if (SECRET.test(key)) url.searchParams.delete(key);
    return url.href;
  } catch { return ""; }
}

function mediaUrl(value, size) {
  if (typeof value !== "string" || !value.startsWith("/files/") || /[?#\\\u0000-\u001f]/.test(value)) return "";
  const query = new URLSearchParams({ path: value });
  if (size) query.set("size", size);
  if (api.TOKEN) query.set("token", api.TOKEN);
  return `/api/grabber/${size ? "preview" : "file"}?${query}`;
}
export function previewUrl(value, size = "thumb") {
  return ["thumb", "detail"].includes(size) ? mediaUrl(value, size) : "";
}
export function originalUrl(value) { return mediaUrl(value); }

export function redact(value, secrets = []) {
  if (Array.isArray(value)) return value.map((item) => redact(item, secrets));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, SECRET.test(key) && key !== "max_tokens" ? "[redacted]" : redact(item, secrets)]));
  if (typeof value !== "string") return value;
  let text = value;
  for (const secret of secrets) if (secret) text = text.split(String(secret)).join("[redacted]");
  return text.replace(/(Bearer\s+)\S+/gi, "$1[redacted]")
    .replace(/((?:api[_-]?key|access_token|auth_token|phpsessid|ct0|password|authorization)\s*[=:]\s*)[^\s,;&]+/gi, "$1[redacted]")
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, "$1[redacted]@");
}

export async function grabberView(main, ctx) {
  main.__studioCleanup?.();
  const root = document.createElement("section");
  root.className = "grabber";
  main.replaceChildren(root);
  const gt = (key, vars) => ctx.t(`grabber.${key}`, vars);
  const label = (key) => esc(gt(key));
  let disposed = false, timer = null, polling = false, busy = false;
  let sourceEpoch = 0, tagEpoch = 0;
  let service = null, sources = [], sourceValues = {}, tagValues = {}, configured = [], tagConfigured = [];
  let candidates = [], message = "", messageError = false, messageOrigin = "", checked = false;
  let tab = "range", selectedItem = null, filter = "all", resultPage = 0;
  const resultLimit = 60;
  const media = createGrabberMedia({ root, esc, gt, previewUrl, originalUrl });
  // Local input updates intentionally skip a full render. Drop the cached pane
  // HTML so the next explicit draw cannot mistake stale markup for current state.
  const parts = new Map();
  const invalidatePane = () => parts.delete("pane");
  function invalidateMedia() { media.clear(); parts.delete("pane"); }
  let capabilities = {}, sourceCheckMessage = "";
  const pendingClears = new Set();
  let snapshot = { status: "idle" }, items = [], logs = [], logOffset = 0, itemOffset = 0, stopRequested = false;
  let taskConfig = null, finalSynced = false, progressKnown = false;
  const secrets = new Set();
  const cfg = {
    source: "", artist: "", canonical_artist: "", query_type: "artist", count: 200,
    start_page: 1, end_page: 0, rating: "", skip_video: true, include_artist: false,
    include_meta: false, images_only: false, background_enabled: false, background_color: "#ffffff",
    tagger_id: "none", tag_merge_mode: "native_only", tag_format: "comma",
  };
  let unlimited = false, remoteConsent = false;
  const selectedSources = new Set(), sourceDrafts = new Map(), tagDrafts = new Map();
  let modelDirectory = "", models = [], modelSelected = "", modelHint = "";
  const alive = () => !disposed && root.isConnected;
  const meta = () => sources.find((source) => source.id === cfg.source) || {};
  const clean = (value) => redact(value, [...secrets]);
  const running = () => ACTIVE.has(snapshot.status);
  const retagUnknown = () => capabilities.retag === true && !library.state.known;
  const annotationClient = createAnnotationClient();
  const annotations = createAnnotations({ root, main, esc, gt, media, alive, render, invalidate: invalidatePane,
    api: { ...annotationClient, get: api.get, onGroup: group => { library.state.group = group; } } });
  const library = createLibrary({ api, post, esc, gt, media, invalidateMedia, alive, render, notice, clean, annotations,
    ready: () => Boolean(service?.running), capabilities: () => capabilities, collecting: () => running() || busy === "start" || !progressKnown || connectionLost,
    tagger: () => ({ id: cfg.tagger_id, config: taggerConfig(), format: cfg.tag_format }), tagPane });
  function draft(id) {
    if (!sourceDrafts.has(id)) sourceDrafts.set(id, { artist: "", query_type: "artist", rating: "", values: {}, configured: [], clears: [], loaded: false });
    return sourceDrafts.get(id);
  }
  function stashSource() {
    if (!cfg.source) return;
    Object.assign(draft(cfg.source), { artist: cfg.artist, query_type: cfg.query_type, rating: cfg.rating, values: sourceValues, configured,
      clears: [...pendingClears].filter(key => key.startsWith("source:")) });
  }
  function sourceConfig(id) {
    stashSource();
    const d = draft(id), info = sources.find(s => s.id === id) || {};
    const keys = new Set(["proxy", "x_cookie_mode", "pixiv_cookie_mode", ...(info.auth_fields || []).map(f => f.id), ...(info.extra_fields || []).map(f => f.id)]);
    return { ...Object.fromEntries(Object.entries(d.values).filter(([key, value]) => keys.has(key) && value !== "")), artist: d.artist, query_type: d.query_type, rating: d.rating };
  }
  function taggerConfig() {
    rememberSecrets();
    return Object.fromEntries(Object.entries(tagValues).filter(([key, value]) => key !== "api_key" || value !== ""));
  }
  function currentBody() { return { source: cfg.source, ...sourceConfig(cfg.source) }; }
  const btn = (act, key, disabled = false, extra = "") => `<button type="button" class="btn ${extra}" data-act="${act}" ${disabled ? "disabled" : ""}>${label(key)}</button>`;
  const row = (name, control, hint = "") => `<div class="lab">${name}</div><div class="val">${control}${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
  const input = (name, value, type = "text", attrs = "", scope = "cfg") => `<input class="field" aria-label="${label(`field.${name}`)}" data-scope="${scope}" data-field="${esc(name)}" type="${type}" value="${esc(value ?? "")}" ${attrs}>`;
  const field = (name, type = "text", attrs = "") => row(label(`field.${name}`), input(name, cfg[name], type, attrs));
  const select = (name, options, value, scope = "cfg") => `<select class="field" aria-label="${label(`field.${name}`)}" data-scope="${scope}" data-field="${esc(name)}">${options.map(([v, text]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(text)}</option>`).join("")}</select>`;
  const choice = (name, options) => row(label(`field.${name}`), select(name, options.map((value) => [value, gt(`option.${value}`)]), cfg[name]));
  const check = (name, value, scope = "cfg") => `<input type="checkbox" data-scope="${scope}" data-field="${name}" aria-label="${label(`field.${name}`)}" ${value ? "checked" : ""}>`;
  const toggle = (name) => row(label(`field.${name}`), check(name, cfg[name]));
  function tabs(names, current) {
    return `<div class="tabs surface" role="tablist">${names.map((name) => `<button type="button" role="tab" aria-selected="${current === name}" data-act="tab" data-tab="${name}" class="${current === name ? "on" : ""}">${label(`tab.${name}`)}</button>`).join("")}</div>`;
  }
  function dynamicField(field, scope, values, saved) {
    const name = field.id;
    const title = esc(field.label || name);
    let control;
    if (field.type === "select") control = select(name, field.options || [], values[name] ?? field.options?.[0]?.[0] ?? "", scope);
    else control = `<input class="field" aria-label="${title}" data-scope="${scope}" data-field="${esc(name)}" type="${field.secret ? "password" : "text"}" value="${esc(values[name] ?? "")}" autocomplete="off" spellcheck="false" placeholder="${esc(field.secret && saved.includes(name) ? gt("secret.saved") : field.placeholder || "")}">`;
    if (field.secret) control += ` <label class="hint"><input type="checkbox" data-clear="${esc(name)}" data-scope="${scope}"> ${label("secret.clear")}</label>`;
    return row(title + (field.required ? " *" : ""), control);
  }
  function authPane() {
    const source = meta();
    const session = ["twitter", "pixiv"].includes(source.id);
    const mode = source.id === "twitter" ? "x_cookie_mode" : "pixiv_cookie_mode";
    return `<p class="hint">${label("secret.hint")}</p><div class="form surface">
      ${(source.auth_fields || []).map((f) => dynamicField(f, "source", sourceValues, configured)).join("")}
      ${session ? row(label("session.mode"), select(mode, [["", gt("session.manual")], ["managed", gt("session.managed")]], sourceValues[mode] || "", "source")) : ""}
      ${session ? row(label("session.title"), btn("session-open", "session.open", busy) + btn("session-check", "session.check", busy)) : ""}
      ${row(label("field.proxy"), input("proxy", sourceValues.proxy || "", "text", "autocomplete=off", "source"))}
      ${row(label("credentials"), btn("save-source", "save", busy) + btn("test-source", "check", busy || !service?.running))}
      </div>`;
  }
  function tagPane(forLibrary = false) {
    const fields = TAG_FIELDS[cfg.tagger_id] || [];
    const renderFields = list => list.map(([name, type, fallback, min, max]) => {
      const attrs = `${type === "number" ? 'step="any"' : ""} ${min != null ? `min="${min}"` : ""} ${max != null ? `max="${max}"` : ""} autocomplete="off"`;
      let control = type === "textarea" ? `<textarea class="field" aria-label="${label(`field.${name}`)}" data-scope="tag" data-field="${name}">${esc(tagValues[name] ?? fallback)}</textarea>` : input(name, tagValues[name] ?? fallback, type, attrs, "tag");
      if (type === "password") control = `<input class="field" type="password" aria-label="${label(`field.${name}`)}" data-scope="tag" data-field="${name}" value="${esc(tagValues[name] || "")}" autocomplete="off" placeholder="${tagConfigured.includes(name) ? label("secret.saved") : ""}"><label class="hint"><input type="checkbox" data-clear="${name}" data-scope="tag"> ${label("secret.clear")}</label>`;
      return row(label(`field.${name}`), control);
    }).join("");
    const local = cfg.tagger_id === "local_onnx";
    return `<div class="form surface">${choice("tagger_id", ["none", "openai_compatible", "local_onnx"])}
      ${cfg.tagger_id === "openai_compatible" && !forLibrary ? row(label("remote.title"), `<label>${check("remoteConsent", remoteConsent, "ui")} ${label("remote.hint")}</label>`) : ""}
      ${local ? row(label("models.directory"), input("modelDirectory", modelDirectory, "text", "", "model") + btn("models-scan", "models.scan"), label("models.hint")) + row(label("models.choose"), select("modelSelected", [["", gt("models.choose")], ...models.map((m, i) => [String(i), m.name])], modelSelected, "model"), esc(modelHint)) : ""}
      ${renderFields(local ? fields.slice(0, 4) : fields)}
      ${local ? row(label("models.advanced"), `<details data-details="onnx"><summary>${label("models.advanced")}</summary><div class="form">${renderFields(fields.slice(4))}</div></details>`, label("models.gpu")) : ""}
      ${forLibrary ? "" : choice("tag_merge_mode", ["native_only", "tagger_only", "native_plus_tagger"])}${choice("tag_format", ["comma", "space"])}
      ${cfg.tagger_id !== "none" ? row(label("credentials"), btn("save-tag", "save", busy) + btn("test-tag", "tag.test", busy || !service?.running)) : ""}</div>
      ${local ? `<div class="hint" data-onnx-compatibility><p>${label("models.compatibility")}</p><p>${label("models.inputOutput")}</p><p>${label("models.csvCategories")}</p><p>${label("models.validationScope")}</p></div>` : ""}`;
  }
  function configPane() {
    if (tab === "auth") return authPane();
    if (tab === "tagging") return (cfg.images_only ? `<p class="hint">${label("imagesOnly.hint")}</p>` : "") + tagPane();
    if (tab === "images") return `<div class="form surface">${row(label("field.images_only"), check("images_only", cfg.images_only), label(capabilities.images_only === true ? "imagesOnly.hint" : "imagesOnly.unsupported"))}${toggle("skip_video")}${toggle("include_artist")}${toggle("include_meta")}${toggle("background_enabled")}${field("background_color", "text", 'pattern="#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?"')}</div>`;
    return `<div class="form surface">
      ${row(label("field.count"), input("count", cfg.count, "number", `min="1" step="1" ${unlimited ? "disabled" : ""}`) + `<label>${check("unlimited", unlimited, "ui")} ${label("unlimited")}</label>`, label("count.hint"))}
      ${field("start_page", "number", 'min="1" max="1000" step="1"')}${field("end_page", "number", 'min="0" max="1000" step="1"')}
      ${row(label("field.rating"), select("rating", meta().ratings || [["", gt("all")]], cfg.rating))}
      ${(meta().extra_fields || []).map((f) => dynamicField(f, "source", sourceValues, configured)).join("")}</div>`;
  }
  function queryTypes(id = cfg.source) {
    const fallback = {
      danbooru: ["artist", "character", "tag"], gelbooru: ["artist", "character", "tag"],
      safebooru: ["artist", "character", "tag"], konachan: ["artist", "character", "tag"],
      yandere: ["artist", "character", "tag"], twitter: ["artist"], pixiv: ["artist"], openverse: ["artist", "tag"],
    };
    const types = sources.find(source => source.id === id)?.query_types;
    return Array.isArray(types) && types.length ? types : fallback[id] || ["artist"];
  }
  function canSearch() {
    const types = meta().search_query_types;
    if (Array.isArray(types)) return types.includes(cfg.query_type);
    return cfg.source === "danbooru" || (cfg.query_type === "artist" && Boolean(meta().supports_artist_search));
  }
  const unknownTask = () => running() && !taskConfig;
  const queryKind = () => cfg.source === "openverse" && cfg.query_type === "tag" ? "keyword" : cfg.query_type;
  function taskSummary() {
    return { source: snapshot.source, artist: snapshot.artist, canonical_artist: snapshot.canonical_artist, folder: snapshot.folder };
  }
  function unknownPane() {
    return `<div class="box"><p class="hint">${label("config.unknown")}</p><pre class="code">${esc(JSON.stringify(clean(taskSummary()), null, 2))}</pre></div>`;
  }
  function basicPane() {
    if (unknownTask()) return `<p class="hint">${label("config.unknown")}</p><div class="form newrun-head surface">
      ${row(label("source"), `<span>${esc(snapshot.source_label || snapshot.source || "—")}</span>`)}
      ${row(label("field.query_type"), `<span>—</span>`)}
      ${row(label("field.artist"), `<span>${esc(clean(snapshot.artist || "—"))}</span>`)}
      ${row(label("field.canonical_artist"), `<span>${esc(clean(snapshot.canonical_artist || "—"))}</span>`)}
      ${row(label("download"), `<span>${esc(service?.download_dir || "—")}</span>`)}
    </div>`;
    const kind = queryKind();
    return `<div class="form newrun-head surface">
      ${row(label("multi.sources"), `<div class="grabber-sources">${sources.map(s => `<label><input type="checkbox" data-scope="selection" data-field="${esc(s.id)}" ${selectedSources.has(s.id) ? "checked" : ""}> ${esc(s.label)}</label>`).join("")}</div>`, label(capabilities.multi_source === true ? "multi.hint" : "multi.unsupported"))}
      ${sources.filter(s => selectedSources.has(s.id) && s.id !== cfg.source).map(s => row(esc(s.label), `<input class="field" data-scope="mapping" data-field="${esc(s.id)}" aria-label="${esc(s.label)}" value="${esc(draft(s.id).artist)}" placeholder="${label("multi.mapping")}">`, esc(gt(`option.${draft(s.id).query_type}`)))).join("")}
      ${row(label("multi.editing"), select("source", sources.map((s) => [s.id, s.label]), cfg.source), label("multi.editHint") + " " + esc(meta().description || ""))}
      ${row(label("field.query_type"), select("query_type", queryTypes().map((v) => [v, gt(`option.${cfg.source === "openverse" && v === "tag" ? "keyword" : v}`)]), cfg.query_type))}
      ${row(label(`query.${kind}.label`), `<input class="field" aria-label="${label(`query.${kind}.label`)}" data-scope="cfg" data-field="artist" value="${esc(cfg.artist)}" placeholder="${label(`query.${kind}.placeholder`)}">` + btn("search", "search"), label(`query.${kind}.hint`) + (!canSearch() ? ` ${label("search.unsupported")}` : cfg.query_type === "character" ? ` ${label("character.hint")}` : ""))}
      ${row(label("field.canonical_artist"), input("canonical_artist", cfg.canonical_artist), label("folder.hint"))}
      ${row(label("download"), `<span class="field">${esc(service?.download_dir || "—")}</span>`, label("download.hint"))}
    </div>`;
  }
  function statusText() {
    if (snapshot.status === "done" && (Number(snapshot.failed) > 0 || items.some((item) => item.tag_status === "failed") || snapshot.sources?.some((s) => s.status === "error"))) return gt("status.warning");
    return gt(`status.${snapshot.status || "idle"}`);
  }
  function resultPane() {
    if (tab === "logs") return `<div class="box"><div class="logbox flat">${logs.length ? logs.map((line) => `<div class="ln">${esc(clean(line))}</div>`).join("") : label("empty.logs")}</div></div>`;
    if (tab === "config") return snapshot.status === "idle" ? `<div class="box"><p class="hint">${label("config.empty")}</p></div>` : taskConfig ? `<div class="box"><p class="hint">${label("config.hint")}</p><pre class="code">${esc(JSON.stringify(clean(taskConfig), null, 2))}</pre></div>` : unknownPane();
    if (selectedItem != null && items[selectedItem]) {
      const item = items[selectedItem];
      const path = item.image_url || item.preview_url;
      const link = safeLink(item.url);
      return `<div class="box grabber-detail">${btn("detail-close", "back")}<h3>${esc(item.filename || item.id)}</h3>${media.markup(path, "detail", item.filename || item.id)}
        ${media.original(path)}<div class="form">${["status", "tag_status", "native_tags", "generated_tags", "final_tags", "tag_error"].map((key) => row(label(`item.${key}`), `<span class="grabber-wrap">${esc(clean(Array.isArray(item[key]) ? item[key].join(", ") : item[key] || "—"))}</span>`)).join("")}</div>
        ${link ? `<a class="btn" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${label("original")}</a>` : ""}</div>`;
    }
    const shown = [];
    for (let index = 0; index < items.length; index++) {
      const item = items[index];
      if (filter === "all" || (filter === "tag_failed" ? item.tag_status === "failed" : item.status === filter)) shown.push(index);
    }
    resultPage = Math.min(resultPage, Math.max(0, Math.ceil(shown.length / resultLimit) - 1));
    const offset = resultPage * resultLimit;
    const page = shown.slice(offset, offset + resultLimit);
    return `<p class="hint">${label("results.live")}</p><div class="form surface">${row(label("filter"), select("filter", ["all", "ok", "exists", "skipped", "duplicate", "failed", "tag_failed"].map((v) => [v, gt(`filter.${v}`)]), filter, "ui"))}</div>
      <div class="box"><div class="gallery">${page.map(index => {
        const item = items[index];
        return `<button type="button" class="shot" data-act="detail" data-index="${index}">${media.markup(item.preview_url || item.image_url, "thumb", item.filename || item.id)}<span class="cap"><span>${esc(item.filename || item.id)}</span><span>${esc(item.status)}</span></span></button>`;
      }).join("")}</div><div class="acts">${btn("results-prev", "library.prev", resultPage === 0)}<span>${shown.length ? offset + 1 : 0}–${offset + page.length} / ${shown.length}</span>${btn("results-next", "library.next", offset + resultLimit >= shown.length)}</div>${shown.length ? "" : `<p class="dim">${label("empty.results")} ${btn("open-library", "tab.library")}</p>`}</div>`;
  }
  const TAG_OPTIONS = new Set(["tagger_id", "tag_merge_mode", "tag_format", "include_artist", "include_meta", "remoteConsent"]);
  const CONFIG_ACTIONS = new Set(["candidate", "search", "test-source", "save-source", "save-tag", "test-tag", "session-open", "session-check", "models-scan", "start"]);
  let renderedUnknown = false;
  function patch(id, html) {
    if (parts.get(id) === html) return;
    const host = root.querySelector(`#grabber-${id}`);
    const active = document.activeElement;
    const focused = host.contains(active) ? { field: active.dataset.field, scope: active.dataset.scope, act: active.dataset.act, start: active.selectionStart, end: active.selectionEnd, direction: active.selectionDirection, top: active.scrollTop, left: active.scrollLeft } : null;
    const scroll = host.querySelector(".logbox")?.scrollTop;
    const detailScroll = id === "pane" && host.querySelector(".grabber-media-detail") ? main.scrollTop : null;
    const opened = [...host.querySelectorAll("details")].filter(node => node.open).map(node => node.dataset.details);
    if (id === "pane") media.patch(host, html);
    else host.innerHTML = html;
    parts.set(id, html);
    for (const node of host.querySelectorAll("details")) if (opened.includes(node.dataset.details)) node.open = true;
    if (scroll != null && host.querySelector(".logbox")) host.querySelector(".logbox").scrollTop = scroll;
    if (focused) {
      const node = [...host.querySelectorAll("[data-field],[data-act]")].find((el) => focused.field ? el.dataset.field === focused.field && el.dataset.scope === focused.scope : el.dataset.act === focused.act);
      if (node) {
        node.focus({ preventScroll: true });
        if (focused.start != null && node.setSelectionRange) node.setSelectionRange(focused.start, focused.end, focused.direction);
        if (focused.top != null) node.scrollTop = focused.top;
        if (focused.left != null) node.scrollLeft = focused.left;
      }
    }
    // Parent replacement can still trigger native scroll anchoring. Restore only
    // detail-to-detail commits, after open details/focus; never retain an async
    // scroll target that could override a user's later scroll, tab or route change.
    if (detailScroll != null && host.querySelector(".grabber-media-detail")) main.scrollTop = detailScroll;
  }
  function applyLocks() {
    root.querySelectorAll("input,select,textarea").forEach((node) => {
      const name = node.dataset.field, scope = node.dataset.scope;
      if (scope === "annotation" || scope === "translation") {
        if (annotations.active() || (scope === "translation" && annotations.state.translation.busy)) node.disabled = true;
        return;
      }
      if (name === "filter") { node.disabled = false; return; }
      if (scope === "library") { node.disabled = name !== "group" && library.active(); return; }
      const tagging = scope === "tag" || scope === "model" || TAG_OPTIONS.has(name);
      const requestLock = busy === "start" || (busy && !["stop", "refresh", "service-start"].includes(busy) && (busy.endsWith("-tag") ? tagging : !tagging));
      node.disabled = Boolean(running() || library.active() || requestLock || (cfg.images_only && tab !== "library" && tagging) || (name === "count" && unlimited) || (name === "images_only" && capabilities.images_only !== true));
      if (node.dataset.clear) node.checked = pendingClears.has(`${scope}:${node.dataset.clear}`);
    });
    root.querySelectorAll("[data-act]").forEach((node) => {
      const act = node.dataset.act;
      if (act.startsWith("annotation-") || act.startsWith("translation-")) return;
      if (act.startsWith("library-")) {
        if (act === "library-start") node.disabled = Boolean(busy || running() || library.active() || annotations.retagProtected() || annotations.active() || !library.state.known || library.state.loading || !progressKnown || connectionLost);
        if (act === "library-stop") node.disabled = !library.active() || library.state.stopping;
        return;
      }
      if (act === "results-prev" || act === "results-next") return;
      if (["tab", "detail", "detail-close", "open-library"].includes(act)) { node.disabled = false; return; }
      node.disabled = Boolean(busy || ((running() || library.active()) && CONFIG_ACTIONS.has(act)) || (cfg.images_only && tab !== "library" && ["save-tag", "test-tag", "models-scan"].includes(act)) || (act === "search" && !canSearch()) || (["start", "search", "test-source", "test-tag", "stop"].includes(act) && !service?.running) || (act === "start" && (!progressKnown || connectionLost || retagUnknown())) || (act === "stop" && stopRequested) || (act === "service-start" && !service?.available));
    });
  }
  function render(edit = true) {
    if (!alive()) return;
    if (!root.querySelector("#grabber-head")) {
      root.innerHTML = `<div class="vhead vhead-sticky"><h2>${label("title")}</h2><div class="meta">${label("single")}</div><div class="acts" id="grabber-head"></div></div>
        <div class="readystrip surface" id="grabber-ready"></div><p class="hint">${label("service.lifecycle")}</p>
        <div id="grabber-warnings"></div><div id="grabber-progress"></div><div id="grabber-basic"></div><div id="grabber-candidates"></div>
        <div id="grabber-tabs"></div><div id="grabber-pane"></div><div id="grabber-library-progress"></div><div id="grabber-message"></div>`;
    }
    patch("head", btn("test-source", "check") + (running() ? btn("stop", stopRequested ? "stopping" : "stop", false, "dngr") : btn("start", "start", false, "pri")) + btn("refresh", "refresh"));
    patch("ready", `<span class="readyitem">${label("service")} <b>${label(service?.running ? "ready" : service?.available ? "notStarted" : "notInstalled")}</b></span><span class="readyitem">${label("source")} <b>${esc(unknownTask() ? snapshot.source_label || snapshot.source || "—" : meta().label || "—")}</b></span><span class="readyitem" role="status" title="${esc(clean(sourceCheckMessage))}">${label(checked ? "checked" : "unchecked")}</span>${connectionLost ? `<span class="readyitem">${label("network.interrupted")}</span>` : ""}<span class="readyscope">${label("single")}</span>`);
    patch("warnings", (!service?.running ? `<div class="alert surface">${label(service?.available ? "service.hint" : "install.hint")} ${btn("service-start", "service.start")}</div>` : "") + (meta().warning ? `<div class="alert surface">${esc(meta().warning)}</div>` : ""));
    patch("progress", snapshot.status !== "idle" ? `<div class="readystrip surface" role="status"><b>${esc(statusText())}</b><span>${label("done")} ${esc(snapshot.done ?? 0)}${Number(snapshot.target) > 0 ? ` / ${esc(snapshot.target)}` : ""}</span><span>${label("skipped")} ${esc(snapshot.skipped ?? 0)}</span><span>${label("failed")} ${esc(snapshot.failed ?? 0)}</span><span>${label("multi.active")} ${esc((snapshot.active_source ?? snapshot.source_label ?? snapshot.source) || "—")}</span></div>${(snapshot.sources || []).map(s => `<div class="readystrip surface"><b>${esc(s.label || s.source || s.id)}</b><span>${label(`status.${s.status}`)}</span><span>${label("done")} ${esc(s.done ?? 0)} / ${esc(s.target ?? 0)}</span><span>${label("failed")} ${esc(s.failed ?? 0)}</span>${s.error ? `<span>${esc(clean(s.error))}</span>` : ""}</div>`).join("")}<p class="hint">${esc(snapshot.folder || "")} · ${label("ephemeral")}${!running() ? ` · ${label("config.draft")}` : ""}</p>${snapshot.error ? `<div class="alert surface">${esc(clean(snapshot.error))}</div>` : ""}` : "");
    const structuralChange = renderedUnknown !== unknownTask();
    if (edit || structuralChange) {
      patch("basic", basicPane());
      patch("candidates", !running() && candidates.length ? `<div class="box grabber-candidates">${candidates.map((c, index) => `<button type="button" class="btn" data-act="candidate" data-index="${index}">${esc(c.name || c.id)}${c.post_count != null ? ` · ${esc(c.post_count)}` : ""}</button>`).join("")}</div>` : "");
      patch("tabs", tabs(["range", "images", "tagging", "auth", "results", "library", "logs", "config"], tab));
    }
    patch("library-progress", tab === "library" ? library.progressHtml() : "");
    if (tab === "library") { if (edit || structuralChange) patch("pane", library.pane()); }
    else if (["results", "logs", "config"].includes(tab)) patch("pane", resultPane());
    else if (edit || structuralChange) patch("pane", unknownTask() ? unknownPane() : configPane());
    renderedUnknown = unknownTask();
    patch("message", `<div class="${messageError ? "alert" : "okline"} surface grabber-message" role="${messageError ? "alert" : "status"}" ${message ? "" : "hidden"}>${esc(clean(message))}</div>`);
    applyLocks();
  }
  function notice(text, error = false, origin = "action") { message = String(text || ""); messageError = error; messageOrigin = origin; render(false); }
  function invalidateSource() {
    checked = false; sourceCheckMessage = "";
    if (messageOrigin === "source") { message = ""; messageError = false; messageOrigin = ""; }
  }
  function rememberSecrets() {
    for (const f of meta().auth_fields || []) if (f.secret && sourceValues[f.id]) secrets.add(sourceValues[f.id]);
    if (tagValues.api_key) secrets.add(tagValues.api_key);
  }
  function body() {
    rememberSecrets();
    const { source, artist, query_type, rating, ...shared } = cfg;
    return { ...shared, sources: [...selectedSources], source_configs: Object.fromEntries([...selectedSources].map(id => [id, sourceConfig(id)])), count: unlimited ? 0 : cfg.count,
      ...(cfg.images_only ? { images_only: true, tagger_id: "none", tagger_config: {}, include_artist: false, include_meta: false } : { tagger_config: taggerConfig() }) };
  }
  function validate() {
    if (running() || library.active() || annotations.active()) throw new Error(gt("alreadyRunning"));
    if (retagUnknown()) throw new Error(gt("library.progressUnknown"));
    if (cfg.images_only && capabilities.images_only !== true) throw new Error(gt("imagesOnly.unsupported"));
    if (!selectedSources.size) throw new Error(gt("multi.required"));
    if (selectedSources.size > 1 && capabilities.multi_source !== true) throw new Error(gt("multi.unsupported"));
    for (const id of selectedSources) {
      const values = sourceConfig(id);
      if (!values.artist.trim()) throw new Error(`${id}: ${gt("validation.artist")}`);
      if (!queryTypes(id).includes(values.query_type)) throw new Error(`${id}: ${gt("multi.queryUnsupported")}`);
    }
    if (!unlimited && (!Number.isInteger(Number(cfg.count)) || Number(cfg.count) < 1)) throw new Error(gt("validation.count"));
    if (!Number.isInteger(Number(cfg.start_page)) || cfg.start_page < 1 || cfg.start_page > 1000 || !Number.isInteger(Number(cfg.end_page)) || cfg.end_page < 0 || cfg.end_page > 1000 || (cfg.end_page && Number(cfg.end_page) < Number(cfg.start_page))) throw new Error(gt("validation.pages"));
    if (!/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(cfg.background_color)) throw new Error(gt("validation.color"));
    if (!cfg.images_only && cfg.tagger_id === "openai_compatible" && !remoteConsent) throw new Error(gt("remote.required"));
  }
  async function post(path, data) {
    const result = await api.post(`/api/grabber/${path}`, data);
    if (!result || result.ok === false || result.error) throw new Error(result?.error || result?.message || gt("request.failed"));
    return result;
  }
  async function loadCredentials(scope) {
    const source = scope === "tag" ? `tagger:${cfg.tagger_id}` : cfg.source;
    const epoch = scope === "tag" ? ++tagEpoch : ++sourceEpoch;
    if (source === "tagger:none" || !source) return;
    const before = { ...(scope === "tag" ? tagValues : sourceValues) };
    let data;
    try { data = await api.get("/api/grabber/credentials", { source }); }
    catch (error) {
      if (alive() && (scope === "tag" ? epoch === tagEpoch : epoch === sourceEpoch)) throw error;
      return;
    }
    if (!alive() || (scope === "tag" ? epoch !== tagEpoch : epoch !== sourceEpoch)) return;
    const values = { ...(data.values || {}) };
    // Secrets must never be populated, even if a malformed response supplies them.
    if (scope === "tag") {
      delete values.api_key;
      for (const [key, value] of Object.entries(values)) if (tagValues[key] === before[key]) tagValues[key] = value;
      tagConfigured = data.configured || [];
    } else {
      for (const f of meta().auth_fields || []) if (f.secret) delete values[f.id];
      for (const [key, value] of Object.entries(values)) if (sourceValues[key] === before[key]) sourceValues[key] = value;
      configured = data.configured || [];
      draft(source).loaded = true; stashSource();
    }
    render();
  }
  function resetProgress() {
    items = []; logs = []; logOffset = 0; itemOffset = 0; selectedItem = null; resultPage = 0;
    finalSynced = false; stopRequested = false;
  }
  function applySnapshot(next, full = false) {
    if (next.status === "idle" || Number(next.item_count) < itemOffset || Number(next.log_count) < logOffset) resetProgress();
    items = full ? (next.items || []) : items.concat(next.items || []);
    logs = logs.concat((next.logs || []).map(clean));
    logOffset = Number(next.log_count) || logs.length;
    itemOffset = Number(next.item_count) || items.length;
    snapshot = next;
    progressKnown = true;
  }
  let progressEpoch = 0, connectionLost = false;
  async function poll() {
    if (!alive() || polling || !service?.running) return;
    clearTimeout(timer);
    polling = true;
    const epoch = progressEpoch;
    try {
      let next = await api.get("/api/grabber/progress", { logs: logOffset, items: itemOffset });
      if (!alive() || epoch !== progressEpoch) return;
      const replaced = TERMINAL.has(snapshot.status) && ACTIVE.has(next.status);
      if (replaced) {
        next = await api.get("/api/grabber/progress", { logs: 0, items: 0 });
        if (!alive() || epoch !== progressEpoch) return;
        resetProgress(); invalidateMedia(); taskConfig = null; invalidateSource();
      }
      const full = !finalSynced && TERMINAL.has(next.status);
      if (full) {
        // Duplicate merging changes old final_tags in place: terminal always re-syncs items=0.
        next = await api.get("/api/grabber/progress", { logs: logOffset, items: 0 });
        if (!alive() || epoch !== progressEpoch) return;
        finalSynced = TERMINAL.has(next.status);
      }
      // Artist names can be normalized upstream; never compare them to draft input.
      if (next.status === "idle") taskConfig = null;
      applySnapshot(next, full);
      if (TERMINAL.has(next.status)) {
        stopRequested = false;
        if (messageOrigin === "stop") notice("", false, "");
      }
      if (connectionLost) { if (messageOrigin === "connection") notice("", false, ""); connectionLost = false; }
      await library.poll();
    } catch (error) {
      if (alive() && epoch === progressEpoch) { connectionLost = true; notice(gt("connection", { error: clean(error.message) }), true, "connection"); }
    } finally {
      polling = false;
      if (alive()) {
        render(false);
        clearTimeout(timer);
        timer = setTimeout(poll, 2000);
      }
    }
  }
  async function connect() {
    service = await api.get("/api/grabber/status");
    if (!alive()) return;
    if (!service.running) { notice(service.error || "", Boolean(service.error), "service"); return; }
    const data = await api.get("/api/grabber/sources");
    if (!alive()) return;
    sources = data.sources || [];
    capabilities = data.capabilities || {};
    if (capabilities.images_only !== true) cfg.images_only = false;
    if (cfg.source && !queryTypes().includes(cfg.query_type)) { cfg.query_type = queryTypes()[0]; candidates = []; invalidateSource(); notice(gt("multi.queryUnsupported"), true); }
    // A healthy service must not erase a source-test or other action result.
    if (messageOrigin === "service" || messageOrigin === "connection") notice("", false, "");
    if (!cfg.source && sources.length) { cfg.source = sources[0].id; selectedSources.add(cfg.source); await loadCredentials("source"); }
    if (!alive()) return;
    await poll();
    if (!alive()) return;
    render();
  }
  async function save(scope, clears) {
    const source = scope === "tag" ? `tagger:${cfg.tagger_id}` : cfg.source;
    const values = scope === "tag" ? { ...tagValues } : { ...sourceValues };
    const clear = clears.filter((item) => item.scope === scope).map((item) => item.field);
    rememberSecrets();
    for (const key of clear) delete values[key];
    const data = await post("credentials", { source, values, clear });
    if (!alive()) return;
    if (scope === "tag") { tagValues = data.values || {}; tagConfigured = data.configured || []; }
    else { sourceValues = data.values || {}; configured = data.configured || []; }
    for (const key of clear) pendingClears.delete(`${scope}:${key}`);
    if (scope === "source") invalidateSource();
    notice(gt("saved"));
  }
  async function action(event) {
    const target = event.target.closest?.("[data-act]");
    if (!target || !root.contains(target) || target.disabled) return;
    const act = target.dataset.act;
    if (act === "tab" || act === "open-library") {
      const nextTab = act === "open-library" ? "library" : target.dataset.tab;
      if (tab === "library" && nextTab !== "library") {
        if (!await annotations.requestNavigate({kind: "tab", value: nextTab})) return;
        library.leave();
      }
      tab = nextTab; render(); if (tab === "library") await library.enter(); return;
    }
    if (act.startsWith("annotation-") || act.startsWith("translation-")) { await annotations.action(act, target); return; }
    if (act.startsWith("library-")) { await library.action(act, target); return; }
    if (act === "detail") { selectedItem = Number(target.dataset.index); render(); return; }
    if (act === "detail-close") { selectedItem = null; render(); return; }
    if (act === "results-prev" || act === "results-next") {
      resultPage = Math.max(0, resultPage + (act === "results-next" ? 1 : -1)); render(); return;
    }
    if ((running() || library.active()) && CONFIG_ACTIONS.has(act)) return;
    if (cfg.images_only && tab !== "library" && ["save-tag", "test-tag", "models-scan"].includes(act)) return;
    if (act === "candidate") {
      const c = candidates[Number(target.dataset.index)];
      cfg.artist = String(c.query_tags?.join(" ") || (cfg.source === "pixiv" ? c.id : c.name) || c.id || "");
      candidates = []; invalidateSource(); sourceEpoch++; render(); return;
    }
    if (busy) return;
    // Capture clear choices before rendering removes the checkbox nodes.
    const clears = [...pendingClears].map((key) => { const [scope, field] = key.split(":"); return { scope, field }; });
    busy = act;
    applyLocks();
    const epoch = sourceEpoch, tagVersion = tagEpoch;
    try {
      if (act === "service-start") { await post("service/start", {}); if (alive()) await connect(); }
      else if (act === "refresh") { clearTimeout(timer); invalidateMedia(); await connect(); if (tab === "library") await library.refresh(); }
      else if (act === "models-scan") {
        const result = await post("models/scan", modelDirectory.trim() ? { directory: modelDirectory.trim() } : {});
        if (!alive() || tagVersion !== tagEpoch) return;
        modelDirectory = result.directory || modelDirectory; models = result.models || []; modelSelected = "";
        modelHint = gt(result.truncated ? "models.truncated" : models.length ? "models.found" : "models.empty");
      }
      else if (act === "stop") {
        await post("task/stop", {});
        if (alive() && !TERMINAL.has(snapshot.status)) { stopRequested = true; notice(gt("stop.hint"), false, "stop"); }
      } else if (act === "start") {
        validate();
        const payload = body();
        progressEpoch++;
        clearTimeout(timer);
        await post("task/start", payload);
        if (!alive()) return;
        // Also invalidate any read started while acceptance was in flight.
        progressEpoch++;
        taskConfig = clean(payload);
        resetProgress(); invalidateMedia(); snapshot = { status: "preparing", active_source: payload.sources[0] }; message = ""; candidates = [];
        // Keep only redacted configuration for review; clear live passwords.
        stashSource();
        for (const s of sources) for (const f of s.auth_fields || []) if (f.secret) delete draft(s.id).values[f.id];
        delete tagValues.api_key;
        clearTimeout(timer); await poll();
      } else if (act === "save-source" || act === "save-tag") {
        // Use the captured selection, never checkbox nodes replaced by an async render.
        await save(act === "save-tag" ? "tag" : "source", clears);
      } else {
        let result;
        if (act === "search") {
          if (!canSearch()) throw new Error(gt("search.unsupported"));
          if (!cfg.artist.trim()) throw new Error(gt("validation.artist"));
          result = await post("artists/search", { ...currentBody(), query: cfg.artist });
        } else if (act === "test-tag") result = await post("tagger/test", { tagger_id: cfg.tagger_id, tagger_config: taggerConfig() });
        else if (act === "session-open" || act === "session-check") result = await post(`session/${act === "session-open" ? "open" : "check"}`, { source: cfg.source, artist: cfg.artist });
        else if (act === "test-source") result = await post("source/test", currentBody());
        if (!alive() || epoch !== sourceEpoch || tagVersion !== tagEpoch) return;
        if (act === "search") { candidates = result.artists || []; notice(candidates.length ? gt("search.choose") : gt("search.empty")); }
        else if (act.startsWith("session-")) notice(gt(result.logged_in ? "session.loggedIn" : "session.pending") + (result.message ? ` · ${clean(result.message)}` : ""));
        else if (act === "test-source") {
          checked = true; sourceCheckMessage = result.message || gt("checked");
          if (messageOrigin === "source") { message = ""; messageError = false; }
          render(false);
        } else notice(result.message || gt("checked"));
      }
    } catch (error) {
      if (alive() && epoch === sourceEpoch && tagVersion === tagEpoch) {
        if (act === "test-source") invalidateSource();
        notice(clean(error.message), true, ["service-start", "refresh"].includes(act) ? "service" : act === "test-source" ? "source" : "action");
      }
    } finally {
      busy = false;
      if (alive()) {
        if (service?.running && !polling) { clearTimeout(timer); timer = setTimeout(poll, 2000); }
        render();
        // pendingClears survives tabs/polls; only a successful save consumes it.
      }
    }
  }
  function capture(event) {
    const node = event.target.closest?.("[data-field],[data-clear]");
    if (!node || node.disabled) return;
    if (annotations.capture(node) || library.capture(node)) return;
    if (node.dataset.clear) {
      const key = `${node.dataset.scope}:${node.dataset.clear}`;
      if (node.checked) pendingClears.add(key); else pendingClears.delete(key);
      if (node.dataset.scope === "source") { invalidateSource(); sourceEpoch++; render(false); }
      return;
    }
    const { field: name, scope } = node.dataset;
    if ((running() || library.active()) && name !== "filter") return;
    if (scope === "cfg" && ["source", "tagger_id"].includes(name)) return;
    const value = node.type === "checkbox" ? node.checked : node.type === "number" ? (node.value === "" ? "" : Number(node.value)) : node.value;
    const before = scope === "cfg" ? cfg[name] : scope === "source" ? sourceValues[name] : scope === "tag" ? tagValues[name] : undefined;
    if (scope === "selection") { if (value) selectedSources.add(name); else selectedSources.delete(name); render(false); return; }
    if (scope === "mapping") { draft(name).artist = value; invalidateSource(); return; }
    if (scope === "model") { if (name === "modelDirectory") modelDirectory = value; else modelSelected = value; return; }
    if (scope === "cfg") cfg[name] = value;
    else if (scope === "source") sourceValues[name] = value;
    else if (scope === "tag") { tagValues[name] = value; library.resetConsent(); }
    else if (name === "filter" && filter !== value) { filter = value; resultPage = 0; selectedItem = null; }
    else if (name === "unlimited") unlimited = value;
    else if (name === "remoteConsent") remoteConsent = value;
    if (before !== value && (scope === "source" || (scope === "cfg" && !TAG_OPTIONS.has(name)))) {
      invalidateSource(); sourceEpoch++;
      if (["artist", "query_type", "source"].includes(name)) { candidates = []; patch("candidates", ""); }
      render(false);
    }
    rememberSecrets();
  }
  async function change(event) {
    const node = event.target.closest?.("[data-field],[data-clear]");
    if (!node || node.disabled) return;
    capture(event);
    const name = node.dataset.field;
    if (["annotation", "translation"].includes(node.dataset.scope)) { await annotations.change(node); return; }
    if (node.dataset.scope === "library") { await library.change(node); return; }
    if (name === "source") {
      if (node.value === cfg.source) return;
      stashSource(); cfg.source = node.value;
      sourceEpoch++; candidates = []; message = ""; invalidateSource();
      const d = draft(cfg.source);
      sourceValues = d.values; configured = d.configured;
      cfg.artist = d.artist; cfg.query_type = d.query_type; cfg.rating = d.rating;
      if (!queryTypes().includes(cfg.query_type)) { cfg.query_type = queryTypes()[0]; notice(gt("multi.queryUnsupported"), true); }
      for (const key of pendingClears) if (key.startsWith("source:")) pendingClears.delete(key);
      for (const key of d.clears) pendingClears.add(key);
      for (const f of meta().extra_fields || []) if (f.options?.length && sourceValues[f.id] == null) sourceValues[f.id] = f.options[0][0];
      render();
      if (!d.loaded) try { await loadCredentials("source"); } catch (error) { if (alive()) notice(clean(error.message), true); }
    } else if (name === "tagger_id") {
      if (cfg.tagger_id === node.value) return;
      tagDrafts.set(cfg.tagger_id, { values: tagValues, configured: tagConfigured });
      cfg.tagger_id = node.value; tagEpoch++; remoteConsent = false; library.resetConsent();
      const saved = tagDrafts.get(cfg.tagger_id);
      tagValues = saved?.values || Object.fromEntries((TAG_FIELDS[cfg.tagger_id] || []).map(([key, , fallback]) => [key, fallback]));
      tagConfigured = saved?.configured || [];
      for (const key of pendingClears) if (key.startsWith("tag:")) pendingClears.delete(key);
      cfg.tag_merge_mode = cfg.tagger_id === "none" ? "native_only" : "tagger_only";
      render();
      if (!saved) try { await loadCredentials("tag"); } catch (error) { if (alive()) notice(clean(error.message), true); }
    } else if (name === "modelSelected") {
      const model = modelSelected === "" ? null : models[Number(modelSelected)];
      if (model) { tagValues.model_path = model.model_path; tagValues.tags_path = model.tags_path || ""; modelHint = model.tags_path ? gt("models.paired") : gt("models.noCsv"); }
      render();
    } else if (["filter", "unlimited", "query_type", "images_only"].includes(name) || ["selection", "mapping"].includes(node.dataset.scope) || (tab === "library" && node.dataset.scope === "tag")) render();
  }

  // The shell guards all route/language re-renders through this hook. Keep the
  // hash-only fallback for standalone hosts that do not provide shell navigation.
  const beforeNavigate = intent => annotations.requestNavigate(intent);
  main.__studioBeforeNavigate = beforeNavigate;
  let acceptedHash = globalThis.location?.hash || "#/grabber", approvedRoute = false;
  async function guardRoute(event) {
    if (approvedRoute) { approvedRoute = false; return; }
    if (!annotations.dirty() && !annotations.active()) return;
    const next = globalThis.location.hash;
    event.stopImmediatePropagation();
    globalThis.history.replaceState(null, "", acceptedHash);
    if (await annotations.requestNavigate({ kind: "route", value: next }) && alive()) {
      approvedRoute = true; globalThis.location.hash = next;
    }
  }
  if (!ctx.navigate) globalThis.window?.addEventListener("hashchange", guardRoute, true);
  root.addEventListener("click", action);
  root.addEventListener("input", capture);
  root.addEventListener("change", change);
  const cleanup = () => {
    disposed = true; sourceEpoch++; tagEpoch++; clearTimeout(timer);
    root.removeEventListener("click", action); root.removeEventListener("input", capture); root.removeEventListener("change", change);
    globalThis.window?.removeEventListener("hashchange", guardRoute, true);
    annotations.dispose(); annotationClient.dispose(); library.dispose(); media.dispose(); sourceDrafts.clear(); tagDrafts.clear(); selectedSources.clear();
    secrets.clear(); pendingClears.clear(); parts.clear(); sourceValues = {}; tagValues = {}; taskConfig = null;
    if (main.__studioBeforeNavigate === beforeNavigate) main.__studioBeforeNavigate = null;
    if (main.__studioCleanup === cleanup) main.__studioCleanup = null;
  };
  main.__studioCleanup = cleanup;
  render();
  try { await connect(); } catch (error) { if (alive()) notice(clean(error.message), true, "service"); }
  return cleanup;
}
