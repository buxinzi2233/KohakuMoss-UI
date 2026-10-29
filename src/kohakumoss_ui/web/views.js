/* Views: each function renders one page into <main> from the real API. */
import * as api from "./api.js";
import {
  bar, bars, bytes, clock, duration, esc, logLine, metric, num, rate, spark, when,
} from "./format.js";
import { collectOverrides, initialValues, mountKnobForm } from "./knobform.js";
import { gbcDraftView } from "./gbc-draft.js";

function loading(label) {
  return `<div class="loading surface">${esc(label)}…</div>`;
}

function emptyState(title, lines = []) {
  return `<div class="empty surface"><b>${esc(title)}</b>${lines.map((line) => `<div>${esc(line)}</div>`).join("")}</div>`;
}

/** One status word, translated; the class keeps the stamp styling. */
function statusText(ctx, status) {
  return ctx.t(`runs.filter.${String(status || "").toLowerCase()}`);
}

function statusStamp(ctx, status) {
  const cls = ["running", "stopped", "done", "unknown"].includes(status) ? status.slice(0, 4) : "unknown";
  return `<span class="st ${cls}">${esc(statusText(ctx, status) || String(status || "").toUpperCase())}</span>`;
}

/* The closed base-select sizes itself to the selected option, so any longer
   label is clipped. The themed picker is exactly as wide as the control
   (anchor-size), so the control also has to cover the picker frame, the option
   frame, and the checkmark gutter every row reserves. Measure each label with
   the option's computed font, add those edges, and pin the sum inline; app.css
   caps it with max-width:100%. Re-run once the web fonts settle, since the first
   pass may measure a fallback face. */
function fitSelectToLongestOption(select) {
  if (!select || !select.options.length) return;
  const apply = () => {
    if (!select.isConnected || !select.options.length) return;
    const px = (value) => parseFloat(value) || 0;
    const host = select.parentElement;
    select.style.width = "";
    select.style.inlineSize = "";
    const hostStyle = getComputedStyle(host);
    const available = host.clientWidth - px(hostStyle.paddingLeft) - px(hostStyle.paddingRight);
    const option = select.options[0];
    const optionStyle = getComputedStyle(option);
    const pickerStyle = getComputedStyle(select, "::picker(select)");
    const probe = document.createElement("span");
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre;pointer-events:none";
    for (const prop of ["fontFamily", "fontSize", "fontWeight", "fontStyle", "fontVariant", "letterSpacing", "wordSpacing"]) {
      probe.style[prop] = optionStyle[prop];
    }
    host.appendChild(probe);
    const textWidth = (text) => {
      probe.textContent = text;
      return probe.getBoundingClientRect().width;
    };
    let longest = 0;
    for (const item of select.options) longest = Math.max(longest, textWidth(item.textContent));
    const checkmark = parseFloat(getComputedStyle(option, "::checkmark").width) || textWidth("\u2713");
    probe.remove();
    const frame = [
      pickerStyle.borderLeftWidth, pickerStyle.borderRightWidth,
      pickerStyle.paddingLeft, pickerStyle.paddingRight,
      optionStyle.borderLeftWidth, optionStyle.borderRightWidth,
      optionStyle.paddingLeft, optionStyle.paddingRight,
      optionStyle.columnGap,
    ].reduce((sum, edge) => sum + px(edge), checkmark);
    const size = Math.min(Math.ceil(longest + frame), Math.floor(available));
    if (size > 0) {
      select.style.width = `${size}px`;
      select.style.inlineSize = `${size}px`;
    }
  };
  apply();
  if (document.fonts?.ready) document.fonts.ready.then(apply);
}

function alertBlock(ctx, preflight, doctor) {
  if (!doctor && !preflight) return `<div class="alert calm surface"><div class="h">${esc(ctx.t("doctor.preflight.checking"))}</div><div class="fix">${esc(ctx.t("doctor.preflight.bg"))}</div></div>`;
  if (doctor?.error) return `<div class="alert calm surface"><div class="h">${esc(ctx.t("doctor.preflight.unavailable"))}</div><div class="fix">${esc(doctor.error)}</div></div>`;
  const blocking = preflight?.blocking || [];
  const warnings = preflight?.warnings || [];
  if (blocking.length) {
    return `<div class="alert surface"><div class="h">${esc(ctx.t("doctor.preflight.blocking", { n: blocking.length }))}</div>${blocking.map((item) => `<div>${esc(item.what)} &nbsp; <span class="fix">→ ${esc(item.fix)}</span></div>`).join("")}</div>`;
  }
  if (warnings.length) {
    return `<div class="alert calm surface"><div class="h">${esc(ctx.t("doctor.preflight.warning", { n: warnings.length }))}</div>${warnings.map((item) => `<div>${esc(item.what)} &nbsp; <span class="fix">→ ${esc(item.fix)}</span></div>`).join("")}</div>`;
  }
  return `<div class="alert calm surface"><div class="h">${esc(ctx.t("doctor.preflight.ok"))}</div><div class="fix">${esc(ctx.t("doctor.preflight.okline"))}</div></div>`;
}

function configSlug(path) {
  const base = String(path || "").split("/").pop() || "";
  return base.replace(/\.[a-z0-9]+$/i, "");
}

function pickConfig(slug, choices, fallback) {
  if (!slug) return fallback;
  const wanted = String(slug).toLowerCase();
  const exact = choices.find((item) => configSlug(item.path).toLowerCase() === wanted);
  if (exact) return exact.path;
  const loose = choices.find((item) => item.path.toLowerCase().includes(wanted));
  return loose ? loose.path : fallback;
}

const KNOB_DOCS_URL = "/assets/data/knob-docs.json";

/** Parameter explanations live in a static table under web/data, so the wording
 *  can be revised without editing a module. Loaded once per tab. */
async function loadKnobDocs(state) {
  if (state.knobDocs !== undefined) return state.knobDocs;
  state.knobDocs = await api.get(KNOB_DOCS_URL).catch(() => null);
  return state.knobDocs;
}

/** #/new?from=<notebook id> is the NOTEBOOK hand-off: the note outranks the
 *  saved config for the four knobs it actually models, and nothing else is
 *  invented on its behalf. */
async function loadNotebookItem(id) {
  try {
    const data = await api.get("/api/notebook");
    return (data.items || []).find((item) => item.id === id) || null;
  } catch {
    return null;
  }
}

const PREFILL_FIELDS = { name: "RUN_NAME", steps: "STEPS", lr: "LR", dim: "DIM" };

function applyPrefill(values, item) {
  Object.entries(PREFILL_FIELDS).forEach(([field, knob]) => {
    const value = item[field];
    if (value === undefined || value === null || value === "") return;
    values[knob] = value;
  });
}

function runRow(ctx, run) {
  const resume = run.managed?.config
    ? `<a class="btn" href="#/new?${new URLSearchParams({ resume: "latest" }).toString()}">${esc(ctx.t("runs.resume"))}</a>`
    : "";
  const stop = run.status === "running"
    ? `<button class="btn dngr" data-stop="${esc(run.name)}">${esc(ctx.t("runs.stop"))}</button>`
    : resume;
  const href = `#/run/${encodeURIComponent(run.name)}`;
  return `<tr>
    <td>${statusStamp(ctx, run.status)}</td>
    <td><a href="${href}"><b>${esc(run.name)}</b></a></td>
    <td class="num">${run.steps ? `${run.step}/${run.steps}` : run.step || "—"}</td>
    <td class="num">${run.loss == null ? "—" : num(run.loss)}</td>
    <td class="num">${run.vram == null ? "—" : `${metric(run.vram)} G`}</td>
    <td>${run.started_at ? clock(run.started_at) : "—"}</td>
    <td><a class="btn" href="${href}">${esc(ctx.t("runs.open"))}</a> ${stop}</td>
  </tr>`;
}

function sortRuns(runs, mode) {
  const list = [...runs];
  if (mode === "name") return list.sort((a, b) => a.name.localeCompare(b.name));
  if (mode === "step") return list.sort((a, b) => (b.step || 0) - (a.step || 0));
  return list.sort((a, b) => (b.started_at || 0) - (a.started_at || 0) || (b.step || 0) - (a.step || 0));
}

function filterRuns(runs, filter) {
  const needle = String(filter.q || "").trim().toLowerCase();
  return runs.filter((run) => {
    if (filter.status !== "all" && run.status !== filter.status) return false;
    if (!needle) return true;
    return `${run.name} ${run.config?.RUN_NAME || ""} ${run.config?.ALGO || ""}`.toLowerCase().includes(needle);
  });
}

export async function runsView(main, ctx) {
  const state = ctx.state;
  main.innerHTML = loading(ctx.t("runs.loading"));
  let data;
  try {
    data = await api.get("/api/runs");
  } catch (error) {
    main.innerHTML = emptyState(ctx.t("runs.unavailable"), [String(error.message || error)]);
    return;
  }
  state.runs = data.runs;
  ctx.updateRunner(data.runs);

  if (!state.doctor && !state.doctorLoading) {
    state.doctorLoading = true;
    api.get("/api/doctor").then((doctor) => {
      state.doctor = doctor;
      state.doctorLoading = false;
      if (location.hash.startsWith("#/runs")) ctx.rerender();
    }).catch((error) => {
      state.doctor = { error: error.message };
      state.doctorLoading = false;
    });
  }

  state.runsFilter = state.runsFilter || { q: "", status: "all", sort: "recent" };
  const filter = state.runsFilter;
  const counts = { all: data.runs.length, running: 0, done: 0, stopped: 0 };
  data.runs.forEach((run) => { if (run.status in counts) counts[run.status] += 1; });

  const statusButtons = ["all", "running", "done", "stopped"].map((status) => `<button class="btn ${filter.status === status ? "on" : ""}" type="button" data-run-status="${status}" aria-pressed="${filter.status === status}">${esc(ctx.t(`runs.filter.${status}`))} <span>${counts[status]}</span></button>`).join("");
  const sortOptions = [["recent", "runs.sort.recent"], ["name", "runs.sort.name"], ["step", "runs.sort.step"]]
    .map(([value, key]) => `<option value="${value}" ${filter.sort === value ? "selected" : ""}>${esc(ctx.t(key))}</option>`).join("");

  main.innerHTML = `
    <div class="vhead surface">
      <h2>${esc(ctx.t("runs.title"))}</h2>
      <div class="meta">${esc(ctx.t("runs.meta", { count: data.runs.length, running: counts.running, dir: data.runs_dir }))}</div>
      <div class="acts"><select class="field" id="runsort" aria-label="${esc(ctx.t("runs.sort.label"))}"><button type="button"><selectedcontent></selectedcontent></button>${sortOptions}</select><button class="btn" data-refresh-runs="1">${esc(ctx.t("runs.refresh"))}</button><a class="btn pri" href="#/new">${esc(ctx.t("runs.new"))}</a></div>
    </div>
    ${alertBlock(ctx, state.doctor?.preflight, state.doctor)}
    <div class="knobbar runs-bar surface">
      <div class="knobsearch">
        <label for="runsearch">${esc(ctx.t("runs.search.label"))}</label>
        <input class="field" id="runsearch" type="search" value="${esc(filter.q)}" placeholder="${esc(ctx.t("runs.search.ph"))}" autocomplete="off" spellcheck="false">
      </div>
      <div class="knoblevels" role="group" aria-label="${esc(ctx.t("runs.col.status"))}">${statusButtons}</div>
    </div>
    <div id="runs-body"></div>
    <div class="okline surface">${esc(ctx.t("runs.note"))}</div>`;

  const body = main.querySelector("#runs-body");
  const summary = main.querySelector(".runs-bar");
  const renderBody = () => {
    const shown = sortRuns(filterRuns(data.runs, filter), filter.sort);
    if (!data.runs.length) {
      body.innerHTML = emptyState(ctx.t("runs.empty.title"), [ctx.t("runs.empty.line1"), ctx.t("runs.empty.line2")]);
    } else if (!shown.length) {
      body.innerHTML = emptyState(ctx.t("runs.noMatch"), []);
    } else {
      body.innerHTML = `<div class="table-frame surface"><div class="table-scroll" tabindex="0"><table class="led"><thead><tr><th>${esc(ctx.t("runs.col.status"))}</th><th>${esc(ctx.t("runs.col.run"))}</th><th class="num">${esc(ctx.t("runs.col.step"))}</th><th class="num">${esc(ctx.t("runs.col.loss"))}</th><th class="num">${esc(ctx.t("runs.col.vram"))}</th><th>${esc(ctx.t("runs.col.started"))}</th><th></th></tr></thead><tbody>${shown.map((run) => runRow(ctx, run)).join("")}</tbody></table></div></div>
        <div class="knobsummary surface">${esc(ctx.t("runs.matches", { n: shown.length, total: data.runs.length }))}</div>`;
    }
    bindStop();
  };

  const bindStop = () => {
    body.querySelectorAll("[data-stop]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await api.post("/api/run/stop", { name: button.dataset.stop });
          ctx.toast(result.stopped ? ctx.t("runs.stopSent", { name: button.dataset.stop }) : (result.reason || ctx.t("runs.stopFailed")), result.stopped);
          setTimeout(() => runsView(main, ctx), 800);
        } catch (error) {
          ctx.toast(error.message);
          button.disabled = false;
        }
      });
    });
  };

  renderBody();

  const search = main.querySelector("#runsearch");
  search.addEventListener("input", () => {
    filter.q = search.value;
    renderBody();
  });
  main.querySelectorAll("[data-run-status]").forEach((button) => {
    button.addEventListener("click", () => {
      filter.status = button.dataset.runStatus;
      main.querySelectorAll("[data-run-status]").forEach((other) => {
        other.classList.toggle("on", other === button);
        other.setAttribute("aria-pressed", String(other === button));
      });
      renderBody();
    });
  });
  main.querySelector("#runsort")?.addEventListener("change", (event) => {
    filter.sort = event.target.value;
    renderBody();
  });
  main.querySelector("[data-refresh-runs]")?.addEventListener("click", () => runsView(main, ctx));
  if (summary) summary.dataset.bound = "1";
}

export async function newView(main, ctx, slug = "") {
  const state = ctx.state;
  const query = ctx.query();
  if (query.has("draftId")) return gbcDraftView(main, ctx);
  main.innerHTML = loading(ctx.t("new.loading"));
  try {
    const [knobs, configs] = await Promise.all([
      state.knobs ? Promise.resolve(state.knobs) : api.get("/api/knobs"),
      state.configs ? Promise.resolve(state.configs) : api.get("/api/configs"),
    ]);
    state.knobs = knobs;
    state.configs = configs;
    if (!state.doctor && !state.doctorLoading) {
      state.doctorLoading = true;
      api.get("/api/doctor").then((doctor) => { state.doctor = doctor; state.doctorLoading = false; }).catch(() => {});
    }

    const choices = [...configs.recipes, ...configs.user];
    if (!choices.length) {
      main.innerHTML = emptyState(ctx.t("new.empty.title"), [ctx.t("new.empty.line")]);
      return;
    }
    const fallback = query.get("config") || state.selectedConfig || choices[0].path;
    const selected = pickConfig(slug, choices, fallback);
    state.selectedConfig = selected;
    let values = {};
    try {
      const loaded = await api.get("/api/config/load", { path: selected });
      values = loaded.values || {};
    } catch (error) {
      ctx.toast(ctx.t("new.loadFailed", { error: error.message }));
    }
    if (query.get("resume")) values.RESUME = query.get("resume");
    const fromId = query.get("from");
    const prefilled = fromId ? await loadNotebookItem(fromId) : null;
    if (prefilled) applyPrefill(values, prefilled);
    const docs = await loadKnobDocs(state);

    const initial = initialValues(knobs.sections, values);

    const options = choices.map((item) => `<option value="${esc(item.path)}" ${item.path === selected ? "selected" : ""}>${esc(item.path)}</option>`).join("");
    const command = () => {
      const parts = [`kogine run scripts/train/moss.py --config ${selected}`];
      Object.entries(collectOverrides(main, initial)).forEach(([name, value]) => {
        parts.push(`--set ${name}=${value}`);
      });
      const preview = main.querySelector("#preview");
      if (preview) preview.textContent = parts.join(" ");
    };

    main.innerHTML = `
      <div class="vhead vhead-sticky">
        <h2>${esc(ctx.t("new.title"))}</h2>
        <div class="meta">${esc(ctx.t("new.meta", { count: knobs.count }))}</div>
        <div class="acts"><a class="btn" href="#/runs">${esc(ctx.t("new.cancel"))}</a><button class="btn pri" id="start" aria-describedby="start-note">${esc(ctx.t("new.start"))}</button><span class="start-note" id="start-note" role="status" hidden></span></div>
      </div>
      ${alertBlock(ctx, state.doctor?.preflight, state.doctor)}
      <div class="readystrip surface" id="readystrip">
        <span class="readyitem"><span class="readylabel">${esc(ctx.t("new.ready.config"))}</span><b id="config-state">${esc(ctx.t("new.state.unchecked"))}</b></span>
        <span class="readyitem"><span class="readylabel">${esc(ctx.t("new.ready.overrides"))}</span><b id="override-count">0</b></span>
        <span class="readyitem"><span class="readylabel">${esc(ctx.t("new.ready.name"))}</span><b id="name-state">${esc(ctx.t("new.name.requiredState"))}</b></span>
        ${prefilled ? `<span class="readyitem" id="prefill-state"><span class="readylabel">${esc(ctx.t("new.from.label"))}</span><b>${esc(prefilled.title || prefilled.id)}</b></span>` : ""}
        <span class="readyscope">${esc(ctx.t("new.ready.scope"))}</span>
      </div>
      <div class="form newrun-head surface">
        <div class="lab">${esc(ctx.t("new.config.label"))}</div><div class="val"><select class="field" id="cfg"><button type="button"><selectedcontent></selectedcontent></button>${options}</select><span class="hint">${esc(ctx.t("new.config.hint"))}</span></div>
        <div class="lab">${esc(ctx.t("new.name.label"))}</div><div class="val"><input class="field" id="run-name" data-knob="RUN_NAME" value="${esc(initial.RUN_NAME || "")}" spellcheck="false" required aria-describedby="run-name-hint run-name-error"><span class="hint" id="run-name-hint">${esc(ctx.t("new.name.hint"))}</span><span class="hint run-name-error" id="run-name-error" hidden>${esc(ctx.t("new.name.required"))}</span></div>
      </div>
      <div id="knobform"></div>
      <div class="footcol">
        <div class="box" style="margin-top:12px"><h3>${esc(ctx.t("new.command"))} <b>${esc(ctx.t("new.command.sub"))}</b></h3><pre class="code" id="preview"></pre></div>
        <div class="okline surface" id="checkline" role="status" aria-live="polite">${esc(ctx.t("new.ready.scope"))}</div>
      </div>`;

    fitSelectToLongestOption(main.querySelector("#cfg"));
    const nameInput = main.querySelector("#run-name");
    const startButton = main.querySelector("#start");
    const checkline = main.querySelector("#checkline");
    const configState = main.querySelector("#config-state");
    const overrideCount = main.querySelector("#override-count");
    const nameState = main.querySelector("#name-state");
    const nameError = main.querySelector("#run-name-error");
    const startNote = main.querySelector("#start-note");
    let checkState = "unchecked";

    const stateKey = () => {
      if (checkState === "checking") return "checking";
      if (checkState === "invalid") return "invalid";
      if (checkState === "error") return "failed";
      if (checkState === "ok") return "ok";
      return "unchecked";
    };

    const updateReadiness = () => {
      const name = nameInput.value.trim();
      const count = Object.keys(collectOverrides(main, initial)).length;
      const invalid = checkState === "invalid";
      overrideCount.textContent = String(count);
      nameState.textContent = name ? ctx.t("new.name.set") : ctx.t("new.name.requiredState");
      nameState.classList.toggle("red", !name);
      nameInput.setAttribute("aria-invalid", String(!name));
      nameError.hidden = Boolean(name);
      configState.textContent = ctx.t(`new.state.${stateKey()}`);
      configState.classList.toggle("red", invalid || checkState === "error");
      startButton.disabled = !name || invalid;
      startNote.hidden = Boolean(name) && !invalid;
      startNote.textContent = !name
        ? ctx.t("new.startNote.name")
        : invalid ? ctx.t("new.startNote.invalid") : "";
    };

    const preview = () => {
      command();
      updateReadiness();
    };
    nameInput.addEventListener("input", preview);
    mountKnobForm(main.querySelector("#knobform"), {
      sections: knobs.sections,
      initial,
      script: knobs.script,
      onChange: preview,
      t: (key, vars) => ctx.t(key, vars),
      docs,
    });
    main.querySelector("#cfg").addEventListener("change", (event) => {
      const next = event.target.value;
      const resume = query.get("resume");
      const suffix = resume ? `?resume=${encodeURIComponent(resume)}` : "";
      ctx.navigate(`#/new/${encodeURIComponent(configSlug(next))}${suffix}`);
    });
    preview();

    const checkButton = document.createElement("button");
    checkButton.className = "btn";
    checkButton.textContent = ctx.t("new.check");
    checkButton.addEventListener("click", async () => {
      checkButton.disabled = true;
      checkState = "checking";
      updateReadiness();
      checkline.textContent = ctx.t("new.check.running");
      try {
        const result = await api.get("/api/config/check", { path: selected });
        if (result.ok) {
          checkState = "ok";
          checkline.textContent = `${ctx.t("new.check.ok")} ${ctx.t("new.check.scope")}`;
        } else {
          checkState = "invalid";
          checkline.innerHTML = `<span class="red">${esc(ctx.t("new.check.invalid"))}</span> ${esc(ctx.t("new.check.scope"))}<details class="rawcheck"><summary>${esc(ctx.t("new.check.raw"))}</summary><pre class="code">${esc(result.output || "")}</pre></details>`;
        }
      } catch (error) {
        checkState = "error";
        checkline.textContent = `${ctx.t("new.check.failed")} ${error.message} ${ctx.t("new.check.scope")}`;
      } finally {
        checkButton.disabled = false;
        startButton.disabled = !nameInput.value.trim() || checkState === "invalid";
        updateReadiness();
      }
    });
    main.querySelector(".vhead .acts").insertBefore(checkButton, main.querySelector("#start"));

    main.querySelector("#start").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      const name = nameInput.value.trim();
      if (!name) {
        nameInput.focus();
        updateReadiness();
        return;
      }
      if (checkState === "invalid") {
        checkline.focus?.();
        updateReadiness();
        return;
      }
      const overrides = collectOverrides(main, initial);
      if (values.RESUME && !overrides.RESUME) overrides.RESUME = String(values.RESUME);
      button.disabled = true;
      try {
        const result = await api.post("/api/run/start", { config: selected, name, overrides });
        ctx.toast(ctx.t("new.started", { name: result.name, pid: result.pid }), true);
        ctx.navigate(`#/run/${encodeURIComponent(result.name)}`);
      } catch (error) {
        ctx.toast(error.message);
        updateReadiness();
      }
    });
  } catch (error) {
    main.innerHTML = emptyState(ctx.t("new.unavailable"), [String(error.message || error)]);
  }
}

export async function datasetsView(main, ctx) {
  const state = ctx.state;
  main.innerHTML = loading(ctx.t("datasets.loading"));
  try {
    const [data, configChoices] = await Promise.all([
      api.get("/api/datasets"),
      state.configs ? Promise.resolve(state.configs) : api.get("/api/configs"),
    ]);
    state.configs = configChoices;
    const selectable = new Set([...configChoices.recipes, ...configChoices.user].map((item) => item.path));
    const rows = data.datasets.map((item) => {
      const cache = item.cache?.present
        ? `${bar(1, 10)} <span class="dim">${item.cache.files} files · ${bytes(item.cache.bytes)}</span>`
        : `${bar(0, 10)} <span class="red">${esc(ctx.t("datasets.missing"))}</span>`;
      const configs = [...(item.configs || [])].sort((a, b) => Number(!selectable.has(a)) - Number(!selectable.has(b)));
      const configLink = (config) => selectable.has(config)
        ? `<a class="cfg-link" href="#/new/${encodeURIComponent(configSlug(config))}" title="${esc(config)}">${esc(config.split("/").pop())}</a>`
        : `<span class="cfg-base" title="${esc(config)} · base layer, not directly runnable">${esc(config.split("/").pop())}</span>`;
      const firstLinks = configs.slice(0, 2).map(configLink).join("<br>");
      const moreConfigs = configs.length > 2
        ? `<details class="cfgmore"><summary>${esc(ctx.t("datasets.more", { n: configs.length - 2 }))}</summary>${configs.slice(2).map(configLink).join("<br>")}</details>`
        : "";
      const configLinks = `${firstLinks}${moreConfigs}`;
      const first = configs.find((config) => selectable.has(config));
      const use = first
        ? `<a class="btn mini" href="#/new/${encodeURIComponent(configSlug(first))}" aria-label="${esc(ctx.t("datasets.use"))} ${esc(first)}">${esc(ctx.t("datasets.use"))}</a>`
        : `<span class="dim" title="Only recipe/user configs are runnable">—</span>`;
      return `<tr>
        <td>${esc(item.source)}${item.template ? ` <span class="dim">${esc(ctx.t("datasets.template"))}</span>` : ""}</td>
        <td class="wrap">${esc(item.path)}${item.exists ? "" : ` <span class="red">${esc(ctx.t("datasets.notFound"))}</span>`}</td>
        <td>${esc(item.images ?? "—")}${item.truncated ? "+" : ""}</td>
        <td>${esc(item.captions ?? "—")}${item.truncated ? "+" : ""}</td>
        <td>${cache}</td>
        <td class="wrap">${configLinks || `<span class="dim">—</span>`}</td>
        <td>${use}</td>
      </tr>`;
    }).join("");
    const failures = (data.failures || []).map((item) => `<div>${esc(item.config)} · <span class="red">${esc(item.error)}</span></div>`).join("");
    main.innerHTML = `
      <div class="vhead surface">
        <h2>${esc(ctx.t("datasets.title"))}</h2>
        <div class="meta">${esc(ctx.t("datasets.meta", { sources: data.datasets.length, configs: data.scanned_configs.length }))}</div>
        <div class="acts"><a class="btn" href="#/datasets/gbc">${esc(ctx.t("gbc.title"))}</a><button class="btn" id="rescan">${esc(ctx.t("datasets.rescan"))}</button><a class="btn pri" href="#/new">${esc(ctx.t("datasets.new"))}</a></div>
      </div>
      ${data.datasets.length ? `<div class="table-frame surface"><div class="table-scroll" tabindex="0"><table class="led"><thead><tr><th>${esc(ctx.t("datasets.col.source"))}</th><th>${esc(ctx.t("datasets.col.path"))}</th><th>${esc(ctx.t("datasets.col.images"))}</th><th>${esc(ctx.t("datasets.col.captions"))}</th><th>${esc(ctx.t("datasets.col.cache"))}</th><th>${esc(ctx.t("datasets.col.declared"))}</th><th>${esc(ctx.t("datasets.col.use"))}</th></tr></thead><tbody>${rows}</tbody></table></div></div><div class="scroll-hint surface">${esc(ctx.t("datasets.scroll"))}</div>` : emptyState(ctx.t("datasets.empty.title"), [ctx.t("datasets.empty.line")])}
      <div class="okline surface">${esc(ctx.t("datasets.note"))}</div>
      ${failures ? `<details class="adv surface"><summary>${esc(ctx.t("datasets.failures", { n: data.failures.length }))}</summary><div class="sec">${failures}</div></details>` : ""}`;
    main.querySelector("#rescan")?.addEventListener("click", async () => {
      main.innerHTML = loading(ctx.t("datasets.rescan"));
      try {
        const fresh = await api.get("/api/datasets", { refresh: 1 });
        ctx.state.datasets = fresh;
        await datasetsView(main, ctx);
        ctx.toast(ctx.t("toast.rescan"), true);
      } catch (error) {
        ctx.toast(error.message);
      }
    });
  } catch (error) {
    main.innerHTML = emptyState(ctx.t("datasets.unavailable"), [String(error.message || error)]);
  }
}

export async function artifactsView(main, ctx) {
  const state = ctx.state;
  main.innerHTML = loading(ctx.t("artifacts.loading"));
  try {
    const data = state.runs.length ? { runs: state.runs } : await api.get("/api/runs");
    state.runs = data.runs;
    ctx.updateRunner(data.runs);
    if (!data.runs.length) {
      main.innerHTML = `<div class="vhead surface"><h2>${esc(ctx.t("artifacts.title"))}</h2><div class="meta">${esc(ctx.t("artifacts.noRuns"))}</div></div>${emptyState(ctx.t("artifacts.empty.title"), [ctx.t("artifacts.empty.line")])}`;
      return;
    }
    const query = ctx.query();
    const name = query.get("run") || state.selectedRun || data.runs[0].name;
    state.selectedRun = name;
    const detail = await api.get("/api/run", { name });
    const options = data.runs.map((run) => `<option value="${esc(run.name)}" ${run.name === name ? "selected" : ""}>${esc(run.name)}</option>`).join("");

    const checkpoints = detail.checkpoints.map((item) => `<tr>
      <td>${esc(item.name)}</td><td class="num">${bytes(item.size)}</td><td>${when(item.mtime)}</td>
      <td class="wrap dim">${esc((item.exports || []).join(", ") || "—")}</td>
    </tr>`).join("");

    const shots = [];
    detail.samples.forEach((sample) => {
      sample.files.filter((file) => /\.(png|jpe?g|webp)$/i.test(file.name)).slice(0, 8).forEach((file) => {
        shots.push(`<div class="shot"><img loading="lazy" src="${api.fileUrl(name, `samples/${sample.name}/${file.name}`)}" alt="${esc(file.name)}"><div class="cap"><span>${esc(sample.name)}</span><span>${esc(file.name)}</span></div></div>`);
      });
    });
    if (detail.grid) {
      shots.push(`<div class="shot"><img loading="lazy" src="${api.fileUrl(name, "samples/grid.png")}" alt="grid"><div class="cap"><span>grid.png</span><span>${esc(ctx.t("artifacts.grid"))}</span></div></div>`);
    }

    main.innerHTML = `
      <div class="vhead surface">
        <h2>${esc(ctx.t("artifacts.title"))}</h2>
        <div class="meta">${esc(ctx.t("artifacts.meta", { name, checkpoints: detail.checkpoints.length, samples: detail.samples.length }))}</div>
        <div class="acts"><select class="field" id="pickrun"><button type="button"><selectedcontent></selectedcontent></button>${options}</select><a class="btn" href="#/run/${encodeURIComponent(name)}">${esc(ctx.t("artifacts.open"))}</a></div>
      </div>
      ${checkpoints ? `<div class="table-frame surface"><div class="table-scroll" tabindex="0"><table class="led"><thead><tr><th>${esc(ctx.t("run.col.step"))}</th><th class="num">${esc(ctx.t("run.col.size"))}</th><th>${esc(ctx.t("run.col.saved"))}</th><th>${esc(ctx.t("run.col.export"))}</th></tr></thead><tbody>${checkpoints}</tbody></table></div></div>` : emptyState(ctx.t("artifacts.noCheckpoint"), [ctx.t("artifacts.noCheckpoint.line")])}
      <div class="box" style="margin-top:14px"><h3>${esc(ctx.t("artifacts.samples"))} <b>${esc(ctx.t("artifacts.samples.count", { n: detail.samples.length }))}</b></h3>${shots.length ? `<div class="gallery">${shots.join("")}</div>` : `<div class="dim">${esc(ctx.t("run.noSamples"))} ${esc(ctx.t("run.noSamples.hint"))}</div>`}</div>
      ${detail.exports.length ? `<div class="box" style="margin-top:14px"><h3>${esc(ctx.t("artifacts.exports"))}</h3><div class="code">${esc(detail.exports.join("\n"))}</div></div>` : ""}`;

    main.querySelector("#pickrun")?.addEventListener("change", (event) => {
      ctx.navigate(`#/artifacts?run=${encodeURIComponent(event.target.value)}`);
    });
  } catch (error) {
    main.innerHTML = emptyState(ctx.t("artifacts.unavailable"), [String(error.message || error)]);
  }
}

export async function doctorView(main, ctx) {
  main.innerHTML = loading(ctx.t("doctor.loading"));
  try {
    const report = await api.get("/api/doctor");
    ctx.state.doctor = report;
    const devices = (report.devices || []).map((device) => `<tr><td class="num">${device.index}</td><td>${esc(device.name)}</td><td>${esc(device.arch)}</td><td class="num">${metric(device.memory_gib, 1)} GiB</td><td>${device.bf16 ? "YES" : "NO"}</td><td>${device.fp8 ? "YES" : "NO"}</td></tr>`).join("");
    const packages = Object.entries(report.packages || {}).map(([name, version]) => `<tr><td>${esc(name)}</td><td class="num">${esc(version || "—")}</td></tr>`).join("");
    const missing = (report.missing || []).map((item) => `<div>${esc(item.what)} <span class="dim">→ ${esc(item.fix)}</span></div>`).join("");
    const pre = report.preflight || {};
    main.innerHTML = `
      <div class="vhead surface">
        <h2>${esc(ctx.t("doctor.title"))}</h2>
        <div class="meta">${esc(ctx.t("doctor.meta", { os: report.os || "?", release: report.os_release || "", python: report.python || "?", cuda: report.torch_cuda || "?" }))}</div>
        <div class="acts"><button class="btn" id="redo">${esc(ctx.t("doctor.rerun"))}</button></div>
      </div>
      ${alertBlock(ctx, pre, report)}
      <div class="grid2">
        <div class="box"><h3>${esc(ctx.t("doctor.devices"))}</h3><table class="led"><thead><tr><th>${esc(ctx.t("doctor.col.idx"))}</th><th>${esc(ctx.t("doctor.col.name"))}</th><th>${esc(ctx.t("doctor.col.arch"))}</th><th class="num">${esc(ctx.t("doctor.col.vram"))}</th><th>BF16</th><th>FP8</th></tr></thead><tbody>${devices || `<tr><td colspan="6" class="dim">${esc(ctx.t("doctor.noDevice"))}</td></tr>`}</tbody></table></div>
        <div class="box"><h3>${esc(ctx.t("doctor.packages"))}</h3><table class="led"><tbody>${packages}</tbody></table></div>
      </div>
      <div class="box" style="margin-top:14px"><h3>${esc(ctx.t("doctor.missing"))}</h3>${missing || `<div class="dim">${esc(ctx.t("doctor.nothingMissing"))}</div>`}</div>
      ${report.error ? `<div class="alert surface" style="margin-top:12px"><div class="h">${esc(ctx.t("doctor.error"))}</div>${esc(report.error)}</div>` : ""}`;
    main.querySelector("#redo").addEventListener("click", async () => {
      main.innerHTML = loading(ctx.t("doctor.loading"));
      try {
        const fresh = await api.get("/api/doctor", { refresh: 1 });
        ctx.state.doctor = fresh;
        await doctorView(main, ctx);
      } catch (error) {
        ctx.toast(error.message);
      }
    });
  } catch (error) {
    main.innerHTML = emptyState(ctx.t("doctor.unavailable"), [String(error.message || error)]);
  }
}

export { settingsView } from "./settings.js";

function runMeta(ctx, detail) {
  const summary = detail.summary;
  const eta = summary.step_time && summary.steps ? duration((summary.steps - summary.step) * summary.step_time) : "—";
  const stepText = summary.steps
    ? ctx.t("run.step", { step: summary.step, steps: summary.steps })
    : ctx.t("run.stepOnly", { step: summary.step });
  const resume = summary.managed?.config
    ? `<a class="btn" href="#/new?${new URLSearchParams({ resume: "latest" }).toString()}">${esc(ctx.t("run.resume"))}</a>`
    : "";
  return `<div class="vhead surface" id="run-head">
      <h2>${esc(summary.name)}</h2>
      <div class="meta">${esc(stepText)} · ${summary.tokens_per_s ? `${rate(summary.tokens_per_s)} tok/s` : "—"} · ${esc(ctx.t("run.eta", { eta }))}</div>
      <div class="acts">${statusStamp(ctx, summary.status)}${resume}${summary.status === "running" ? `<button class="btn dngr" data-stop-run="${esc(summary.name)}">${esc(ctx.t("run.stop"))}</button>` : ""}</div>
    </div>`;
}

function overviewHtml(ctx, detail) {
  const points = detail.metrics || [];
  const last = points[points.length - 1] || {};
  const losses = points.map((point) => point.loss).filter((value) => typeof value === "number");
  const vrams = points.map((point) => point.vram_peak_alloc ?? point.vram_peak_reserved).filter((value) => typeof value === "number");
  const tokens = points.map((point) => point.tokens_per_s).filter((value) => typeof value === "number");
  const firstStep = points.length ? points[0].step : null;
  const lastStep = points.length ? points[points.length - 1].step : null;
  const row = (label, value) => `<tr><td class="k">${esc(label)}</td><td class="dots"></td><td class="v">${value}</td></tr>`;
  return `<div id="run-overview">
    <div class="grid2">
      <div class="box"><h3>${esc(ctx.t("run.loss"))} <b>${last.loss == null ? "—" : num(last.loss)}</b></h3>
        ${losses.length ? `<pre class="chart">${esc(bars(losses, 6, "#"))}</pre><div class="axisline"><span>${esc(firstStep ?? "")}</span><span>${esc(lastStep ?? "")}</span></div>` : `<div class="dim">${esc(ctx.t("run.noMetrics"))}</div>`}
      </div>
      <div class="box"><h3>${esc(ctx.t("run.ledger"))}</h3>
        <table class="ledger">
          ${row(ctx.t("run.ledger.lr"), last.lr == null ? "—" : num(last.lr, 6))}
          ${row(ctx.t("run.ledger.grad"), last.grad_norm == null ? "—" : metric(last.grad_norm))}
          ${row(ctx.t("run.ledger.throughput"), last.tokens_per_s == null ? "—" : `${rate(last.tokens_per_s)} tok/s`)}
          ${row(ctx.t("run.ledger.vram"), last.vram_peak_alloc == null ? "—" : `${metric(last.vram_peak_alloc)} GiB`)}
          ${row(ctx.t("run.ledger.lossScale"), last.loss_scale == null ? "—" : esc(last.loss_scale))}
        </table>
      </div>
    </div>
    <div class="box" style="margin-top:14px"><h3>${esc(ctx.t("run.vram"))} <b>${vrams.length ? `${metric(vrams[vrams.length - 1])} GiB` : "—"}</b></h3><pre class="chart" style="font-size:var(--fs-12);line-height:1.25">${esc(spark(vrams))}</pre></div>
    <div class="box" style="margin-top:14px"><h3>${esc(ctx.t("run.throughput"))} <b>${tokens.length ? `${rate(tokens[tokens.length - 1])} tok/s` : "—"}</b></h3><pre class="chart" style="font-size:var(--fs-12);line-height:1.25">${esc(spark(tokens))}</pre></div>
    <div class="box" style="margin-top:14px"><h3>${esc(ctx.t("run.tail"))} <b>log.txt</b></h3><div class="logbox flat" style="max-height:150px">${(detail.logs || []).slice(-12).map(logLine).join("") || `<div class="dim">${esc(ctx.t("run.noLog"))}</div>`}</div></div>
  </div>`;
}

function logsHtml(ctx, detail) {
  return `<div class="box"><h3>${esc(ctx.t("run.log"))} <b>${esc(ctx.t("run.log.lines", { n: detail.logs.length }))}</b></h3>
    <div class="form" style="border-top:none"><div class="lab">${esc(ctx.t("run.filter"))}</div><div class="val"><input class="field" id="logfilter" aria-label="${esc(ctx.t("run.filter"))}" placeholder="${esc(ctx.t("run.filter.ph"))}" spellcheck="false"><button class="btn on" id="logfollow" type="button" aria-pressed="true">${esc(ctx.t("run.follow"))}</button></div></div>
    <div class="logbox flat" id="logstream" style="max-height:520px">${(detail.logs || []).map(logLine).join("") || `<div class="dim">${esc(ctx.t("run.noLog"))}</div>`}</div></div>`;
}

function artifactsPane(ctx, detail) {
  const rows = detail.checkpoints.map((item) => `<tr><td>${esc(item.name)}</td><td class="num">${bytes(item.size)}</td><td>${when(item.mtime)}</td><td class="dim">${esc((item.exports || []).join(", ") || "—")}</td></tr>`).join("");
  const shots = [];
  detail.samples.forEach((sample) => {
    sample.files.filter((file) => /\.(png|jpe?g|webp)$/i.test(file.name)).slice(0, 8).forEach((file) => {
      shots.push(`<div class="shot"><img loading="lazy" src="${api.fileUrl(detail.summary.name, `samples/${sample.name}/${file.name}`)}" alt="${esc(file.name)}"><div class="cap"><span>${esc(sample.name)}</span><span>${esc(file.name)}</span></div></div>`);
    });
  });
  return `${rows ? `<div class="table-frame surface"><div class="table-scroll" tabindex="0"><table class="led"><thead><tr><th>${esc(ctx.t("run.col.step"))}</th><th class="num">${esc(ctx.t("run.col.size"))}</th><th>${esc(ctx.t("run.col.saved"))}</th><th>${esc(ctx.t("run.col.export"))}</th></tr></thead><tbody>${rows}</tbody></table></div></div>` : emptyState(ctx.t("run.noCheckpoint"), [ctx.t("run.checkpoint.hint")])}
    <div class="box" style="margin-top:14px"><h3>${esc(ctx.t("run.samples"))}</h3>${shots.length ? `<div class="gallery">${shots.join("")}</div>` : `<div class="dim">${esc(ctx.t("run.noSamples"))} ${esc(ctx.t("run.noSamples.hint"))}</div>`}</div>`;
}

function configPane(ctx, detail) {
  const entries = Object.entries(detail.config || {}).sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) return emptyState(ctx.t("run.config.empty.title"), [ctx.t("run.config.empty.body")]);
  const rows = entries.map(([key, value]) => `<tr data-key="${esc(key)}"><td>${esc(key)}</td><td class="wrap">${esc(typeof value === "object" ? JSON.stringify(value) : value)}</td></tr>`).join("");
  return `<div class="box"><h3>${esc(ctx.t("run.config"))} <b>${esc(ctx.t("run.config.count", { n: entries.length }))}</b></h3>
    <div class="form" style="border-top:none"><div class="lab">${esc(ctx.t("run.config.filter"))}</div><div class="val"><input class="field" id="cfgfilter" aria-label="${esc(ctx.t("run.config.filter"))}" placeholder="${esc(ctx.t("run.config.ph"))}" spellcheck="false"></div></div>
    <table class="led" id="cfgtbl"><thead><tr><th>KNOB</th><th>VALUE</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

export async function runView(main, ctx, name) {
  const state = ctx.state;
  const requestedTab = ctx.query().get("tab");
  if (requestedTab) state.runTab = requestedTab;
  if (!state.runTab) state.runTab = "overview";
  main.innerHTML = loading(name || "RUN");
  let detail;
  try {
    detail = await api.get("/api/run", { name });
  } catch (error) {
    main.innerHTML = emptyState(ctx.t("run.unavailable"), [String(error.message || error)]);
    return;
  }
  state.runDetail = detail;
  main.innerHTML = `${runMeta(ctx, detail)}<div id="run-body"></div>`;

  const follow = { on: true };

  const applyLogFilter = (root) => {
    const filter = root.querySelector("#logfilter");
    if (!filter) return;
    const needle = filter.value.toLowerCase();
    root.querySelectorAll("#logstream .ln").forEach((line) => {
      line.style.display = line.textContent.toLowerCase().includes(needle) ? "" : "none";
    });
  };

  const renderTab = () => {
    const body = main.querySelector("#run-body");
    if (!body) return;
    const tab = state.runTab || "overview";
    const tabs = ["overview", "logs", "artifacts", "config"];
    body.innerHTML = `<div class="tabs surface">${tabs.map((item) => `<button data-tab="${item}" class="${item === tab ? "on" : ""}">${esc(ctx.t(`run.tab.${item}`))}</button>`).join("")}</div>
      <div class="pane ${tab === "overview" ? "on" : ""}" id="pane-overview">${overviewHtml(ctx, detail)}</div>
      <div class="pane ${tab === "logs" ? "on" : ""}" id="pane-logs">${logsHtml(ctx, detail)}</div>
      <div class="pane ${tab === "artifacts" ? "on" : ""}" id="pane-artifacts">${artifactsPane(ctx, detail)}</div>
      <div class="pane ${tab === "config" ? "on" : ""}" id="pane-config">${configPane(ctx, detail)}</div>`;
    body.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => {
      state.runTab = button.dataset.tab;
      body.querySelectorAll("[data-tab]").forEach((item) => item.classList.toggle("on", item === button));
      body.querySelectorAll(".pane").forEach((pane) => pane.classList.toggle("on", pane.id === `pane-${state.runTab}`));
    }));
    const filter = body.querySelector("#logfilter");
    if (filter) filter.addEventListener("input", () => applyLogFilter(body));
    const followBtn = body.querySelector("#logfollow");
    if (followBtn) followBtn.addEventListener("click", () => {
      follow.on = !follow.on;
      followBtn.classList.toggle("on", follow.on);
      followBtn.setAttribute("aria-pressed", String(follow.on));
      followBtn.textContent = ctx.t(follow.on ? "run.follow.on" : "run.follow.off");
      if (follow.on) {
        const stream = body.querySelector("#logstream");
        if (stream) stream.scrollTop = stream.scrollHeight;
      }
    });
    const stream = body.querySelector("#logstream");
    if (stream) {
      stream.scrollTop = stream.scrollHeight;
      stream.addEventListener("scroll", () => {
        const atBottom = stream.scrollTop + stream.clientHeight >= stream.scrollHeight - 24;
        if (!atBottom && follow.on) {
          follow.on = false;
          const btn = body.querySelector("#logfollow");
          if (btn) {
            btn.classList.remove("on");
            btn.setAttribute("aria-pressed", "false");
            btn.textContent = ctx.t("run.follow.off");
          }
        }
      });
    }
    const cfgFilter = body.querySelector("#cfgfilter");
    if (cfgFilter) cfgFilter.addEventListener("input", () => {
      const needle = cfgFilter.value.toLowerCase();
      body.querySelectorAll("#cfgtbl tbody tr").forEach((row) => {
        row.style.display = row.dataset.key.toLowerCase().includes(needle) ? "" : "none";
      });
    });
  };
  renderTab();

  const bindStop = () => {
    const button = main.querySelector("[data-stop-run]");
    if (!button || button.dataset.bound) return;
    button.dataset.bound = "1";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await api.post("/api/run/stop", { name });
        ctx.toast(result.stopped ? ctx.t("run.stopSent") : (result.reason || ctx.t("run.stopFailed")), result.stopped);
      } catch (error) {
        ctx.toast(error.message);
      } finally {
        button.disabled = false;
      }
    });
  };
  bindStop();

  ctx.startPoll(async () => {
    try {
      const fresh = await api.get("/api/run", { name });
      state.runDetail = fresh;
      const oldTab = state.runTab;
      const head = main.querySelector("#run-head");
      if (head) head.outerHTML = runMeta(ctx, fresh);
      bindStop();
      if (oldTab === "overview") {
        const old = main.querySelector("#run-overview");
        if (old) old.outerHTML = overviewHtml(ctx, fresh);
      } else if (oldTab === "logs") {
        const stream = main.querySelector("#logstream");
        if (stream) {
          stream.innerHTML = (fresh.logs || []).map(logLine).join("") || `<div class="dim">${esc(ctx.t("run.noLog"))}</div>`;
          const body = main.querySelector("#run-body");
          if (body) applyLogFilter(body);
          if (follow.on) stream.scrollTop = stream.scrollHeight;
        }
      }
      if (fresh.summary.status !== "running") ctx.stopPoll();
    } catch {
      /* a transient read error leaves the last render in place */
    }
  }, 2500);
}
