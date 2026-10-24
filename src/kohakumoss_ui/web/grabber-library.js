/* Disk-backed library. No dependency on the collection task's item cache. */
export function createLibrary({ api, post, esc, gt, media, invalidateMedia, alive, render, notice, ready, capabilities, collecting, tagger, tagPane, clean, annotations = null }) {
  const state = { groups: [], group: "", items: [], offset: 0, total: 0, limit: 60, loading: false, error: "", truncated: false,
    selected: null, mode: "merge", confirm_replace: false, confirm_remote: false, job: { status: "idle" }, stopping: false, submitting: false, known: false };
  let epoch = 0, progressEpoch = 0, disposed = false, polling = false, groupsEpoch = 0, completedJob = "";
  const live = () => !disposed && alive();
  const active = () => ["preparing", "running", "stopping"].includes(state.job.status) || state.submitting;
  const label = key => esc(gt(key));
  const button = (act, key, disabled = false, extra = "") => `<button type="button" class="btn" data-act="library-${act}" ${disabled ? "disabled" : ""} ${extra}>${label(key)}</button>`;
  const check = name => `<label><input type="checkbox" data-scope="library" data-field="${name}" ${state[name] ? "checked" : ""}> ${label(`library.${name}`)}</label>`;
  const supported = () => ready() && capabilities().library === true;
  function pane() {
    if (!ready()) return `<p class="hint">${label("service.hint")}</p>`;
    if (!supported()) return `<p class="hint">${label("library.unsupported")}</p>`;
    if (annotations && capabilities().annotations_v1 === true) return annotations.pane() + retagPane();
    const group = state.groups.find(g => g.name === state.group);
    const model = tagger();
    const detail = state.selected == null ? null : state.items[state.selected];
    // Page loading/failure must not insert geometry above the retained detail.
    const status = `${state.loading ? `<p role="status">${label("library.loading")}</p>` : ""}${state.error ? `<p class="alert" role="alert">${esc(clean(state.error))}</p>` : ""}`;
    return `<p class="hint">${label("library.hint")}</p><div class="form surface"><div class="lab">${label("library.group")}</div><div class="val"><select class="field" data-scope="library" data-field="group" aria-label="${label("library.group")}"><option value="">${label("library.choose")}</option>${state.groups.map(g => `<option value="${esc(g.name)}" ${g.name === state.group ? "selected" : ""}>${esc(g.name)} (${esc(g.image_count)})</option>`).join("")}</select>${button("refresh", "refresh", state.loading)}</div></div>
      ${detail ? "" : status}
      ${!state.groups.length && !state.loading ? `<p>${label("library.empty")}</p>` : ""}${state.truncated ? `<p class="hint">${label("library.truncated")}</p>` : ""}
      ${state.group ? `<div class="box"><p>${label("library.images")} ${esc(group?.image_count ?? state.total)} · ${label("library.tagged")} ${esc(group?.tagged_count ?? 0)}</p>
        ${detail ? `${button("back", "library.back")}<div class="grabber-detail"><h3>${esc(detail.filename)}</h3>${media.markup(detail.image_url, "detail", detail.filename)}${media.original(detail.image_url)}<p class="grabber-wrap">${esc(Array.isArray(detail.tags) ? detail.tags.join(", ") : detail.tags || gt("library.noTags"))}</p></div>` : `<div class="gallery">${state.items.map((item, index) => `<button type="button" class="shot" data-act="library-detail" data-index="${index}">${media.markup(item.image_url, "thumb", item.filename)}<span class="cap">${esc(item.filename)} · ${label(item.has_tags ? "library.hasTags" : "library.noTags")}</span></button>`).join("")}</div>`}
        <div class="acts">${button("prev", detail ? "library.prevImage" : "library.prev", state.loading || (detail ? state.offset + state.selected === 0 : state.offset === 0))}<span>${detail ? esc(state.offset + state.selected + 1) : `${esc(state.total ? state.offset + 1 : 0)}–${esc(Math.min(state.offset + state.items.length, state.total))}`} / ${esc(state.total)}</span>${button("next", detail ? "library.nextImage" : "library.next", state.loading || (detail ? state.offset + state.selected + 1 >= state.total : state.offset + state.limit >= state.total))}</div></div>
        ${detail ? status : ""}
        ${retagPane()}` : ""}`;
  }
  function retagPane() {
    const model = tagger();
    return (annotations && capabilities().annotations_v1 === true ? `<p class="hint">${label("annotation.retagWarning")}</p>${annotations.retagProtected() ? `<p role="alert">${label("annotation.retagProtected")}</p>` : ""}` : "") + (capabilities().retag !== true ? `<p class="hint">${label("library.retagUnsupported")}</p>` : `<div class="box" id="annotation-retag"><h3>${label("library.retag")}</h3><p class="hint">${label("library.backupHint")}</p><p>${label("library.model")} ${esc(model.id === "none" ? gt("library.modelRequired") : `${gt(`option.${model.id}`)} · ${model.config.model_path || model.config.model || "—"}`)}</p>
        <details class="grabber-model-settings" data-details="retag"><summary>${label("library.configure")}</summary><p class="hint">${label("library.independent")}</p>${tagPane(true)}</details>
        <div class="form surface"><div class="lab">${label("library.mode")}</div><div class="val"><select class="field" aria-label="${label("library.mode")}" data-scope="library" data-field="mode">${["merge", "missing", "replace"].map(m => `<option value="${m}" ${m === state.mode ? "selected" : ""}>${label(`library.mode.${m}`)}</option>`).join("")}</select></div></div>
        ${state.mode === "replace" ? `<p>${check("confirm_replace")}</p>` : ""}${model.id === "openai_compatible" ? `<p>${check("confirm_remote")}</p>` : ""}
        ${button("start", "library.start", active() || collecting() || state.loading || !state.known || annotations?.retagProtected())}${button("stop", state.stopping ? "library.stopping" : "library.stop", !active() || state.stopping)}</div>`);
  }
  function progressHtml() {
    const job = state.job;
    if (job.status === "idle") return "";
    return `<div class="box" role="status"><h3>${label("library.retag")} · ${esc(job.group || "")} · ${label(`status.${job.status}`)}</h3><p>${label("library.processed")} ${esc(job.processed || 0)} / ${esc(job.total || 0)} · ${label("library.written")} ${esc(job.written || 0)} · ${label("skipped")} ${esc(job.skipped || 0)} · ${label("failed")} ${esc(job.failed || 0)}</p><p class="grabber-wrap">${esc(clean(job.current || ""))}</p>${state.stopping ? `<p>${label("stop.hint")}</p>` : ""}${job.error ? `<p class="alert">${esc(clean(job.error))}</p>` : ""}${job.backup_dir ? `<p class="grabber-wrap">${label("library.backup")} ${esc(job.backup_dir)}</p>` : ""}<div class="logbox flat">${(job.logs || []).map(line => `<div class="ln">${esc(clean(line))}</div>`).join("")}</div></div>`;
  }
  let groupsLoading = false, itemsLoading = false, groupsTruncated = false, browsing = false;
  function loadingChanged() { state.loading = groupsLoading || itemsLoading; render(); }
  function cancelItems() { epoch++; itemsLoading = false; }
  function leave() {
    annotations?.leave();
    browsing = false; cancelItems(); groupsEpoch++; groupsLoading = false;
    state.loading = false;
  }
  async function load(group = state.group, offset = 0, selectEdge = null) {
    if (!live() || !browsing) return;
    const version = ++epoch;
    const sameGroup = group === state.group;
    const selectedPath = sameGroup && state.selected != null ? state.items[state.selected]?.image_url : null;
    // A new group clears the old group's data; pages within a group commit atomically.
    if (!sameGroup) { state.selected = null; state.items = []; state.total = 0; state.offset = 0; }
    state.group = group; state.error = "";
    state.confirm_replace = false; state.confirm_remote = false;
    if (!group || !supported()) { itemsLoading = false; loadingChanged(); return; }
    itemsLoading = true; loadingChanged();
    const current = () => live() && browsing && version === epoch && state.group === group;
    try {
      let data, total, pageOffset;
      while (true) {
        data = await api.get("/api/grabber/library/items", { group, offset, limit: state.limit });
        if (!current()) return;
        total = Math.max(0, Number(data.total) || 0);
        pageOffset = Math.max(0, Number(data.offset) || 0);
        const lastOffset = Math.max(0, Math.ceil(total / state.limit) - 1) * state.limit;
        // A refresh may discover a deleted last page. Only fetch its valid replacement.
        if (offset > lastOffset && pageOffset > lastOffset) { offset = lastOffset; continue; }
        break;
      }
      const items = (data.items || []).slice(0, state.limit);
      const selected = selectEdge === "first" ? 0 : selectEdge === "last" ? items.length - 1 : selectedPath ? items.findIndex(item => item.image_url === selectedPath) : -1;
      state.items = items; state.total = total; state.offset = pageOffset; state.truncated = groupsTruncated || Boolean(data.truncated);
      state.selected = selected < 0 || selected >= items.length ? null : selected;
    } catch (error) { if (current()) state.error = `${error.message} ${gt("library.retry")}`; }
    finally { if (current()) { itemsLoading = false; loadingChanged(); } }
  }
  async function navigate(direction) {
    if (!live() || !browsing || state.loading) return;
    const detail = state.selected != null && state.items[state.selected];
    if (detail) {
      const index = state.selected + direction, globalIndex = state.offset + index;
      if (globalIndex < 0 || globalIndex >= state.total) return;
      if (index >= 0 && index < state.items.length) { state.selected = index; state.error = ""; render(); }
      else await load(state.group, state.offset + direction * state.limit, direction > 0 ? "first" : "last");
    } else {
      const offset = state.offset + direction * state.limit;
      if (offset < 0 || offset >= state.total) return;
      await load(state.group, offset);
    }
  }
  async function refresh() {
    if (annotations && capabilities().annotations_v1 === true) { if (browsing && !annotations.dirty()) await annotations.refresh(); return; }
    if (!live() || !browsing) return;
    if (!supported()) { render(); return; }
    const version = ++groupsEpoch, selected = state.group, itemVersion = epoch;
    groupsLoading = true; state.error = ""; loadingChanged();
    try {
      const data = await api.get("/api/grabber/library/groups");
      if (!live() || version !== groupsEpoch) return;
      state.groups = data.groups || []; groupsTruncated = Boolean(data.truncated); state.truncated = groupsTruncated;
      if (selected !== state.group || itemVersion !== epoch) return;
      const group = state.groups.some(g => g.name === selected) ? selected : state.groups[0]?.name || "";
      await load(group, group === selected ? state.offset : 0);
    } catch (error) { if (live() && version === groupsEpoch && itemVersion === epoch) state.error = error.message; }
    finally { if (live() && version === groupsEpoch) { groupsLoading = false; loadingChanged(); } }
  }
  async function poll() {
    if (!live() || polling || !ready() || capabilities().retag !== true) return;
    polling = true;
    const version = progressEpoch;
    try {
      const data = await api.get("/api/grabber/library/retag/progress");
      if (!live() || version !== progressEpoch) return;
      state.job = data.job || data; state.known = true;
      if (!active()) state.stopping = false;
      if (["done", "stopped", "error"].includes(state.job.status) && state.job.id !== completedJob) {
        completedJob = state.job.id;
        // Never load a completed job's group over a different group selected meanwhile.
        if (state.group === state.job.group) await refresh();
      }
      render(false);
    } catch (error) { if (live() && version === progressEpoch) { state.known = false; notice(error.message, true); } }
    finally { polling = false; }
  }
  function capture(node) {
    if (node.dataset.scope !== "library") return false;
    const name = node.dataset.field;
    if (["mode", "confirm_replace", "confirm_remote"].includes(name) && !active()) state[name] = node.type === "checkbox" ? node.checked : node.value;
    return true;
  }
  async function change(node) {
    if (node.dataset.scope !== "library") return false;
    if (node.dataset.field === "group") await load(node.value);
    else if (node.dataset.field === "mode") { state.confirm_replace = false; render(); }
    return true;
  }
  async function action(act, target) {
    if (annotations && capabilities().annotations_v1 === true && await annotations.action(act, target)) return true;
    if (!act.startsWith("library-")) return false;
    try {
      if (act === "library-refresh") { if (!state.loading) { invalidateMedia(); await refresh(); } }
      else if (act === "library-next") await navigate(1);
      else if (act === "library-prev") await navigate(-1);
      else if (act === "library-detail") {
        const index = Number(target.dataset.index);
        if (!state.loading && Number.isInteger(index) && state.items[index]) { state.selected = index; render(); }
      }
      else if (act === "library-back") { cancelItems(); state.selected = null; state.error = ""; loadingChanged(); }
      else if (act === "library-stop") {
        if (state.stopping || !active()) return true;
        state.stopping = true; render();
        try { await post("library/retag/stop", { id: state.job.id }); }
        catch (error) { state.stopping = false; throw error; }
        if (live()) { notice("", false, "library"); render(); }
      } else if (act === "library-start") {
        if (!supported() || capabilities().retag !== true) throw Error(gt("library.retagUnsupported"));
        if (collecting() || active() || annotations?.active()) throw Error(gt("alreadyRunning"));
        if (annotations?.retagProtected()) throw Error(gt("annotation.retagProtected"));
        if (!state.group || !state.known) throw Error(gt("library.choose"));
        const model = tagger();
        if (!["local_onnx", "openai_compatible"].includes(model.id)) throw Error(gt("library.modelRequired"));
        if (state.mode === "replace" && !state.confirm_replace) throw Error(gt("library.replaceRequired"));
        if (model.id === "openai_compatible" && !state.confirm_remote) throw Error(gt("remote.required"));
        progressEpoch++; state.submitting = true; render(false);
        try {
          const result = await post("library/retag/start", { group: state.group, mode: state.mode, tag_format: model.format,
            tagger_id: model.id, tagger_config: model.config, confirm_replace: state.confirm_replace, confirm_remote: state.confirm_remote });
          if (!live()) return true;
          progressEpoch++; state.job = result.job; state.stopping = false; state.confirm_replace = false; state.confirm_remote = false;
          notice("", false, "library");
        } finally { state.submitting = false; }
        render();
      }
    } catch (error) { if (live()) { notice(error.message, true, "library"); render(false); } }
    return true;
  }
  return { state, pane, progressHtml, load, refresh, poll, active, capture, change, action,
    enter: async () => { browsing = true; await Promise.all([annotations && capabilities().annotations_v1 === true ? annotations.enter() : refresh(), poll()]); }, leave,
    resetConsent: () => { state.confirm_remote = false; },
    dispose: () => { disposed = true; leave(); progressEpoch++; state.items = []; state.groups = []; } };
}
