/* Appearance state: theme, per-theme colour overrides, glass surfaces, wallpaper. */
import * as api from "./api.js";

export const THEMES = ["kohaku", "soul", "teletype"];

export const COLOR_KEYS = ["canvas", "panel", "text", "border", "accent"];

const ZERO_COLORS = () => Object.fromEntries(COLOR_KEYS.map((key) => [key, ""]));

const DEFAULTS = {
  theme: "teletype",
  mode: "light",
  lang: "auto",
  glass: false,
  surfaceOpacity: 0.82,
  background: { enabled: false, url: "", blur: 5, mask: 0.5 },
};

let current = normalize(null);
let timer = null;

function clamp(value, low, high, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(high, Math.max(low, number));
}

function hex(value) {
  const text = String(value ?? "").trim().toLowerCase();
  return /^#[0-9a-f]{6}$/.test(text) ? text : "";
}

export function normalize(raw) {
  const data = raw && typeof raw === "object" ? raw : {};
  const background = data.background && typeof data.background === "object" ? data.background : {};
  const stored = data.colors && typeof data.colors === "object" ? data.colors : {};
  const legacy = data.custom && typeof data.custom === "object" ? data.custom : {};
  const colors = {};
  for (const name of THEMES) {
    const given = stored[name] && typeof stored[name] === "object" ? stored[name] : {};
    const values = ZERO_COLORS();
    for (const key of COLOR_KEYS) values[key] = hex(given[key]);
    colors[name] = values;
  }
  let theme = THEMES.includes(data.theme) ? data.theme : DEFAULTS.theme;
  if (data.theme === "custom") {
    // Legacy fourth theme: keep its palette as teletype overrides.
    theme = "teletype";
    const map = { canvas: "paper", panel: "panel", text: "ink", border: "border", accent: "accent" };
    for (const [key, old] of Object.entries(map)) {
      if (!colors[theme][key] && hex(legacy[old])) colors[theme][key] = hex(legacy[old]);
    }
  }
  return {
    theme,
    mode: data.mode === "carbon" ? "carbon" : "light",
    lang: ["auto", "en", "zh"].includes(data.lang) ? data.lang : DEFAULTS.lang,
    glass: data.glass === true,
    surfaceOpacity: clamp(data.surfaceOpacity, 0, 1, DEFAULTS.surfaceOpacity),
    background: {
      enabled: background.enabled === true,
      url: typeof background.url === "string" ? background.url : "",
      blur: clamp(background.blur, 0, 30, DEFAULTS.background.blur),
      mask: clamp(background.mask, 0, 1, DEFAULTS.background.mask),
    },
    colors,
    backgroundUrl: typeof data.backgroundUrl === "string" ? data.backgroundUrl : "",
  };
}

export function getAppearance() {
  return current;
}

/** Overrides stored for one theme; "" entries fall back to the theme value. */
export function themeColors(theme = current.theme) {
  return current.colors[theme] || ZERO_COLORS();
}

const BASE_VARS = {
  canvas: "--canvas",
  panel: "--panel",
  text: "--text-primary",
  border: "--border-default",
  accent: "--accent",
};

/** Derived tokens: recomputed by the five pickers (docs/teletype.md §3). */
const DERIVED_VARS = [
  "--canvas", "--paper", "--panel", "--panel-alt", "--elevated", "--overlay",
  "--border-subtle", "--border-default", "--border-strong",
  "--text-primary", "--text-secondary", "--text-muted", "--text-disabled", "--text-inverse",
  "--ink", "--dim", "--rule", "--tint2",
  "--accent", "--accent-weak", "--accent-strong", "--accent-on", "--status-run",
];

/** Material tokens: theme-owned values, never recomputed by the picker (§3).
    Only teletype claims them; kohaku/soul declare none of these, so dropping
    them there would change how those two themes look. */
const MATERIAL_VARS = ["--perf", "--perf-hole", "--red", "--tint", "--log-text"];

const MATERIAL_EXEMPT = { teletype: MATERIAL_VARS };

/** Every inline token applyColors() may own; all of them are cleared per pass. */
const MANAGED_VARS = [...DERIVED_VARS, ...MATERIAL_VARS];

/** The tokens the active theme lets the picker write. */
function ownedVars(theme = current.theme) {
  const exempt = MATERIAL_EXEMPT[theme] || [];
  return MANAGED_VARS.filter((name) => !exempt.includes(name));
}

const baseCache = new Map();

/** The colours the current theme + mode define in CSS, read without overrides. */
function themeBase() {
  const key = `${current.theme}/${current.mode}`;
  if (baseCache.has(key)) return baseCache.get(key);
  const root = document.documentElement;
  const saved = MANAGED_VARS.map((name) => [name, root.style.getPropertyValue(name)]);
  MANAGED_VARS.forEach((name) => root.style.removeProperty(name));
  const computed = getComputedStyle(root);
  const values = {};
  for (const [name, cssVar] of Object.entries(BASE_VARS)) {
    values[name] = computed.getPropertyValue(cssVar).trim() || "#000000";
  }
  saved.forEach(([name, value]) => { if (value) root.style.setProperty(name, value); });
  baseCache.set(key, values);
  return values;
}

/** Effective five base colours: overrides on top of the active theme. */
export function effectiveColors() {
  const base = themeBase();
  const over = themeColors();
  const out = {};
  for (const key of COLOR_KEYS) out[key] = over[key] || base[key];
  return out;
}

export function isOverridden() {
  const over = themeColors();
  return COLOR_KEYS.some((key) => over[key]);
}

function onAccent(color) {
  return contrast(color, "#17191b") >= contrast(color, "#ffffff") ? "#17191b" : "#ffffff";
}

function applyColors() {
  const root = document.documentElement;
  const style = root.style;
  MANAGED_VARS.forEach((name) => style.removeProperty(name));
  const eff = effectiveColors();
  const over = themeColors();
  const active = COLOR_KEYS.some((key) => over[key]);
  root.dataset.colors = active ? "on" : "off";
  root.dataset.dark = luminance(eff.canvas) < 0.35 ? "on" : "off";
  if (!active) return;

  const owned = new Set(ownedVars());
  const put = (name, value) => { if (owned.has(name)) style.setProperty(name, value); };
  const mix = (color, percent, other) => `color-mix(in srgb, ${color} ${percent}%, ${other})`;
  put("--canvas", eff.canvas);
  put("--paper", eff.canvas);
  put("--panel", eff.panel);
  put("--panel-alt", mix(eff.panel, 84, eff.canvas));
  put("--elevated", mix(eff.panel, 92, eff.text));
  put("--overlay", eff.panel);
  put("--border-subtle", mix(eff.border, 72, eff.canvas));
  put("--border-default", eff.border);
  put("--border-strong", mix(eff.text, 38, eff.border));
  put("--text-primary", eff.text);
  put("--text-secondary", mix(eff.text, 76, eff.canvas));
  put("--text-muted", mix(eff.text, 54, eff.canvas));
  put("--text-disabled", mix(eff.text, 34, eff.canvas));
  put("--text-inverse", eff.canvas);
  put("--ink", eff.text);
  put("--dim", mix(eff.text, 58, eff.canvas));
  put("--rule", eff.border);
  put("--perf", mix(eff.canvas, 92, eff.text));
  put("--perf-hole", `color-mix(in srgb, ${mix(eff.canvas, 92, eff.text)} 55%, #000)`);
  put("--red", eff.accent);
  put("--tint", mix(eff.text, 6, "transparent"));
  put("--tint2", mix(eff.text, 10, "transparent"));
  put("--log-text", mix(eff.text, 88, eff.canvas));
  put("--accent", eff.accent);
  put("--accent-weak", mix(eff.accent, 16, "transparent"));
  put("--accent-strong", mix(eff.accent, 82, "#ffffff"));
  put("--accent-on", onAccent(eff.accent));
  put("--status-run", eff.accent);
}

export function applyAppearance() {
  const root = document.documentElement;
  const data = current;
  const mode = data.mode;
  root.dataset.theme = data.theme;
  root.dataset.mode = mode;
  root.dataset.glass = data.glass ? "on" : "off";
  const style = root.style;
  style.setProperty("--surface-alpha", String(data.surfaceOpacity));
  applyColors();

  const url = data.background.enabled ? (data.backgroundUrl || data.background.url) : "";
  root.dataset.bg = url ? "on" : "off";
  if (url) paintWallpaper(url, data.background.blur, data.background.mask);
  else clearWallpaper();

  const button = document.getElementById("modeBtn");
  if (button) button.textContent = mode === "light" ? "[ CARBON ]" : "[ LIGHT ]";
  const footer = document.getElementById("f-theme");
  if (footer) footer.textContent = `${data.theme.toUpperCase()} / ${mode.toUpperCase()}`;
  window.dispatchEvent(new CustomEvent("km-theme-applied", { detail: { mode, theme: data.theme } }));
}

/* --- wallpaper: one paint on the <html> canvas, blur baked into the image --- */

const wallpaperCache = new Map();
let wallpaperSeq = 0;

function clearWallpaper() {
  wallpaperSeq += 1;
  document.documentElement.style.backgroundImage = "none";
}

/** Composite `color` over `desk` at `t` (sRGB, the space CSS composites in). */
function blend(color, desk, t) {
  const top = parsed(color);
  const under = parsed(desk);
  return `#${top
    .map((value, i) => Math.round(value + (under[i] - value) * t).toString(16).padStart(2, "0"))
    .join("")}`;
}

/** Smallest paper fraction (0..1) of the desk that keeps `ink` at `target`. */
function paperFloor(ink, paper, desk, target) {
  for (let step = 0; step <= 100; step += 1) {
    const fraction = step / 100;
    if (contrast(blend(paper, desk, 1 - fraction), ink) >= target) return fraction;
  }
  return 1;
}

/** Extra veil a translucent teletype sheet needs (docs/teletype.md §6): body ink
    holds 4.5:1 at 30 % surface alpha and 7:1 from 65 % up, against the worst desk
    pixel (black in light, white in carbon). Opaque stock needs none. */
function teleVeilFloor() {
  const sheet = current.surfaceOpacity;
  if (sheet >= 1) return 0;
  const colors = effectiveColors();
  const desk = luminance(colors.canvas) < 0.35 ? "#ffffff" : "#000000";
  // 30 % -> 4.5:1, 65 % and up -> 7:1; +0.05 covers browser rounding on the composite.
  const ramp = 4.5 + 2.5 * Math.min(1, Math.max(0, (sheet - 0.3) / 0.35));
  const paper = paperFloor(colors.text, colors.canvas, desk, ramp + 0.05);
  const veil = (paper - sheet) / (1 - sheet);
  return Number.isFinite(veil) ? Math.min(1, Math.max(0, veil)) : 0;
}

function veilColor(mask) {
  const canvas = effectiveColors().canvas;
  const [r, g, b] = parsed(canvas);
  // light surfaces need a little more veil for the same legibility
  let alpha = luminance(canvas) < 0.35 ? mask : Math.min(0.9, mask + 0.1);
  // Teletype prints on thin stock, not frosted glass: the veil keeps the desk paper
  // enough for body ink at every slider position, because the sheet itself is now
  // translucent. kohaku/soul keep the plain mask.
  if (current.theme === "teletype") alpha = Math.max(alpha, teleVeilFloor());
  return `rgb(${r} ${g} ${b} / ${Math.round(alpha * 100)}%)`;
}

/** Gaussian blur baked on a canvas: never a full-viewport CSS filter. */
function bakeBlur(src, blur) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => {
      try {
        const scale = Math.min(1, 1280 / Math.max(img.naturalWidth, img.naturalHeight));
        const w = Math.max(1, Math.round(img.naturalWidth * scale));
        const h = Math.max(1, Math.round(img.naturalHeight * scale));
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("canvas 2d context unavailable");
        const pad = Math.min(64, Math.round(blur * scale * 2));
        ctx.filter = `blur(${(blur * scale).toFixed(2)}px)`;
        ctx.drawImage(img, -pad, -pad, w + pad * 2, h + pad * 2);
        resolve(canvas.toDataURL("image/jpeg", 0.85));
      } catch (error) {
        reject(error);
      }
    };
    img.onerror = () => reject(new Error("wallpaper load failed"));
    img.src = src;
  });
}

async function paintWallpaper(url, blur, mask) {
  const root = document.documentElement;
  const seq = ++wallpaperSeq;
  const source = api.backgroundUrl(url);
  const key = `${source}|${blur}`;
  let paint = wallpaperCache.get(key);
  if (!paint) {
    paint = source;
    if (blur > 0) {
      try {
        paint = await bakeBlur(source, blur);
      } catch {
        // cross-origin image the canvas cannot read back: use the sharp source
        paint = source;
      }
    }
    wallpaperCache.set(key, paint);
    if (wallpaperCache.size > 4) wallpaperCache.delete(wallpaperCache.keys().next().value);
  }
  if (seq !== wallpaperSeq) return;
  const veil = veilColor(mask);
  root.style.backgroundImage = `linear-gradient(${veil}, ${veil}), url("${paint.replace(/"/g, '\\"')}")`;
}

function persist(payload, immediate) {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  const run = () => {
    timer = null;
    api.post("/api/appearance", payload).then((data) => {
      current = normalize(data);
      applyAppearance();
    }).catch(() => {});
  };
  if (immediate) run();
  else timer = setTimeout(run, 300);
}

function savePayload() {
  const data = current;
  return {
    theme: data.theme,
    mode: data.mode,
    lang: data.lang,
    glass: data.glass,
    surfaceOpacity: data.surfaceOpacity,
    background: data.background,
    colors: data.colors,
  };
}

/** Merge a partial patch, apply it now and persist (debounced by default). */
export function patch(partial, immediate = false) {
  const colors = { ...current.colors };
  if (partial.colors && typeof partial.colors === "object") {
    for (const [name, values] of Object.entries(partial.colors)) {
      if (!THEMES.includes(name) || !values || typeof values !== "object") continue;
      colors[name] = { ...colors[name], ...values };
    }
  }
  current = normalize({
    ...current,
    ...partial,
    background: { ...current.background, ...(partial.background || {}) },
    colors,
  });
  applyAppearance();
  persist(savePayload(), immediate);
}

export function setTheme(theme) {
  if (!THEMES.includes(theme)) return;
  const mode = theme === "kohaku" ? "light" : theme === "soul" ? "carbon" : current.mode;
  patch({ theme, mode }, true);
}

export function setColors(values, immediate = true) {
  patch({ colors: { [current.theme]: values } }, immediate);
}

export function toggleMode() {
  patch({ mode: current.mode === "light" ? "carbon" : "light" }, true);
}

function legacy() {
  const theme = localStorage.getItem("km-theme") || "teletype";
  const mode = localStorage.getItem("km-mode") || "light";
  return { theme, mode };
}

export async function load() {
  try {
    current = normalize(await api.get("/api/appearance"));
    applyAppearance();
    return true;
  } catch {
    current = normalize(legacy());
    applyAppearance();
    return false;
  }
}

export async function reload() {
  current = normalize(await api.get("/api/appearance"));
  applyAppearance();
  return current;
}

export async function uploadBackground(file) {
  const type = file.type || "application/octet-stream";
  try {
    await api.postRaw("/api/appearance/background", file, type);
  } catch (error) {
    throw new Error(String(error.message || error));
  }
  return reload();
}

export async function removeBackground() {
  await api.remove("/api/appearance/background");
  return reload();
}

/* --- contrast helpers (sRGB relative luminance) --- */
function channel(value) {
  const c = value / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function parsed(hexValue) {
  const text = String(hexValue || "").trim().toLowerCase();
  const rgb = text.match(/^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  const body = text.replace("#", "");
  if (body.length !== 6) return [0, 0, 0];
  return [parseInt(body.slice(0, 2), 16), parseInt(body.slice(2, 4), 16), parseInt(body.slice(4, 6), 16)];
}

export function luminance(hexValue) {
  const [r, g, b] = parsed(hexValue);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrast(a, b) {
  const one = luminance(a);
  const two = luminance(b);
  const high = Math.max(one, two);
  const low = Math.min(one, two);
  return (high + 0.05) / (low + 0.05);
}
