/* Settings: three themes, per-theme colour overrides, appearance (wallpaper) layer,
   interface language and the civitai credential block. Every visible string comes
   from the dictionary, never from this file. */
import * as api from "./api.js";
import { esc, when } from "./format.js";
import * as appearance from "./appearance.js";

let themeHandler = null;

const THEME_CARDS = [
  {
    id: "kohaku", title: "KOHAKU",
    desc: "settings.theme.kohaku",
    swatch: ["#f2eee8", "#2e2a26", "#5A4FCF", "#D46B6B", "#4C9989"],
  },
  {
    id: "soul", title: "SOUL",
    desc: "settings.theme.soul",
    swatch: ["#141618", "#1C1F22", "#EBA375", "#91B9EA", "#89C5A2"],
  },
  {
    id: "teletype", title: "TELETYPE",
    desc: "settings.theme.teletype",
    swatch: ["#efe9dc", "#221f1a", "#8f3418", "#57503f", "#b9aa90"],
  },
];

const COLOR_FIELDS = [
  ["canvas", "settings.colour.canvas"],
  ["panel", "settings.colour.panel"],
  ["text", "settings.colour.text"],
  ["border", "settings.colour.border"],
  ["accent", "settings.colour.accent"],
];

function swatchRow(colors) {
  return colors.map((color) => `<i style="background:${color}"></i>`).join("");
}

function themeCard(card, active, t) {
  return `<button type="button" class="theme ${active ? "on" : ""}" data-theme-pick="${card.id}" aria-pressed="${active}">
    <span class="theme-title">${card.title}</span>
    <span class="theme-desc">${esc(t(card.desc))}</span>
    <span class="sw">${swatchRow(card.swatch)}</span>
    <span class="note">${active ? t("settings.theme.active") : t("settings.theme.apply")}</span>
  </button>`;
}

function segment(options, active, attr, label) {
  return `<span class="seg" role="group" aria-label="${esc(label)}">${options.map(([value, optionLabel]) =>
    `<button type="button" ${attr}="${value}" class="${value === active ? "on" : ""}" aria-pressed="${value === active}">${esc(optionLabel)}</button>`).join("")}</span>`;
}

/**
 * Credential state for the civitai block: GET /api/profile carries
 * {username,hasKey,keyHint,lastSync} and never the key itself. Null means the
 * endpoint did not answer (older backend), so the block disables itself instead
 * of throwing.
 */
async function civitaiStatus() {
  try {
    const profile = await api.get("/api/profile");
    const block = profile?.civitai;
    return block && typeof block === "object" ? block : null;
  } catch {
    return null;
  }
}

export async function settingsView(main, ctx) {
  const state = ctx.state;
  const t = ctx.t;
  if (!state.trainer) {
    try {
      state.trainer = (await api.get("/api/state")).trainer;
      ctx.updateRunner(state.runs);
    } catch {
      /* the paths block shows dashes when discovery failed */
    }
  }
  const trainer = state.trainer || {};
  const data = appearance.getAppearance();
  const colors = appearance.effectiveColors();
  const density = localStorage.getItem("km-density") || "relaxed";
  const scale = localStorage.getItem("km-scale") || "1.1";
  const background = data.background;
  const hasImage = Boolean(data.backgroundUrl || background.url);
  // Glass and surface opacity apply to every theme. Teletype only swaps frosted
  // glass for a thin sheet: the alpha thins the paper, blur stays banned.
  // SHEET row copy lives under settings.sheet.note: "teletype thins the controls to vellum;
  // the page keeps the wallpaper" is a dictionary string, never inlined here.
  const isTeletype = data.theme === "teletype";
  const opacityValue = Math.round(data.surfaceOpacity * 100);
  const langPref = data.lang || "auto";
  let creds = await civitaiStatus();
  const cvReady = Boolean(creds);
  const cvName = String(creds?.username || "");
  const cvKeyHint = String(creds?.keyHint || "");
  const cvTail = cvKeyHint.replace(/^\*+/, "");
  const off = cvReady ? "" : " disabled";

  main.innerHTML = `
    <div class="vhead surface"><h2>${esc(t("settings.title"))}</h2><div class="meta">${esc(t("settings.meta"))}</div><div class="acts"><button class="btn pri" id="save">${esc(t("settings.save"))}</button></div></div>
    <div class="box"><h3>${esc(t("settings.theme"))} <b>${esc(t("settings.theme.count"))}</b></h3>
      <div class="themes" role="group" aria-label="${esc(t("settings.theme"))}">
        ${THEME_CARDS.map((card) => themeCard(card, card.id === data.theme, t)).join("")}
      </div>
      <div class="form settings-form" style="border-top:none;margin-top:8px">
        <div class="lab">${esc(t("settings.mode"))}</div><div class="val">${segment([["light", t("settings.mode.light")], ["carbon", t("settings.mode.carbon")]], data.mode, "data-mode-pick", t("settings.mode"))}<span class="hint">${esc(t("settings.mode.hint"))}</span></div>
        <div class="lab">${esc(t("settings.colours"))}</div><div class="val">
          ${COLOR_FIELDS.map(([key, labelKey]) => `<label class="csw" title="${esc(t(labelKey))}"><input type="color" data-color="${key}" value="${colors[key]}"><span>${esc(t(labelKey))}</span></label>`).join("")}
          <button class="btn" id="colorReset">${esc(t("settings.colours.reset"))}</button>
          <span class="hint">${esc(t("settings.colours.hint", { theme: data.theme.toUpperCase() }))}</span>
        </div>
        <div class="lab">${esc(t("settings.contrast"))}</div><div class="val"><span class="hint">${esc(t("settings.contrast.text"))} <b id="ct-text" class="ct-ok">—</b> · ${esc(t("settings.contrast.accent"))} <b id="ct-accent" class="ct-ok">—</b> · <span id="ct-state">${esc(t("settings.contrast.theme"))}</span></span></div>
        <div class="lab">${esc(t("settings.density"))}</div><div class="val">${segment([["relaxed", t("settings.density.relaxed")], ["compact", t("settings.density.compact")]], density, "data-density-pick", t("settings.density"))}<span class="hint">${esc(t("settings.density.hint"))}</span></div>
        <div class="lab">${esc(t("settings.scale"))}</div><div class="val">${segment([["1", "100%"], ["1.1", "110%"], ["1.25", "125%"], ["1.4", "140%"]], scale, "data-scale-pick", t("settings.scale"))}<span class="hint">${esc(t("settings.scale.hint"))}</span></div>
        <div class="lab">${esc(t("settings.language"))}</div><div class="val">${segment([["auto", t("settings.language.auto")], ["en", "EN"], ["zh", "ZH"]], langPref, "data-lang-pick", t("settings.language"))}<span class="hint">${esc(t("settings.language.hint"))}</span></div>
      </div>
    </div>
    <div class="box" style="margin-top:14px"><h3>${esc(t("settings.appearance"))} <b>${esc(t("settings.appearance.sub"))}</b></h3>
      <div class="form settings-form">
        ${isTeletype ? `<div class="lab">${esc(t("settings.sheet"))}</div><div class="val"><span class="hint" id="paperNote">${esc(t("settings.sheet.note"))}</span></div>` : ""}
        <div class="lab">${esc(t("settings.glass"))}</div><div class="val">${segment([["on", t("settings.bool.on")], ["off", t("settings.bool.off")]], data.glass ? "on" : "off", "data-glass", t("settings.glass"))}<span class="hint" id="glassHint">${esc(t("settings.glass.hint"))}</span></div>
        <div class="lab">${esc(t("settings.opacity"))}</div><div class="val"><input class="range" id="opacity" type="range" min="0" max="100" value="${opacityValue}"><span class="hint" id="opacityVal">${opacityValue}%</span></div>
        <div class="lab">${esc(t("settings.bg"))}</div><div class="val">${segment([["on", t("settings.bool.on")], ["off", t("settings.bool.off")]], background.enabled ? "on" : "off", "data-bg", t("settings.bg"))}<span class="hint" id="bgHint">${hasImage ? esc(t("settings.bg.ready")) : esc(t("settings.bg.none"))}</span></div>
        <div class="lab">${esc(t("settings.blur"))}</div><div class="val"><input class="range" id="blur" type="range" min="0" max="30" value="${Math.round(background.blur)}"><span class="hint" id="blurVal">${Math.round(background.blur)}px</span></div>
        <div class="lab">${esc(t("settings.mask"))}</div><div class="val"><input class="range" id="mask" type="range" min="0" max="100" value="${Math.round(background.mask * 100)}"><span class="hint" id="maskVal">${Math.round(background.mask * 100)}%</span></div>
        <div class="lab">${esc(t("settings.url"))}</div><div class="val"><input class="field" id="bgUrl" type="text" value="${esc(background.url)}" placeholder="https://… or data:image/…"><button class="btn" id="bgApply">${esc(t("settings.useUrl"))}</button></div>
        <div class="lab">${esc(t("settings.upload"))}</div><div class="val"><input type="file" id="bgFile" accept="image/png,image/jpeg,image/webp" hidden><button class="btn" id="bgUpload">${esc(t("settings.upload"))}</button><span class="hint">${esc(t("settings.upload.hint"))}</span></div>
        <div class="lab">${esc(t("settings.colours.reset"))}</div><div class="val"><button class="btn dngr" id="bgRemove">${esc(t("settings.removeBg"))}</button></div>
      </div>
    </div>
    <div class="box" style="margin-top:14px"><h3>${esc(t("settings.civitai"))} <b>${esc(t("settings.civitai.sub"))}</b></h3>
      <div class="form settings-form">
        <div class="lab">${esc(t("settings.civitai.username"))}</div><div class="val"><input class="field" id="cvUser" type="text" value="${esc(cvName)}" placeholder="${esc(t("settings.civitai.username.ph"))}" autocomplete="off" spellcheck="false"${off}></div>
        <div class="lab">${esc(t("settings.civitai.key"))}</div><div class="val"><input class="field" id="cvKey" type="password" value="" placeholder="${esc(t("settings.civitai.key.input"))}" aria-describedby="cvKeyHint cvKeyState" autocomplete="off" spellcheck="false"${off}><span class="hint hint-line" id="cvKeyHint">${esc(t("settings.civitai.key.ph"))}</span><span class="hint hint-line" id="cvKeyState">${cvTail ? esc(t("settings.civitai.key.saved", { tail: cvTail })) : esc(t("settings.civitai.key.none"))}</span></div>
        <div class="lab">${esc(t("settings.civitai.actions"))}</div><div class="val"><button class="btn pri" id="cvSave"${off}>${esc(t("common.save"))}</button><button class="btn" id="cvSync"${off}>${esc(t("settings.civitai.sync"))}</button><button class="btn dngr" id="cvClear"${off}>${esc(t("settings.civitai.clear"))}</button></div>
        <div class="lab">${esc(t("settings.civitai.sub"))}</div><div class="val"><span class="hint" id="cvState"></span></div>
      </div>
    </div>
    <div class="box" style="margin-top:14px"><h3>${esc(t("settings.paths"))} <b>${esc(t("settings.paths.sub"))}</b></h3>
      <div class="form settings-form">
        <div class="lab">${esc(t("settings.paths.root"))}</div><div class="val"><span class="field">${esc(trainer.root || "—")}</span><span class="hint">${esc(trainer.version || "")}</span></div>
        <div class="lab">${esc(t("settings.paths.runs"))}</div><div class="val"><span class="field">${esc(trainer.runs_dir || "—")}</span></div>
        <div class="lab">${esc(t("settings.paths.python"))}</div><div class="val"><span class="field">${esc(trainer.python || "—")}</span></div>
        <div class="lab">${esc(t("settings.paths.kogine"))}</div><div class="val"><span class="field">${esc(trainer.kogine || t("settings.paths.notFound"))}</span></div>
      </div>
    </div>
    <div class="box" style="margin-top:14px"><h3>${esc(t("settings.access"))} <b>${esc(t("settings.access.sub"))}</b></h3><div class="dim">${esc(t("settings.access.note"))}</div></div>`;

  const syncCards = () => {
    const now = appearance.getAppearance();
    main.querySelectorAll("[data-theme-pick]").forEach((card) => {
      const active = card.dataset.themePick === now.theme;
      card.classList.toggle("on", active);
      card.setAttribute("aria-pressed", String(active));
      const note = card.querySelector(".note");
      if (note) note.textContent = active ? t("settings.theme.active") : t("settings.theme.apply");
    });
    main.querySelectorAll("[data-mode-pick]").forEach((item) => {
      const active = item.dataset.modePick === now.mode;
      item.classList.toggle("on", active);
      item.setAttribute("aria-pressed", String(active));
    });
  };

  const bgHintText = () => {
    const now = appearance.getAppearance();
    return now.backgroundUrl || now.background.url ? t("settings.bg.ready") : t("settings.bg.none");
  };

  const updateContrast = () => {
    const now = appearance.effectiveColors();
    const text = appearance.contrast(now.text, now.canvas);
    const accent = appearance.contrast(now.accent, now.canvas);
    const textEl = main.querySelector("#ct-text");
    const accentEl = main.querySelector("#ct-accent");
    if (textEl) {
      textEl.textContent = `${text.toFixed(1)}:1`;
      textEl.className = text >= 4.5 ? "ct-ok" : "ct-warn";
    }
    if (accentEl) {
      accentEl.textContent = `${accent.toFixed(1)}:1`;
      accentEl.className = accent >= 4.5 ? "ct-ok" : "ct-warn";
    }
    const stateEl = main.querySelector("#ct-state");
    if (stateEl) stateEl.textContent = appearance.isOverridden() ? t("settings.contrast.overrides") : t("settings.contrast.theme");
  };

  main.querySelectorAll("[data-theme-pick]").forEach((card) => card.addEventListener("click", () => {
    if (card.dataset.themePick === appearance.getAppearance().theme) return;
    appearance.setTheme(card.dataset.themePick);
    ctx.rerender();
  }));

  main.querySelectorAll("[data-mode-pick]").forEach((item) => item.addEventListener("click", () => {
    if (item.dataset.modePick === appearance.getAppearance().mode) return;
    appearance.patch({ mode: item.dataset.modePick }, true);
    ctx.rerender();
  }));

  main.querySelectorAll("[data-lang-pick]").forEach((item) => item.addEventListener("click", async () => {
    if (item.dataset.langPick === (appearance.getAppearance().lang || "auto")) return;
    await ctx.setLanguage(item.dataset.langPick);
  }));

  main.querySelectorAll("[data-density-pick]").forEach((item) => item.addEventListener("click", () => {
    localStorage.setItem("km-density", item.dataset.densityPick);
    document.documentElement.dataset.density = item.dataset.densityPick;
    main.querySelectorAll("[data-density-pick]").forEach((other) => {
      other.classList.toggle("on", other === item);
      other.setAttribute("aria-pressed", String(other === item));
    });
  }));

  main.querySelectorAll("[data-scale-pick]").forEach((item) => item.addEventListener("click", () => {
    localStorage.setItem("km-scale", item.dataset.scalePick);
    document.documentElement.style.setProperty("--scale", item.dataset.scalePick);
    main.querySelectorAll("[data-scale-pick]").forEach((other) => {
      other.classList.toggle("on", other === item);
      other.setAttribute("aria-pressed", String(other === item));
    });
  }));

  main.querySelectorAll("[data-color]").forEach((input) => input.addEventListener("input", () => {
    appearance.setColors({ [input.dataset.color]: input.value }, false);
    updateContrast();
  }));
  const resetColors = main.querySelector("#colorReset");
  if (resetColors) resetColors.addEventListener("click", () => {
    appearance.setColors({ canvas: "", panel: "", text: "", border: "", accent: "" }, true);
    ctx.rerender();
  });
  updateContrast();

  main.querySelectorAll("[data-glass]").forEach((item) => item.addEventListener("click", () => {
    appearance.patch({ glass: item.dataset.glass === "on" }, true);
    main.querySelectorAll("[data-glass]").forEach((other) => {
      other.classList.toggle("on", other === item);
      other.setAttribute("aria-pressed", String(other === item));
    });
  }));

  main.querySelectorAll("[data-bg]").forEach((item) => item.addEventListener("click", () => {
    appearance.patch({ background: { enabled: item.dataset.bg === "on" } }, true);
    main.querySelectorAll("[data-bg]").forEach((other) => {
      other.classList.toggle("on", other === item);
      other.setAttribute("aria-pressed", String(other === item));
    });
    const hint = main.querySelector("#bgHint");
    if (hint) hint.textContent = bgHintText();
  }));

  const bindRange = (id, format, apply) => {
    const input = main.querySelector(`#${id}`);
    const out = main.querySelector(`#${id}Val`);
    if (!input) return;
    input.addEventListener("input", () => {
      const value = Number(input.value);
      if (out) out.textContent = format(value);
      apply(value);
    });
  };
  bindRange("opacity", (v) => `${v}%`, (v) => appearance.patch({ surfaceOpacity: v / 100 }));
  bindRange("blur", (v) => `${v}px`, (v) => appearance.patch({ background: { blur: v } }));
  bindRange("mask", (v) => `${v}%`, (v) => appearance.patch({ background: { mask: v / 100 } }));

  const urlInput = main.querySelector("#bgUrl");
  const applyUrl = () => {
    const value = urlInput.value.trim();
    appearance.patch({ background: { url: value, enabled: Boolean(value) } }, true);
    main.querySelectorAll("[data-bg]").forEach((item) => {
      const active = item.dataset.bg === (value ? "on" : "off");
      item.classList.toggle("on", active);
      item.setAttribute("aria-pressed", String(active));
    });
    const hint = main.querySelector("#bgHint");
    if (hint) hint.textContent = bgHintText();
  };
  main.querySelector("#bgApply").addEventListener("click", applyUrl);
  urlInput.addEventListener("change", applyUrl);

  const fileInput = main.querySelector("#bgFile");
  main.querySelector("#bgUpload").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    try {
      await appearance.uploadBackground(file);
      ctx.toast(t("toast.bgUploaded"), true);
      ctx.rerender();
    } catch (error) {
      ctx.toast(String(error.message || error));
    }
  });

  main.querySelector("#bgRemove").addEventListener("click", async () => {
    try {
      await appearance.removeBackground();
      ctx.toast(t("toast.bgRemoved"), true);
      ctx.rerender();
    } catch (error) {
      ctx.toast(String(error.message || error));
    }
  });

  const cvState = main.querySelector("#cvState");
  const cvButton = (id) => main.querySelector(id);
  const cvFail = (error) => {
    if (!cvState) return;
    cvState.className = "hint ct-warn";
    cvState.textContent = String(error?.message || error || "");
  };
  const renderCreds = () => {
    if (!cvState) return;
    cvState.className = "hint";
    if (!cvReady) {
      cvState.textContent = t("settings.civitai.hint");
      return;
    }
    if (creds?.error) {
      cvState.classList.add("ct-warn");
      cvState.textContent = String(creds.error);
      return;
    }
    const last = Number(creds?.lastSync || 0);
    if (!last) {
      cvState.textContent = t("settings.civitai.never");
      return;
    }
    const count = Array.isArray(creds?.models) ? creds.models.length : t("common.none");
    cvState.textContent = t("settings.civitai.last", { when: when(last), count });
  };
  renderCreds();

  if (cvReady) {
    cvButton("#cvSave")?.addEventListener("click", async () => {
      const username = (main.querySelector("#cvUser")?.value || "").trim();
      if (!username) return;
      const save = cvButton("#cvSave");
      if (save) save.disabled = true;
      try {
        const body = { username };
        const key = (main.querySelector("#cvKey")?.value || "").trim();
        // An empty field means "keep the stored key"; the server clears it only
        // when the account name changes, so never send an empty apiKey.
        if (key) body.apiKey = key;
        creds = await api.post("/api/civitai/credentials", body);
        ctx.toast(t("toast.civitaiSaved"), true);
        ctx.rerender();
      } catch (error) {
        cvFail(error);
      } finally {
        if (save) save.disabled = false;
      }
    });

    cvButton("#cvClear")?.addEventListener("click", async () => {
      const clear = cvButton("#cvClear");
      if (clear) clear.disabled = true;
      try {
        creds = await api.remove("/api/civitai/credentials");
        ctx.toast(t("toast.civitaiCleared"), true);
        ctx.rerender();
      } catch (error) {
        cvFail(error);
      } finally {
        if (clear) clear.disabled = false;
      }
    });

    cvButton("#cvSync")?.addEventListener("click", async () => {
      const sync = cvButton("#cvSync");
      if (sync) sync.disabled = true;
      try {
        const result = await api.post("/api/civitai/sync", {});
        creds = {
          ...creds,
          lastSync: Number(result?.syncedAt || creds?.lastSync || 0),
          models: Array.isArray(result?.models) ? result.models : [],
          error: result?.error || "",
        };
        renderCreds();
        if (result?.error) ctx.toast(String(result.error));
        else ctx.toast(t("toast.civitaiSynced"), true);
      } catch (error) {
        cvFail(error);
      } finally {
        if (sync) sync.disabled = false;
      }
    });
  }

  main.querySelector("#save").addEventListener("click", () => {
    appearance.patch({}, true);
    ctx.toast(t("toast.settingsSaved"), true);
  });

  const onApplied = () => syncCards();
  if (themeHandler) window.removeEventListener("km-theme-applied", themeHandler);
  themeHandler = onApplied;
  window.addEventListener("km-theme-applied", onApplied);
}
