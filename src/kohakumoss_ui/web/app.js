/* Router, shell state, i18n wiring, polling and appearance. */
import * as api from "./api.js";
import * as appearance from "./appearance.js";
import * as i18n from "./i18n.js";
import * as views from "./views.js";

// The studio views (queue, profile, notebook) live in their own module so the
// shell keeps booting even while that file is absent or being edited.
const studio = await import("./studio.js").catch(() => null);

const state = {
  trainer: null,
  runs: [],
  knobs: null,
  configs: null,
  doctor: null,
  doctorLoading: false,
  runDetail: null,
  runTab: "overview",
  selectedRun: null,
  selectedConfig: null,
  queue: null,
  notebook: null,
  profile: null,
};

let pollTimer = null;
let routeSeq = 0;
let acceptedHash = location.hash;
let navigationPending = false;

const main = document.getElementById("main");
const crumb = document.getElementById("crumb");
const chip = document.getElementById("chip");
const chipText = document.getElementById("chipText");
const runner = document.getElementById("runner");
const modeBtn = document.getElementById("modeBtn");
const langBtn = document.getElementById("langBtn");

const t = (key, vars) => i18n.t(key, vars);

function hashParts() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  return { path: path || "runs", query: new URLSearchParams(query || "") };
}

function query() {
  return hashParts().query;
}

function navigate(hash) {
  return route({ hash });
}

function toast(message, ok = false) {
  const node = document.createElement("div");
  node.className = `toast ${ok ? "ok" : ""}`;
  node.textContent = message;
  document.getElementById("toast").appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function statusLabel(status) {
  const key = `runs.filter.${String(status || "").toLowerCase()}`;
  return t(key);
}

function updateRunner(runs) {
  state.runs = runs || [];
  const running = state.runs.find((run) => run.status === "running");
  const latest = running || state.runs[0];
  if (!latest) {
    chip.className = "chip idle";
    chipText.textContent = t("chip.noRun");
    runner.innerHTML = `<div class="row"><span class="live">${escapeHtml(t("shell.empty"))}</span><span>--</span></div><div class="row"><span>${escapeHtml(t("shell.idle"))}</span></div><div>${escapeHtml(t("nav.new"))}</div><div class="bar">[..........]</div>`;
    return;
  }
  chip.className = `chip ${running ? "" : "idle"}`;
  chipText.textContent = `${statusLabel(latest.status)} · ${latest.name}`;
  const cells = Math.round((latest.progress || 0) * 10);
  runner.innerHTML = `
    <div class="row"><span class="live">${escapeHtml(statusLabel(latest.status))}</span><span>${Math.round((latest.progress || 0) * 100)}%</span></div>
    <div class="row"><span>${escapeHtml(latest.name)}</span></div>
    <div>step ${latest.step || 0}${latest.steps ? ` / ${latest.steps}` : ""}</div>
    <div class="bar">[<span class="fill">${"#".repeat(cells)}</span>${".".repeat(10 - cells)}]</div>`;
}

function applyLayout() {
  const density = localStorage.getItem("km-density") || "relaxed";
  const scale = localStorage.getItem("km-scale") || "1.1";
  document.documentElement.dataset.density = density;
  document.documentElement.style.setProperty("--scale", scale);
}

function crumbFor(path) {
  const part = (key) => t(key);
  switch (path) {
    case "new": return `${part("nav.group.workbench")} / ${part("nav.new")}`;
    case "queue": return `${part("nav.group.studio")} / ${part("nav.queue")}`;
    case "profile": return `${part("nav.group.studio")} / ${part("nav.profile")}`;
    case "notebook": return `${part("nav.group.studio")} / ${part("nav.notebook")}`;
    case "grabber": return `${part("nav.group.data")} / ${part("nav.grabber")}`;
    case "datasets": return part("nav.group.data");
    case "artifacts": return part("nav.group.data");
    case "doctor": return `${part("nav.group.system")} / ${part("nav.doctor")}`;
    case "settings": return `${part("nav.group.system")} / ${part("nav.settings")}`;
    case "run": return `${part("nav.group.workbench")} / ${part("nav.runs")}`;
    default: return part("nav.group.workbench");
  }
}

/** Re-apply every translated piece of the shell after a language or mode change. */
function applyShell() {
  i18n.applyStatic(document);
  const now = appearance.getAppearance();
  modeBtn.textContent = `[ ${t(`shell.mode.${now.mode}`)} ]`;
  modeBtn.title = t("shell.mode.title");
  langBtn.textContent = `[ ${i18n.lang().toUpperCase()} ]`;
  langBtn.title = t("shell.lang.title");
  document.getElementById("f-theme").textContent = `${now.theme.toUpperCase()} / ${t(`shell.mode.${now.mode}`)}`;
  crumb.textContent = crumbFor(hashParts().path.split("/")[0]);
  updateRunner(state.runs);
}

function setFooter() {
  const trainer = state.trainer || {};
  const engine = state.doctor?.packages?.["kohaku-engine"];
  document.getElementById("f-kogine").textContent = engine || (trainer.kogine ? "available" : "—");
  if (state.knobs) document.getElementById("f-knobs").textContent = state.knobs.count;
  const device = state.doctor?.devices?.[0];
  if (device) document.getElementById("f-gpu").textContent = `${device.name.replace(/NVIDIA |GeForce |GPU/g, "").trim()} ${device.memory_gib?.toFixed?.(0) || ""}G`;
  if (state.doctor?.packages?.torch) document.getElementById("f-torch").textContent = `${state.doctor.packages.torch} cu${state.doctor.torch_cuda || ""}`;
}

function startPoll(fn, ms) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(fn, ms);
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function setLanguage(value) {
  return route({ language: value });
}

function unavailableView(label) {
  main.innerHTML = `<div class="empty surface"><b>${escapeHtml(label)}</b><div>${escapeHtml(t("common.loading"))}</div></div>`;
}

// Every destructive re-render (including same-hash and language changes) goes
// through the mounted view's gate. Keep the current URL and language until it
// resolves; a second request must not replace the first intent or its resolver.
async function route({ hash = location.hash, language } = {}) {
  const hashChanged = location.hash !== acceptedHash;
  const url = (value) => location.pathname + location.search + value;
  if (hashChanged) history.replaceState(null, "", url(acceptedHash));
  if (navigationPending) return false;
  navigationPending = true;
  const guard = main.__studioBeforeNavigate;
  const wasInert = main.inert;
  try {
    if (guard && !await guard({ kind: "route", value: hash })) return false;
    if (guard !== main.__studioBeforeNavigate) return false;
    if (language !== undefined) {
      // Dictionary loading is asynchronous: do not allow a new draft between
      // approval and the language re-render.
      main.inert = true;
      await i18n.setLang(language);
      appearance.patch({ lang: language }, true);
    }
    if (hash !== acceptedHash) {
      if (hashChanged) history.replaceState(null, "", url(hash));
      else history.pushState(null, "", url(hash));
    }
    acceptedHash = hash;
    if (language !== undefined) applyShell();
    // Release the gate lock once rendering starts, not after network reads in
    // the new view finish. renderRoute owns the existing routeSeq lifecycle.
    return renderRoute().then(() => true);
  } finally {
    main.inert = wasInert;
    navigationPending = false;
  }
}

async function renderRoute() {
  main.__studioCleanup?.();
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  const seq = ++routeSeq;
  const { path } = hashParts();
  const parts = path.split("/");
  document.querySelectorAll(".nav a").forEach((link) => {
    link.classList.toggle("on", link.dataset.view === (parts[0] === "run" ? "runs" : parts[0]));
  });
  crumb.textContent = crumbFor(parts[0]);

  const ctx = {
    state,
    navigate,
    toast,
    query,
    t,
    lang: () => i18n.lang(),
    setLanguage,
    updateRunner,
    rerender: () => { if (seq === routeSeq) route(); },
    applyLayout,
    applyShell,
    startPoll,
    stopPoll,
  };

  try {
    if (parts[0] === "run" && parts[1]) {
      await views.runView(main, ctx, decodeURIComponent(parts[1]));
    } else if (parts[0] === "new" && parts[1]) {
      await views.newView(main, ctx, decodeURIComponent(parts[1]));
    } else if (parts[0] === "new") {
      await views.newView(main, ctx, "");
    } else if (parts[0] === "grabber") {
      if (studio?.grabberView) await studio.grabberView(main, ctx);
      else unavailableView(t("nav.grabber"));
    } else if (parts[0] === "queue") {
      if (studio?.queueView) await studio.queueView(main, ctx);
      else unavailableView(t("nav.queue"));
    } else if (parts[0] === "profile") {
      if (studio?.profileView) await studio.profileView(main, ctx);
      else unavailableView(t("nav.profile"));
    } else if (parts[0] === "notebook") {
      if (studio?.notebookView) await studio.notebookView(main, ctx);
      else unavailableView(t("nav.notebook"));
    } else {
      switch (parts[0]) {
        case "datasets": await views.datasetsView(main, ctx); break;
        case "artifacts": await views.artifactsView(main, ctx); break;
        case "doctor": await views.doctorView(main, ctx); break;
        case "settings": await views.settingsView(main, ctx); break;
        default: await views.runsView(main, ctx); break;
      }
    }
  } catch (error) {
    const message = String(error.message || error);
    main.innerHTML = `<div class="empty surface"><b>${message.includes("bad token") ? escapeHtml(t("shell.badToken")) : escapeHtml(t("common.error"))}</b><div>${escapeHtml(message)}</div><div>${escapeHtml(t("shell.restartHint"))}</div></div>`;
  }
  if (seq === routeSeq) setFooter();
}

async function boot() {
  const bootParams = new URLSearchParams(location.search);
  if (bootParams.get("scale")) localStorage.setItem("km-scale", bootParams.get("scale"));
  if (bootParams.get("density")) localStorage.setItem("km-density", bootParams.get("density"));
  applyLayout();
  await appearance.load();
  const bootTheme = bootParams.get("theme");
  if (bootTheme && appearance.THEMES.includes(bootTheme)) {
    appearance.patch({
      theme: bootTheme,
      mode: bootParams.get("mode") || appearance.getAppearance().mode,
    }, true);
  } else if (bootParams.get("mode")) {
    appearance.patch({ mode: bootParams.get("mode") }, true);
  }
  await i18n.setLang(appearance.getAppearance().lang);
  applyShell();
  if (bootParams.get("token")) {
    history.replaceState({}, "", location.pathname + location.hash);
  }
  state.doctorLoading = true;
  route();

  try {
    state.trainer = (await api.get("/api/state")).trainer;
    setFooter();
  } catch (error) {
    toast(error.message);
  }

  api.get("/api/knobs").then((knobs) => {
    state.knobs = knobs;
    setFooter();
  }).catch(() => {});

  api.get("/api/doctor").then((doctor) => {
    state.doctor = doctor;
    state.doctorLoading = false;
    setFooter();
    if (["runs", "new"].includes(hashParts().path)) route();
  }).catch((error) => {
    state.doctor = { error: error.message };
    state.doctorLoading = false;
  });

  updateRunner([]);
  setInterval(async () => {
    try {
      const data = await api.get("/api/runs");
      updateRunner(data.runs);
    } catch {
      /* keep the last known runner state */
    }
  }, 4000);
  setInterval(() => {
    document.getElementById("f-time").textContent = new Date().toLocaleTimeString(
      i18n.lang() === "zh" ? "zh-CN" : undefined, { hour12: false },
    );
  }, 1000);

  subscribeQueue();
}

/* The queue advances on the server clock (5 s tick), so the QUEUE view cannot
   rely on the 4 s runs poll to notice. The shell event stream announces a
   version bump; only QUEUE re-renders, because PROFILE/NOTEBOOK may hold an
   open edit form and a background repaint would throw that work away. */
function subscribeQueue() {
  if (typeof EventSource !== "function") return;
  const events = new EventSource(api.eventUrl());
  let pending = null;
  events.addEventListener("message", (event) => {
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload?.type !== "queue") return;
    state.queue = payload;
    if (pending) clearTimeout(pending);
    pending = setTimeout(() => {
      pending = null;
      if (hashParts().path === "queue") route();
    }, 300);
  });
}

modeBtn.addEventListener("click", () => {
  appearance.toggleMode();
});

langBtn.addEventListener("click", async () => {
  const next = i18n.lang() === "zh" ? "en" : "zh";
  if (await setLanguage(next)) toast(t("toast.lang"), true);
});

window.addEventListener("km-theme-applied", () => {
  applyShell();
});
window.addEventListener("hashchange", () => {
  // Ignore stale queued events after a rejected/restored hash navigation.
  if (location.hash !== acceptedHash) return route();
});
boot();
