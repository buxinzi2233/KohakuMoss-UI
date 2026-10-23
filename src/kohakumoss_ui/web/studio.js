/* Studio views: profile, notebook and queue. Every visible label goes through
 * ctx.t() with a studio.* key; the EN table below is only the fallback used when
 * the dictionary has no entry yet (and by the standalone render test).
 *
 * Interaction is delegated from the view root through data-act, so a re-render
 * never leaves a stale listener behind. Each API call degrades on its own: a
 * missing endpoint renders an inline error inside its own block instead of
 * throwing, and the rest of the page stays readable.
 *
 * Visual rules (teletype): semantic classes from app.css / themes only, no inline
 * colour, no radius, no shadow, no gradient. Numbers live in .num / .ledger td.v,
 * status text in the .st stamp family, one frame per independent reading region.
 */
import * as fallbackApi from "./api.js";
import { bar, duration, esc, pct, when } from "./format.js";
import { renderHeatmap, renderHistory, renderPublishStatus } from "./heatmap.js";

const FILTERS = ["all", "idea", "todo", "queued", "trained", "dropped"];
const EDITABLE_STATUS = ["idea", "todo", "queued", "trained", "dropped"];
const ACTIVE_QUEUE = ["waiting", "starting", "running", "paused"];
const RETRYABLE = ["failed", "canceled"];

const EN = {
  "studio.refresh": "REFRESH",
  "studio.run.new": "+ NEW RUN",
  "studio.error": "ACTION FAILED",
  "studio.profile.title": "PROFILE",
  "studio.profile.loading": "READING PROFILE",
  "studio.profile.meta": "{runs} RUNS · {days} ACTIVE DAYS",
  "studio.profile.unnamed": "UNTITLED",
  "studio.profile.name.edit": "RENAME",
  "studio.profile.name.ph": "DISPLAY NAME",
  "studio.profile.note.edit": "NOTE",
  "studio.profile.note.ph": "WHY THIS ONE",
  "studio.profile.civitai.none": "CIVITAI · NOT CONNECTED",
  "studio.profile.civitai.connected": "CIVITAI · {username}",
  "studio.profile.civitai.key": "CIVITAI · KEY SAVED",
  "studio.profile.stat.runs": "RUNS",
  "studio.profile.stat.steps": "STEPS",
  "studio.profile.stat.days": "ACTIVE DAYS",
  "studio.profile.stat.published": "PUBLISHED",
  "studio.profile.upload": "UPLOAD AVATAR",
  "studio.profile.avatar.remove": "REMOVE",
  "studio.profile.avatar.hint": "PNG / JPEG / WEBP, 5 MB MAX. STORED LOCALLY.",
  "studio.profile.avatar.size": "IMAGE IS LARGER THAN 5 MB",
  "studio.profile.avatar.type": "ONLY PNG, JPEG OR WEBP",
  "studio.profile.best.title": "BEST LORA",
  "studio.profile.best.empty": "NO FAVORITE YET. PIN A RUN FROM HISTORY.",
  "studio.profile.best.steps": "STEPS",
  "studio.profile.best.lr": "LR",
  "studio.profile.best.dim": "DIM",
  "studio.profile.best.export": "EXPORT",
  "studio.profile.best.open": "OPEN",
  "studio.profile.best.unpin": "UNPIN",
  "studio.profile.pin": "PIN",
  "studio.profile.pinned": "PINNED",
  "studio.profile.pin.done": "PINNED {run}",
  "studio.profile.unpin.done": "UNPINNED",
  "studio.profile.activity.title": "ACTIVITY",
  "studio.profile.history.title": "HISTORY",
  "studio.notebook.title": "NOTEBOOK",
  "studio.notebook.loading": "READING NOTEBOOK",
  "studio.notebook.meta": "{count} ENTRIES · {shown} SHOWN",
  "studio.notebook.search": "SEARCH TITLE / BASE / TAG",
  "studio.notebook.new": "+ NEW ENTRY",
  "studio.notebook.empty.title": "NOTEBOOK IS EMPTY",
  "studio.notebook.empty.body": "Note what you want to try; promote an entry once it has a config.",
  "studio.notebook.none.title": "NOTHING IN THIS FILTER",
  "studio.notebook.none.body": "Switch the filter or add an entry.",
  "studio.notebook.edit": "EDIT",
  "studio.notebook.delete": "DELETE",
  "studio.notebook.queue": "QUEUE",
  "studio.notebook.prefill": "PREFILL",
  "studio.notebook.save": "SAVE",
  "studio.notebook.cancel": "CANCEL",
  "studio.notebook.no.config": "THIS ENTRY HAS NO CONFIG YET. USE PREFILL TO BUILD ONE.",
  "studio.notebook.saved": "ENTRY SAVED",
  "studio.notebook.deleted": "ENTRY DELETED",
  "studio.notebook.queued": "QUEUED {title}",
  "studio.notebook.form.title": "TITLE",
  "studio.notebook.form.status": "STATUS",
  "studio.notebook.form.base": "BASE MODEL",
  "studio.notebook.form.dim": "DIM",
  "studio.notebook.form.steps": "STEPS",
  "studio.notebook.form.lr": "LR",
  "studio.notebook.form.dataset": "DATASET",
  "studio.notebook.form.config": "CONFIG",
  "studio.notebook.form.tags": "TAGS",
  "studio.notebook.form.note": "NOTE",
  "studio.notebook.col.status": "STATUS",
  "studio.notebook.col.entry": "ENTRY",
  "studio.notebook.col.base": "BASE",
  "studio.notebook.col.dim": "DIM",
  "studio.notebook.col.steps": "STEPS",
  "studio.notebook.col.lr": "LR",
  "studio.notebook.col.updated": "UPDATED",
  "studio.notebook.col.actions": "ACTION",
  "studio.queue.title": "QUEUE",
  "studio.queue.loading": "READING QUEUE",
  "studio.queue.meta": "{count} ITEMS · V{version}",
  "studio.queue.idle": "IDLE",
  "studio.queue.running": "RUNNING {name}",
  "studio.queue.paused": "PAUSED",
  "studio.queue.paused.by": "PAUSED BY {who}",
  "studio.queue.pause": "PAUSE",
  "studio.queue.resume": "RESUME",
  "studio.queue.clear": "CLEAR DONE",
  "studio.queue.empty.title": "QUEUE IS EMPTY",
  "studio.queue.empty.body": "Add an entry from NOTEBOOK, or start a run from NEW RUN.",
  "studio.queue.col.run": "RUN",
  "studio.queue.col.config": "CONFIG",
  "studio.queue.col.status": "STATUS",
  "studio.queue.col.progress": "PROGRESS",
  "studio.queue.col.elapsed": "ELAPSED",
  "studio.queue.col.actions": "ACTION",
  "studio.queue.up": "UP",
  "studio.queue.down": "DOWN",
  "studio.queue.cancel": "CANCEL",
  "studio.queue.retry": "RETRY",
  "studio.queue.open": "OPEN",
  "studio.queue.unknown": "unknown",
  "studio.queue.started": "STARTED {when}",
};

const ui = {
  error: null,
  nbFilter: "all",
  nbSearch: "",
  nbEditing: null,
  nbCreating: false,
  nameEditing: false,
  noteEditing: false,
  pinDraft: null,
  pinNotes: {},
  publishRefresh: false,
};

/* Labels interpolate {name} and escape exactly once, so a dictionary value can
   never smuggle markup into the page. */
function translate(handler, key, vars) {
  if (typeof handler === "function") {
    const out = handler(key, vars);
    if (typeof out === "string" && out && out !== key) return esc(out);
  }
  const template = EN[key] ?? key;
  return esc(template.replace(/\{(\w+)\}/g, (match, name) => (
    vars && vars[name] != null ? String(vars[name]) : match
  )));
}

const isNode = (value) => !!value && typeof value === "object" && value.nodeType === 1;

/* Views take (main, ctx) like views.js, but a single ctx argument also works so a
   caller can mount a view without passing the container. */
function resolve(main, ctx) {
  if (isNode(main)) return { root: main, ctx: ctx || {} };
  return { root: (isNode(ctx) ? ctx : null) || document.getElementById("main"), ctx: main || {} };
}

function messageOf(error) {
  if (error && typeof error === "object" && error.message) return String(error.message);
  const text = String(error == null ? "" : error);
  return text || "REQUEST FAILED";
}

/* The Lead passes ctx.api once the shared client lands; until then the shipped
   api.js is used directly. DELETE is exported as remove() there, so accept both. */
function client() {
  const source = fallbackApi;
  return {
    get(path, query) {
      if (typeof source.get === "function") return Promise.resolve(source.get(path, query || {}));
      return Promise.reject(new Error("GET " + path + " unavailable"));
    },
    post(path, body) {
      if (typeof source.post === "function") return Promise.resolve(source.post(path, body || {}));
      return Promise.reject(new Error("POST " + path + " unavailable"));
    },
    del(path) {
      const fn = source.remove || source.del || source.delete;
      if (typeof fn === "function") return Promise.resolve(fn.call(source, path));
      return Promise.reject(new Error("DELETE " + path + " unavailable"));
    },
    upload(path, body, type) {
      const fn = source.postRaw || source.upload;
      if (typeof fn === "function") return Promise.resolve(fn.call(source, path, body, type));
      return Promise.reject(new Error("UPLOAD " + path + " unavailable"));
    },
    url(value) {
      const fn = source.backgroundUrl || fallbackApi.backgroundUrl;
      if (typeof fn !== "function" || !value) return value ? String(value) : "";
      try {
        return fn(String(value));
      } catch {
        return String(value);
      }
    },
  };
}

function queryOf(ctx) {
  if (typeof ctx.query === "function") {
    try {
      const value = ctx.query();
      if (value instanceof URLSearchParams) return value;
      if (value && typeof value === "object") return new URLSearchParams(value);
      if (typeof value === "string") return new URLSearchParams(value);
    } catch {
      /* fall through to the hash */
    }
  }
  const hash = String((typeof location !== "undefined" && location.hash) || "");
  const index = hash.indexOf("?");
  return new URLSearchParams(index >= 0 ? hash.slice(index + 1) : "");
}

/* app.js exposes ctx.lang() as a function; older callers passed a string. */
function langOf(ctx) {
  if (ctx && typeof ctx.lang === "function") {
    try {
      const value = ctx.lang();
      if (value === "zh" || value === "en") return value;
    } catch {
      /* fall through */
    }
  }
  return ctx && ctx.lang === "zh" ? "zh" : "en";
}

function count(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  return Math.round(number).toLocaleString();
}

function text(value) {
  if (value == null || value === "") return "—";
  return esc(String(value));
}

function initial(name) {
  const label = String(name || "").trim();
  if (!label) return "K";
  return Array.from(label)[0].toUpperCase();
}

function stampOf(value) {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return when(value);
  const raw = String(value);
  return /^\d+$/.test(raw) ? when(Number(raw)) : raw;
}

function statusStamp(status) {
  const key = String(status || "unknown").toLowerCase();
  return '<span class="st" data-status="' + esc(key) + '">' + esc(key.toUpperCase()) + "</span>";
}

function alertBlock(t, message) {
  return '<div class="alert surface"><div class="h">' + t("studio.error", {}) + "</div><div>"
    + esc(message) + "</div></div>";
}

function inlineError(t, message) {
  return '<div class="okline surface"><b>' + t("studio.error", {}) + "</b> · " + esc(message) + "</div>";
}

function noticeFor(t, view) {
  if (!ui.error || ui.error.view !== view) return "";
  return alertBlock(t, ui.error.message);
}

function failure(view, error) {
  ui.error = { view, message: messageOf(error) };
}

function stateRow(key, value) {
  return '<tr><td class="k">' + esc(key) + '</td><td class="dots"></td><td class="v">'
    + (value == null ? "—" : value) + "</td></tr>";
}

function prefillHref(item) {
  const params = new URLSearchParams();
  params.set("from", String(item.id || ""));
  if (item.config) params.set("config", String(item.config));
  return "#/new?" + params.toString();
}

function formRow(t, item) {
  const value = (key) => esc(item && item[key] != null ? String(item[key]) : "");
  const options = EDITABLE_STATUS.map((key) => {
    const status = item && item.status ? String(item.status).toLowerCase() : "idea";
    return '<option value="' + key + '"' + (key === status ? " selected" : "") + ">"
      + esc(key.toUpperCase()) + "</option>";
  }).join("");
  const tags = item && Array.isArray(item.tags) ? item.tags.join(", ") : (item && item.tags ? String(item.tags) : "");
  const field = (name, label, control) => '<div class="lab">' + label + '</div><div class="val">' + control + "</div>";
  return '<tr class="nb-form"><td colspan="8"><div class="form">'
    + field("title", t("studio.notebook.form.title", {}),
      '<input class="field" data-nb-field="title" value="' + value("title") + '">')
    + field("status", t("studio.notebook.form.status", {}),
      '<select class="field" data-nb-field="status"><button type="button"><selectedcontent></selectedcontent></button>' + options + "</select>")
    + field("base", t("studio.notebook.form.base", {}),
      '<input class="field" data-nb-field="base" value="' + value("base") + '">')
    + field("dim", t("studio.notebook.form.dim", {}),
      '<input class="field" data-nb-field="dim" inputmode="numeric" value="' + value("dim") + '">')
    + field("steps", t("studio.notebook.form.steps", {}),
      '<input class="field" data-nb-field="steps" inputmode="numeric" value="' + value("steps") + '">')
    + field("lr", t("studio.notebook.form.lr", {}),
      '<input class="field" data-nb-field="lr" inputmode="decimal" value="' + value("lr") + '">')
    + field("dataset", t("studio.notebook.form.dataset", {}),
      '<input class="field" data-nb-field="dataset" value="' + value("dataset") + '">')
    + field("config", t("studio.notebook.form.config", {}),
      '<input class="field" data-nb-field="config" value="' + value("config") + '">')
    + field("tags", t("studio.notebook.form.tags", {}),
      '<input class="field" data-nb-field="tags" value="' + esc(tags) + '">')
    + field("note", t("studio.notebook.form.note", {}),
      '<textarea class="field" data-nb-field="note" rows="2">' + value("note") + "</textarea>")
    + '</div><div class="nb-actions">'
    + '<button class="btn pri" data-act="nb-save">' + t("studio.notebook.save", {}) + "</button>"
    + '<button class="btn" data-act="nb-cancel">' + t("studio.notebook.cancel", {}) + "</button>"
    + "</div></td></tr>";
}

function collect(scope) {
  const values = {};
  scope.querySelectorAll("[data-nb-field]").forEach((field) => {
    const key = field.dataset.nbField;
    let value = field.value;
    if (key === "steps" || key === "dim" || key === "lr") {
      const number = Number(value);
      value = value === "" || !Number.isFinite(number) ? null : number;
    } else if (key === "tags") {
      value = String(value).split(",").map((part) => part.trim()).filter(Boolean);
    } else {
      value = String(value);
      if (value === "") value = null;
    }
    values[key] = value;
  });
  return values;
}

export async function profileView(main, ctx) {
  const { root, ctx: c } = resolve(main, ctx);
  const t = (key, vars) => translate(c.t, key, vars);
  const api = client();
  const year = Number(queryOf(c).get("year")) || null;
  root.innerHTML = '<div class="loading surface">' + t("studio.profile.loading", {}) + "</div>";

  const publishTask = api.get("/api/publish", ui.publishRefresh ? {refresh:"1"} : {})
    .catch(error => ({__error:messageOf(error)}));
  ui.publishRefresh = false;
  const [profile, heat, history] = await Promise.all([
    api.get("/api/profile").catch((error) => ({ __error: messageOf(error) })),
    api.get("/api/heatmap", year ? { year } : { days: 365 }).catch((error) => ({ __error: messageOf(error) })),
    api.get("/api/history").catch((error) => ({ __error: messageOf(error) })),
  ]);
  const p = profile && !profile.__error ? profile : {};
  const stats = p.stats && typeof p.stats === "object" ? p.stats : {};
  const civitai = p.civitai && typeof p.civitai === "object" ? p.civitai : {};
  const runs = history && !history.__error && Array.isArray(history.runs) ? history.runs : [];
  const name = String(p.displayName || t("studio.profile.unnamed", {}));
  const avatarUrl = p.avatarUrl ? String(p.avatarUrl) : "";

  const civitaiLines = [];
  if (civitai.username) civitaiLines.push(t("studio.profile.civitai.connected", { username: civitai.username }));
  else if (civitai.hasKey) civitaiLines.push(t("studio.profile.civitai.key", {}));
  else civitaiLines.push(t("studio.profile.civitai.none", {}));

  // The portrait itself is the upload control: a label bound to the hidden file
  // input, so a click anywhere on the face opens the picker. The keydown handler
  // in mountStudio turns Enter/Space on it into the same click.
  const faceInner = avatarUrl
    ? '<img class="pf-face" src="' + esc(fallbackApi.backgroundUrl(avatarUrl)) + '" alt="' + esc(name) + '">'
    : '<span class="pf-face pf-initial" aria-hidden="true">' + esc(initial(name)) + "</span>";
  const pickLabel = esc(t("studio.profile.avatar.pick", {}));
  const face = '<label class="pf-face-pick" for="pf-file" role="button" tabindex="0"'
    + ' aria-label="' + pickLabel + '" title="' + pickLabel + '">'
    + faceInner
    + "</label>";

  const nameBlock = ui.nameEditing
    ? '<div class="pf-name">'
      + '<input class="field" id="pf-name-input" value="' + esc(p.displayName || "")
      + '" placeholder="' + esc(t("studio.profile.name.ph", {})) + '">'
      + '<button class="btn mini" data-act="name-save">' + t("common.save", {}) + "</button>"
      + '<button class="btn mini" data-act="name-cancel">' + t("common.cancel", {}) + "</button>"
      + "</div>"
    : '<div class="pf-name">'
      + '<button type="button" class="pf-name-edit" data-act="name-edit"'
      + ' title="' + esc(t("studio.profile.name.hint", {})) + '">' + esc(name) + "</button>"
      + "</div>";

  const identity = '<div class="box pad">'
    + face
    + nameBlock
    + '<div class="pf-sub dim">' + esc(civitaiLines.join(" ")) + "</div>"
    + '<div class="hint" data-civitai-status role="status">' + (civitai.username ? t("studio.publish.api.loading", {}) : "") + "</div>"
    + '<table class="ledger"><tbody>'
    + stateRow(t("studio.profile.stat.runs", {}), count(runs.length))
    + stateRow(t("studio.profile.stat.steps", {}), count(stats.steps))
    + stateRow(t("studio.profile.stat.days", {}), count(stats.activeDays ?? stats.active_days))
    + stateRow(t("studio.profile.stat.published", {}), '<span data-civitai-count>' + (stats.published == null ? "—" : count(stats.published)) + "</span>")
    + "</tbody></table>"
    + '<input class="field pf-file" id="pf-file" type="file" data-avatar-file'
    + ' aria-label="' + pickLabel + '"'
    + ' accept="image/png,image/jpeg,image/webp">'
    + (avatarUrl ? '<button class="btn mini" data-act="avatar-clear">' + t("studio.profile.avatar.remove", {}) + "</button>" : "")
    + "</div>";

  const pins = Array.isArray(p.pins) ? p.pins : [];
  const pinCards = pins.map(pin => {
    const row = runs.find(item => item.name === pin.run);
    const files = row?.exports || [];
    const file = files.find(name => /\.(safetensors|ckpt|pt|pth|bin)$/i.test(name)) || "";
    return '<article class="box pf-pin"><div class="pf-pin-head"><h4><a href="#/run/' + encodeURIComponent(pin.run) + '">' + esc(pin.run) + '</a></h4>'
      + (row ? statusStamp(row.status) : '<span class="hint">' + t("studio.profile.pins.missing", {}) + '</span>') + '</div>'
      + (ui.noteEditing === pin.run
        ? '<div class="pf-actions"><textarea class="field" id="pf-note-input" rows="3" aria-label="' + t("studio.profile.note.edit", {}) + '" placeholder="' + t("studio.profile.note.ph", {}) + '">' + esc(pin.note || "") + '</textarea>'
          + '<button class="btn mini" data-act="fav-note-save" data-id="' + esc(pin.run) + '">' + t("common.save", {}) + '</button>'
          + '<button class="btn mini" data-act="fav-note-cancel">' + t("common.cancel", {}) + '</button></div>'
        : '<button class="pf-pin-note" data-act="fav-note" data-id="' + esc(pin.run) + '">' + esc(pin.note || t("studio.profile.pins.note", {})) + '</button>')
      + '<div class="pf-pin-file hint">' + (file ? esc(file) : t("studio.profile.pins.noexport", {})) + '</div>'
      + (row ? '<a class="pf-pin-meta pf-pin-params" href="#/run/' + encodeURIComponent(pin.run) + '?tab=config">' : '<div class="pf-pin-meta">')
      + '<span>' + t("studio.profile.pins.steps", {steps:count(row?.steps)}) + '</span>'
      + (row?.dim != null ? '<span>' + t("studio.profile.pins.dim", {dim:row.dim}) + '</span>' : '')
      + (row?.lr != null ? '<span>' + t("studio.profile.pins.lr", {lr:row.lr}) + '</span>' : '')
      + (row ? '<span class="pf-pin-view">' + t("studio.profile.pins.params", {}) + '</span></a>' : '</div>') + '</article>';
  }).join("");
  const candidates = [...runs, ...pins.filter(pin => !runs.some(row => row.name === pin.run)).map(pin => ({name:pin.run}))];
  const picker = ui.pinDraft !== null ? '<div class="box pf-pin-editor"><div class="pf-section-head"><h4>' + t("studio.profile.pins.choose", {}) + '</h4>'
    + '<span class="hint" id="pf-pin-count">' + t("studio.profile.pins.count", {count:ui.pinDraft.length}) + '</span></div>'
    + '<label class="pf-pin-search">' + t("studio.profile.pins.search", {}) + '<input class="field" data-pin-search type="search"></label>'
    + '<div class="pf-pin-choices">' + candidates.map(row => '<label class="pf-pin-choice" data-pin-row><input type="checkbox" data-pin-choice value="' + esc(row.name) + '"'
      + (ui.pinDraft.some(pin => pin.run === row.name) ? ' checked' : '') + '><span>' + esc(row.name) + '</span></label>').join("")
    + '</div><p class="hint" data-pin-error role="status" hidden></p><div class="pf-actions">'
    + '<button class="btn pri" data-act="pins-save">' + t("common.save", {}) + '</button><button class="btn" data-act="pins-cancel">' + t("common.cancel", {}) + '</button></div></div>' : "";
  const best = '<section class="pf-showcase"><div class="pf-section-head surface"><h3>' + t("studio.profile.pins.title", {}) + '</h3>'
    + '<button class="btn mini" data-act="pins-open">' + t("studio.profile.pins.customize", {}) + '</button></div>' + picker
    + (pins.length ? '<div class="pf-pins">' + pinCards + '</div>' : '<div class="box pf-pins-empty">' + t("studio.profile.pins.empty", {}) + '</div>') + '</section>';

  const activity = '<div class="box"><h3>' + t("studio.profile.activity.title", {})
    + "<b>" + esc(year || "") + "</b></h3>"
    + (heat.__error
      ? inlineError(t, heat.__error)
      : renderHeatmap({
        days: heat.days, totals: heat.totals, years: heat.years, today: heat.today, year,
        t: c.t, lang: langOf(c),
      }))
    + "</div>";

  const historyCard = '<div class="box"><h3>' + t("studio.profile.history.title", {})
    + "<b>" + count(runs.length) + "</b></h3>"
    + (history.__error
      ? inlineError(t, history.__error)
      : renderHistory(runs, {
        t: c.t, year,
        rowMeta: row => '<span class="civitai-result" data-civitai-run="' + esc(row.name) + '">'
          + renderPublishStatus(null, row.name, {t:c.t, pending:Boolean(civitai.username)}) + '</span>',
      }))
    + "</div>";

  root.innerHTML = '<div class="studio" data-studio="profile">'
    + '<div class="vhead surface"><h2>' + t("nav.profile", {}) + "</h2>"
    + '<div class="meta">' + t("studio.profile.meta", {
      runs: count(runs.length), days: count(stats.activeDays ?? stats.active_days),
    }) + "</div>"
    + '<div class="acts"><button class="btn" data-act="refresh">' + t("studio.refresh", {}) + "</button>"
    + '<a class="btn pri" href="#/new">' + t("studio.run.new", {}) + "</a></div></div>"
    + noticeFor(t, "profile")
    + '<div class="studio-split"><div class="studio-aside">' + identity + "</div>"
    + '<div class="studio-stack">' + best + activity + historyCard + "</div></div>"
    + "</div>";

  mountStudio(root, c);
  // The local profile stays usable while Civitai is slow. A stale request must
  // never replace a newer route, editor or another rendering of this profile.
  const scope = root.querySelector('[data-studio="profile"]');
  publishTask.then(result => {
    if (!scope || !root.contains(scope)) return;
    const status = scope.querySelector("[data-civitai-status]");
    const total = scope.querySelector("[data-civitai-count]");
    total.textContent = result.count == null ? "—" : count(result.count);
    status.textContent = result.__error ? t("studio.publish.api.failed", {})
      : result.error ? t(result.civitai?.lastSync ? "studio.publish.api.stale" : "studio.publish.api.failed", {})
        : result.civitai?.lastSync ? t("studio.publish.api.synced", {when:when(result.civitai.lastSync)}) : "";
    scope.querySelectorAll("[data-civitai-run]").forEach(slot => {
      slot.innerHTML = renderPublishStatus(result, slot.dataset.civitaiRun, {t:c.t});
    });
  });
}

export async function notebookView(main, ctx) {
  const { root, ctx: c } = resolve(main, ctx);
  const t = (key, vars) => translate(c.t, key, vars);
  const api = client();

  if (FILTERS.indexOf(ui.nbFilter) === -1) ui.nbFilter = "all";
  root.innerHTML = '<div class="loading surface">' + t("studio.notebook.loading", {}) + "</div>";

  let data = null;
  try {
    data = await api.get("/api/notebook");
  } catch (error) {
    failure("notebook", error);
  }

  const items = data && Array.isArray(data.items) ? data.items : [];
  const statusOf = (item) => String(item && item.status ? item.status : "idea").toLowerCase();
  const counted = {};
  FILTERS.forEach((key) => {
    counted[key] = key === "all" ? items.length : items.filter((item) => statusOf(item) === key).length;
  });
  const visible = items.filter((item) => ui.nbFilter === "all" || statusOf(item) === ui.nbFilter);

  const filter = '<div class="seg" role="group">' + FILTERS.map((key) => {
    const on = key === ui.nbFilter;
    const label = key === "all" ? "ALL" : key.toUpperCase();
    const open = on ? '<button type="button" class="on"' : '<button type="button"';
    return open + ' data-act="nb-filter" data-id="' + key + '" aria-pressed="' + on + '">'
      + esc(label) + " " + counted[key] + "</button>";
  }).join("") + "</div>";

  const rows = visible.map((item) => {
    const id = String(item.id || "");
    const statusKey = statusOf(item);
    const haystack = [item.title, item.base, item.note, item.dataset, item.config,
      Array.isArray(item.tags) ? item.tags.join(" ") : item.tags]
      .filter(Boolean).join(" ").toLowerCase();
    const actions = [];
    /* An entry without a config cannot be queued yet: the scheduler launches a
       config, so the button stays visible but disabled instead of vanishing. */
    actions.push('<button class="btn mini" data-act="nb-queue" data-id="' + esc(id) + '"'
      + (item.config ? "" : ' disabled title="' + esc(t("studio.notebook.queue.noconfig", {})) + '"') + ">"
      + t("studio.notebook.queue", {}) + "</button>");
    actions.push('<a class="btn mini" href="' + esc(prefillHref(item)) + '">'
      + t("studio.notebook.prefill", {}) + "</a>");
    actions.push('<button class="btn mini" data-act="nb-edit" data-id="' + esc(id) + '">'
      + t("studio.notebook.edit", {}) + "</button>");
    actions.push('<button class="btn mini" data-act="nb-delete" data-id="' + esc(id) + '">'
      + t("studio.notebook.delete", {}) + "</button>");

    const row = '<tr data-nb-row data-status="' + esc(statusKey) + '" data-text="' + esc(haystack) + '">'
      + "<td>" + statusStamp(statusKey) + "</td>"
      + "<td><b>" + esc(item.title || "—") + "</b>"
      + (item.note ? '<div class="dim">' + esc(item.note) + "</div>" : "") + "</td>"
      + '<td class="wrap dim">' + esc(item.base || "—") + "</td>"
      + '<td class="num">' + text(item.dim) + "</td>"
      + '<td class="num">' + text(item.steps) + "</td>"
      + '<td class="num">' + text(item.lr) + "</td>"
      + '<td class="num">' + esc(stampOf(item.updated_at)) + "</td>"
      + '<td class="nb-acts">' + actions.join(" ") + "</td>"
      + "</tr>";
    return ui.nbEditing === id && id ? row + formRow(t, item) : row;
  }).join("");

  const created = ui.nbCreating ? formRow(t, null) : "";
  const empty = !items.length
    ? '<div class="empty surface"><b>' + t("studio.notebook.empty.title", {}) + "</b><div>"
      + t("studio.notebook.empty.body", {}) + "</div></div>"
    : (!visible.length
      ? '<div class="empty surface"><b>' + t("studio.notebook.none.title", {}) + "</b><div>"
        + t("studio.notebook.none.body", {}) + "</div></div>"
      : "");

  const table = (created || visible.length)
    ? '<div class="table-frame surface"><div class="table-scroll" tabindex="0"><table class="led"><thead><tr>'
      + "<th>" + t("studio.notebook.col.status", {}) + "</th>"
      + "<th>" + t("studio.notebook.col.entry", {}) + "</th>"
      + "<th>" + t("studio.notebook.col.base", {}) + "</th>"
      + '<th class="num">' + t("studio.notebook.col.dim", {}) + "</th>"
      + '<th class="num">' + t("studio.notebook.col.steps", {}) + "</th>"
      + '<th class="num">' + t("studio.notebook.col.lr", {}) + "</th>"
      + '<th class="num">' + t("studio.notebook.col.updated", {}) + "</th>"
      + "<th>" + t("studio.notebook.col.actions", {}) + "</th>"
      + "</tr></thead><tbody>" + created + rows + "</tbody></table></div></div>"
    : empty;

  root.innerHTML = '<div class="studio" data-studio="notebook">'
    + '<div class="vhead surface"><h2>' + t("nav.notebook", {}) + "</h2>"
    + '<div class="meta">' + t("studio.notebook.meta", {
      count: count(items.length), shown: count(visible.length),
    }) + "</div>"
    + '<div class="acts">'
    + '<input class="field nb-search" data-nb-search type="search" value="' + esc(ui.nbSearch)
    + '" placeholder="' + t("studio.notebook.search", {}) + '" aria-label="'
    + t("studio.notebook.search", {}) + '">'
    + '<button class="btn" data-act="refresh">' + t("studio.refresh", {}) + "</button>"
    + '<button class="btn pri" data-act="nb-new">' + t("studio.notebook.new", {}) + "</button>"
    + "</div></div>"
    + noticeFor(t, "notebook")
    + '<div class="nb-filter surface">' + filter + "</div>"
    + table
    + (empty && (created || visible.length) ? empty : "")
    + "</div>";

  mountStudio(root, c);
}

export async function queueView(main, ctx) {
  const { root, ctx: c } = resolve(main, ctx);
  const t = (key, vars) => translate(c.t, key, vars);
  const api = client();
  root.innerHTML = '<div class="loading surface">' + t("studio.queue.loading", {}) + "</div>";

  let data = null;
  try {
    data = await api.get("/api/queue");
  } catch (error) {
    failure("queue", error);
  }

  const items = data && Array.isArray(data.items) ? data.items : [];
  const paused = Boolean(data && data.paused);
  const pausedBy = data && data.pausedBy ? String(data.pausedBy) : "";
  const current = (data && data.current) || items.find((item) => String(item.status).toLowerCase() === "running") || null;


  let scheduler;
  if (paused) {
    scheduler = '<span class="st" data-status="paused">' + t("studio.queue.paused", {}) + "</span>";
    if (pausedBy) scheduler += ' <span class="dim">' + t("studio.queue.paused.by", { who: pausedBy }) + "</span>";
  } else if (current) {
    scheduler = '<span class="st" data-status="running">'
      + t("studio.queue.running", { name: String(current.name || current.id || "") }) + "</span>";
  } else {
    scheduler = '<span class="st" data-status="idle">' + t("studio.queue.idle", {}) + "</span>";
  }

  const rows = items.map((item) => {
    const id = String(item.id || "");
    const statusKey = String(item.status || "waiting").toLowerCase();
    const step = Number(item.step) || 0;
    const steps = Number(item.steps) || 0;
    const ratio = steps > 0 ? Math.min(1, step / steps) : Math.max(0, Math.min(1, Number(item.progress) || 0));
    /* The bar and the percentage carry the progress; the absolute step count would
       push the cell past its column, so it rides along as a tooltip instead. */
    const progress = esc(pct(ratio)) + " " + esc(bar(ratio, 6));
    const stepCount = step || steps ? count(step) + (steps ? " / " + count(steps) : "") : "";
    /* The queue row has six columns and no room for a full recipe path: keep the
       file name (the directory is the same for every row) and put the whole path
       in the title so nothing is lost. */
    const configPath = String(item.config || "");
    const configName = configPath ? configPath.split("/").filter(Boolean).pop() || configPath : "";
    const elapsed = item.seconds != null ? duration(Number(item.seconds))
      : (item.started_at ? duration(Math.max(0, Math.floor(Date.now() / 1000) - Number(item.started_at))) : "—");
    /* The status stamp already reads WAITING, so this note carries a fact only when
       there is one: a start time or an error. No filler "not started" line. */
    const started = item.started_at ? t("studio.queue.started", { when: stampOf(item.started_at) }) : "";
    /* Overrides ride in the run cell as a flat K=V tail instead of a second
       table row: a continuation row reads as "the queue row wrapped", which is
       exactly the confusion this queue must not create. */
    const overridesText = item.overrides && typeof item.overrides === "object"
      ? Object.keys(item.overrides).map((key) => key + "=" + String(item.overrides[key])).join("  ")
      : "";

    const actions = [];
    if (ACTIVE_QUEUE.indexOf(statusKey) !== -1) {
      actions.push('<button class="btn mini" data-act="queue-move" data-id="' + esc(id) + '" data-arg="-1">'
        + t("studio.queue.up", {}) + "</button>");
      actions.push('<button class="btn mini" data-act="queue-move" data-id="' + esc(id) + '" data-arg="1">'
        + t("studio.queue.down", {}) + "</button>");
      actions.push('<button class="btn mini" data-act="queue-remove" data-id="' + esc(id) + '">'
        + t("studio.queue.cancel", {}) + "</button>");
    }
    if (RETRYABLE.indexOf(statusKey) !== -1) {
      actions.push('<button class="btn mini" data-act="queue-control" data-id="retry" data-arg="'
        + esc(id) + '">' + t("studio.queue.retry", {}) + "</button>");
    }
    if (item.name) {
      actions.push('<a class="btn mini" href="#/run/' + encodeURIComponent(String(item.name)) + '">'
        + t("studio.queue.open", {}) + "</a>");
    }

    const note = [item.error ? String(item.error) : "", started].filter(Boolean).join(" · ");
    return '<tr data-q-row="' + esc(id) + '">'
      + "<td><b>" + esc(item.name || item.id || "—") + "</b>"
      + (note ? '<div class="dim" title="' + esc(note) + '">' + esc(note) + "</div>" : "") + "</td>"
      + '<td class="wrap"' + (configPath ? ' title="' + esc(configPath) + '"' : "") + ">"
      + esc(configName || "—")
      + (overridesText ? '<div class="dim" title="' + esc(overridesText) + '">' + esc(overridesText) + "</div>" : "")
      + "</td>"
      + "<td>" + statusStamp(statusKey) + "</td>"
      + '<td class="q-progress"' + (stepCount ? ' title="' + esc(stepCount) + '"' : "") + ">" + progress + "</td>"
      + '<td class="elapsed">' + esc(elapsed) + "</td>"
      + '<td class="q-actions"><div class="q-acts">' + (actions.length ? actions.join(" ") : '<span class="dim">—</span>') + "</div></td>"
      + "</tr>";
  }).join("");

  // The two entry points live in the header now, so the empty state only explains
  // what the queue is and stops.
  const empty = '<div class="empty surface"><b>' + t("studio.queue.empty.title", {}) + "</b><div>"
    + t("studio.queue.empty.body", {}) + "</div></div>";

  const table = items.length
    ? '<div class="table-frame surface"><div class="table-scroll" tabindex="0"><table class="led q-table"><thead><tr>'
      + "<th>" + t("studio.queue.col.run", {}) + "</th>"
      + "<th>" + t("studio.queue.col.config", {}) + "</th>"
      + "<th>" + t("studio.queue.col.status", {}) + "</th>"
      + "<th>" + t("studio.queue.col.progress", {}) + "</th>"
      + "<th>" + t("studio.queue.col.elapsed", {}) + "</th>"
      + "<th>" + t("studio.queue.col.actions", {}) + "</th>"
      + "</tr></thead><tbody>" + rows + "</tbody></table></div></div>"
    : empty;

  root.innerHTML = '<div class="studio" data-studio="queue">'
    + '<div class="vhead surface"><h2>' + t("nav.queue", {}) + "</h2>"
    + '<div class="meta">' + t("studio.queue.meta", { count: count(items.length) }) + " · " + scheduler + "</div>"
    + '<div class="acts">'
    + '<a class="btn" href="#/notebook">' + t("nav.notebook", {}) + "</a>"
    + '<a class="btn pri" href="#/new">' + t("studio.run.new", {}) + "</a>"
    + '<button class="btn" data-act="queue-control" data-id="pause"' + (paused ? " disabled" : "") + ">"
    + t("studio.queue.pause", {}) + "</button>"
    + '<button class="btn" data-act="queue-control" data-id="resume"' + (paused ? "" : " disabled") + ">"
    + t("studio.queue.resume", {}) + "</button>"
    + '<button class="btn" data-act="queue-control" data-id="clear-done">'
    + t("studio.queue.clear", {}) + "</button>"
    + '<button class="btn" data-act="refresh">' + t("studio.refresh", {}) + "</button>"
    + "</div></div>"
    + noticeFor(t, "queue")
    + table
    + "</div>";

  mountStudio(root, c);
}

/* Delegated once per render. A previous binding on the same root is torn down first
   so repeated routes cannot stack listeners or resurrect a stale ctx. */
export function mountStudio(main, ctx) {
  const { root, ctx: c } = resolve(main, ctx);
  if (!root) return () => {};
  if (typeof root.__studioCleanup === "function") root.__studioCleanup();

  const t = (key, vars) => translate(c.t, key, vars);
  const api = client();

  const view = () => {
    const scope = root.querySelector("[data-studio]");
    return scope ? scope.dataset.studio : "";
  };

  const reload = async () => {
    if (typeof c.rerender === "function") {
      c.rerender();
      return;
    }
    const name = view();
    if (name === "profile") await profileView(root, c);
    else if (name === "notebook") await notebookView(root, c);
    else if (name === "queue") await queueView(root, c);
  };

  const run = async (work) => {
    ui.error = null;
    try {
      await work();
    } catch (error) {
      failure(view(), error);
      if (typeof c.toast === "function") c.toast(messageOf(error));
      await reload();
    }
  };

  const click = (event) => {
    const target = event.target && event.target.closest ? event.target.closest("[data-act]") : null;
    if (!target || !root.contains(target)) return;
    const act = target.dataset.act || "";
    const id = target.dataset.id || "";
    const arg = target.dataset.arg || "";

    if (act === "refresh") {
      if (view() === "profile") ui.publishRefresh = true;
      run(reload);
      return;
    }
    if (act === "name-edit") {
      ui.nameEditing = true;
      run(reload);
      return;
    }
    if (act === "name-cancel") {
      ui.nameEditing = false;
      run(reload);
      return;
    }
    if (act === "name-save") {
      run(async () => {
        const box = root.querySelector("#pf-name-input");
        const value = box ? String(box.value || "").trim() : "";
        // An empty string is the server's "back to unnamed" value, not a skip.
        await api.post("/api/profile", { displayName: value });
        ui.nameEditing = false;
        await reload();
      });
      return;
    }
    if (act === "fav-note") {
      ui.noteEditing = id;
      run(reload);
      return;
    }
    if (act === "fav-note-cancel") {
      ui.noteEditing = false;
      run(reload);
      return;
    }
    if (act === "fav-note-save") {
      run(async () => {
        const box = root.querySelector("#pf-note-input");
        const note = box ? String(box.value || "").trim() : "";
        const profile = await api.get("/api/profile");
        const pins = (profile.pins || []).map(pin => pin.run === id ? { ...pin, note } : pin);
        await api.post("/api/profile", { pins });
        ui.noteEditing = false;
        await reload();
      });
      return;
    }
    if (act === "year") {
      const hash = "#/profile" + (id ? "?year=" + encodeURIComponent(id) : "");
      if (typeof c.navigate === "function") c.navigate(hash);
      else location.hash = hash;
      return;
    }
    if (act === "pins-open") {
      run(async () => {
        const profile = await api.get("/api/profile");
        ui.pinDraft = (profile.pins || []).map(pin => ({...pin}));
        ui.pinNotes = Object.fromEntries(ui.pinDraft.map(pin => [pin.run,pin.note]));
        await reload();
      });
      return;
    }
    if (act === "pins-cancel") {
      ui.pinDraft = null;
      run(reload);
      return;
    }
    if (act === "pins-save") {
      run(async () => {
        await api.post("/api/profile", { pins: ui.pinDraft || [] });
        ui.pinDraft = null;
        await reload();
      });
      return;
    }
    if (act === "avatar-clear") {
      run(async () => {
        await api.del("/api/profile/avatar");
        await reload();
      });
      return;
    }
    if (act === "nb-filter") {
      ui.nbFilter = FILTERS.indexOf(id) === -1 ? "all" : id;
      run(reload);
      return;
    }
    if (act === "nb-new") {
      ui.nbCreating = true;
      ui.nbEditing = null;
      run(reload);
      return;
    }
    if (act === "nb-edit") {
      ui.nbEditing = id || null;
      ui.nbCreating = false;
      run(reload);
      return;
    }
    if (act === "nb-cancel") {
      ui.nbEditing = null;
      ui.nbCreating = false;
      run(reload);
      return;
    }
    if (act === "nb-save") {
      run(async () => {
        const scope = target.closest(".nb-form");
        if (!scope) return;
        const values = collect(scope);
        if (ui.nbEditing) {
          await api.post("/api/notebook/update", { id: ui.nbEditing, patch: values });
        } else {
          await api.post("/api/notebook/add", { item: values });
        }
        ui.nbEditing = null;
        ui.nbCreating = false;
        if (typeof c.toast === "function") c.toast(t("studio.notebook.saved", {}), true);
        await reload();
      });
      return;
    }
    if (act === "nb-delete") {
      run(async () => {
        await api.post("/api/notebook/remove", { id });
        ui.nbEditing = null;
        if (typeof c.toast === "function") c.toast(t("studio.notebook.deleted", {}), true);
        await reload();
      });
      return;
    }
    if (act === "nb-queue") {
      run(async () => {
        const result = await api.get("/api/notebook");
        const entry = (result && Array.isArray(result.items) ? result.items : [])
          .find((item) => String(item.id) === String(id));
        if (!entry) throw new Error("ENTRY NOT FOUND");
        if (!entry.config) throw new Error(t("studio.notebook.no.config", {}));
        await api.post("/api/queue/add", {
          draft: {
            name: entry.title,
            config: entry.config,
            overrides: entry.overrides && typeof entry.overrides === "object" ? entry.overrides : {},
            notebookId: entry.id,
          },
        });
        await api.post("/api/notebook/update", { id: entry.id, patch: { status: "queued" } });
        if (typeof c.toast === "function") c.toast(t("studio.notebook.queued", { title: entry.title || "" }), true);
        await reload();
      });
      return;
    }
    if (act === "queue-control") {
      run(async () => {
        const body = { action: id };
        if (id === "retry" && arg) body.id = arg;
        await api.post("/api/queue/control", body);
        await reload();
      });
      return;
    }
    if (act === "queue-move") {
      run(async () => {
        await api.post("/api/queue/move", { id, direction: Number(arg) < 0 ? "up" : "down" });
        await reload();
      });
      return;
    }
    if (act === "queue-remove") {
      run(async () => {
        await api.post("/api/queue/remove", { id });
        await reload();
      });
    }
  };

  const change = (event) => {
    const choice = event.target.closest?.("[data-pin-choice]");
    if (choice && ui.pinDraft !== null) {
      const error = root.querySelector("[data-pin-error]");
      if (choice.checked && ui.pinDraft.length >= 6) {
        choice.checked = false;
        error.hidden = false;
        error.textContent = t("studio.profile.pins.limit", {});
        return;
      }
      error.hidden = true;
      if (choice.checked) ui.pinDraft.push({run:choice.value,note:ui.pinNotes[choice.value] || ""});
      else ui.pinDraft = ui.pinDraft.filter(pin => pin.run !== choice.value);
      root.querySelector("#pf-pin-count").textContent = t("studio.profile.pins.count", {count:ui.pinDraft.length});
      return;
    }
    const input = event.target && event.target.closest ? event.target.closest("[data-avatar-file]") : null;
    if (!input || !input.files || !input.files.length) return;
    run(async () => {
      const file = input.files[0];
      if (file.size > 5 * 1024 * 1024) throw new Error(t("studio.profile.avatar.size", {}));
      if (!/^image\/(png|jpeg|webp)$/i.test(String(file.type || ""))) {
        throw new Error(t("studio.profile.avatar.type", {}));
      }
      await api.upload("/api/profile/avatar", file, file.type || "application/octet-stream");
      await reload();
    });
  };

  const input = (event) => {
    const search = event.target.closest?.("[data-pin-search]");
    if (search) {
      const needle = search.value.trim().toLocaleLowerCase();
      root.querySelectorAll("[data-pin-row]").forEach(row => { row.hidden = !row.textContent.toLocaleLowerCase().includes(needle); });
      return;
    }
    const field = event.target && event.target.closest ? event.target.closest("[data-nb-search]") : null;
    if (!field) return;
    ui.nbSearch = String(field.value || "");
    applySearch(root);
  };

  // The portrait is a label, not a button, so Enter/Space do nothing by default.
  // Forward both to the hidden file input; the browser only does that for real
  // buttons, and the picker is the one thing this control exists for.
  const keydown = (event) => {
    const pick = event.target && event.target.closest ? event.target.closest(".pf-face-pick") : null;
    if (!pick || !root.contains(pick)) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    const file = root.querySelector("#pf-file");
    if (file) file.click();
  };

  root.addEventListener("click", click);
  root.addEventListener("change", change);
  root.addEventListener("input", input);
  root.addEventListener("keydown", keydown);
  applySearch(root);

  const cleanup = () => {
    root.removeEventListener("click", click);
    root.removeEventListener("change", change);
    root.removeEventListener("input", input);
    root.removeEventListener("keydown", keydown);
    root.__studioCleanup = null;
  };
  root.__studioCleanup = cleanup;
  return cleanup;
}

function applySearch(root) {
  const needle = ui.nbSearch.trim().toLowerCase();
  root.querySelectorAll("[data-nb-row]").forEach((row) => {
    const haystack = String(row.dataset.text || "");
    row.hidden = needle !== "" && haystack.indexOf(needle) === -1;
  });
}
