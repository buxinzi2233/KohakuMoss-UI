/* i18n: one flat dictionary per language under web/lang, applied to the shell. */
const SUPPORTED = ["en", "zh"];
const cache = new Map();

let current = "en";
let active = {};
let fallback = {};

function browserLang() {
  const list = Array.isArray(navigator.languages) && navigator.languages.length
    ? navigator.languages
    : [navigator.language || "en"];
  for (const tag of list) {
    const base = String(tag || "").toLowerCase().split("-")[0];
    if (SUPPORTED.includes(base)) return base;
  }
  return "en";
}

/** Turn a stored preference ("auto" | "en" | "zh") into a concrete language. */
export function resolve(pref) {
  return SUPPORTED.includes(pref) ? pref : browserLang();
}

async function dictionary(name) {
  if (!cache.has(name)) {
    cache.set(name, fetch(`/assets/lang/${name}.json`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : {}))
      .catch(() => ({})));
  }
  const data = await cache.get(name);
  return data && typeof data === "object" ? data : {};
}

/** Load the dictionary for a preference; missing keys fall back to English. */
export async function setLang(pref) {
  const want = resolve(pref);
  const [main, english] = await Promise.all([dictionary(want), want === "en" ? null : dictionary("en")]);
  active = main;
  fallback = english || main;
  current = want;
  document.documentElement.lang = want === "zh" ? "zh-CN" : "en";
  document.documentElement.dataset.lang = want;
  return want;
}

export function lang() {
  return current;
}

/** Look up a key; {name} placeholders are filled from vars when given. */
export function t(key, vars) {
  const text = active[key] ?? fallback[key] ?? key;
  if (!vars) return text;
  return String(text).replace(/\{(\w+)\}/g, (match, name) =>
    (Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match));
}

/** Fill static shell nodes marked with data-i18n* attributes. */
export function applyStatic(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
  root.querySelectorAll("[data-i18n-title]").forEach((node) => { node.title = t(node.dataset.i18nTitle); });
  root.querySelectorAll("[data-i18n-aria]").forEach((node) => { node.setAttribute("aria-label", t(node.dataset.i18nAria)); });
  root.querySelectorAll("[data-i18n-ph]").forEach((node) => { node.placeholder = t(node.dataset.i18nPh); });
}
