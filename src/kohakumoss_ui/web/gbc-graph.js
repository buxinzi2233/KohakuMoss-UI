/* Validated graph DTOs are data, never HTML. Selection uses array indexes so the
 * legitimate empty-string root ID remains distinct from no selection. */
import { esc } from "./format.js";
import { GbcError, errorText } from "./gbc-api.js";

export function overlayBox(node) {
  const b = node.displayBbox;
  if (!b || ![b.left, b.top, b.right, b.bottom].every(Number.isFinite) || b.left < 0 || b.top < 0 || b.right > 1 || b.bottom > 1 || b.right <= b.left || b.bottom <= b.top) return null;
  return { left: b.left * 100, top: b.top * 100, width: (b.right - b.left) * 100, height: (b.bottom - b.top) * 100 };
}

export function mountGraph(host, ctx, { media } = {}) {
  const tr = (key) => ctx.t(`gbc.${key}`);
  let disposed = false, sequence = 0, objectUrl = null, controller = null;
  let graph = null, selected = 0, zoom = 1;
  const release = () => { controller?.abort(); controller = null; if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl = null; };
  function paintSelection(focus = false) {
    host.querySelectorAll("[data-node]").forEach((el) => {
      const on = Number(el.dataset.node) === selected;
      el.setAttribute("aria-pressed", String(on));
    });
    const node = graph?.nodes?.[selected];
    const detail = host.querySelector("[data-node-detail]");
    if (detail) detail.textContent = node ? JSON.stringify({ id: node.id, label: node.label, bbox: node.bbox, displayBbox: node.displayBbox, outside: node.outside, descriptions: node.descriptions }, null, 2) : tr("empty");
    host.querySelectorAll("[data-edge]").forEach((el) => {
      const edge = graph.edges[Number(el.dataset.edge)];
      el.classList.toggle("gbc-linked", node != null && (edge.source === node.id || edge.target === node.id));
    });
    if (focus) host.querySelector(`[data-node-list] [data-node="${selected}"]`)?.focus();
  }
  async function show(row, context = {}) {
    const seq = ++sequence; release(); graph = row?.graph || null; selected = 0; zoom = 1;
    if (!graph) { host.innerHTML = `<p class="hint">${esc(tr("graph.empty"))}</p>`; return; }
    const nodes = graph.nodes || [], edges = graph.edges || [];
    const [width, height] = graph.img_size || [1, 1];
    const ratio = Number.isFinite(width / height) && width > 0 && height > 0 ? width / height : 1;
    host.innerHTML = `<div class="gbc-toolbar"><h3>${esc(tr("graph.title"))}</h3><label>${esc(tr("graph.zoom"))}<input data-zoom type="range" min="1" max="4" step="0.25" value="1"></label><span class="hint">${esc(tr("graph.keys"))}</span></div>
      <div class="gbc-graph-grid"><div class="gbc-image-scroll" tabindex="0" aria-label="${esc(tr("graph.title"))}"><div class="gbc-image" style="aspect-ratio:${ratio}"><img hidden alt="${esc(tr("graph.image"))}">${nodes.map((node, i) => {
        const b = overlayBox(node); return b ? `<button type="button" class="gbc-bbox" data-node="${i}" style="left:${b.left}%;top:${b.top}%;width:${b.width}%;height:${b.height}%" aria-label="${esc(`${node.id}: ${node.label}`)}" aria-pressed="false"><span>${esc(node.id === "" ? tr("graph.root") : node.id)}</span></button>` : "";
      }).join("")}</div><p data-media-status class="hint" role="status">${esc(tr(media ? "graph.mediaLoading" : "graph.mediaUnavailable"))}</p></div>
      <div class="gbc-node-pane"><div data-node-list class="gbc-node-list" aria-label="${esc(tr("graph.nodes"))}">${nodes.map((node, i) => `<button class="btn" type="button" data-node="${i}" aria-pressed="false">${esc(node.id === "" ? tr("graph.root") : node.id)} · ${esc(node.label)}</button>`).join("")}</div><pre data-node-detail class="code gbc-json"></pre>
      <h4>${esc(tr("graph.edges"))}</h4><ul class="gbc-edges">${edges.map((edge, i) => `<li data-edge="${i}"><button type="button" class="btn" data-edge-target="${i}">${esc(edge.source === "" ? tr("graph.root") : edge.source)} → ${esc(edge.target === "" ? tr("graph.root") : edge.target)}</button> ${esc(edge.text)}</li>`).join("")}</ul></div></div>`;
    paintSelection();
    if (!media) return;
    controller = new AbortController();
    try {
      const blob = await media(row, context, controller.signal);
      if (disposed || seq !== sequence) return;
      if (!(blob instanceof Blob) || !/^image\/(png|jpeg|webp)$/.test(blob.type)) throw new GbcError("PROTOCOL_ERROR");
      objectUrl = URL.createObjectURL(blob);
      const img = host.querySelector("img");
      const events = { once: true, signal: controller.signal };
      img.addEventListener("load", () => {
        if (disposed || seq !== sequence) return;
        img.hidden = false;
        host.querySelector("[data-media-status]").textContent = tr("graph.original");
      }, events);
      img.addEventListener("error", () => {
        if (disposed || seq !== sequence) return;
        img.hidden = true; img.removeAttribute("src"); release();
        host.querySelector("[data-media-status]").textContent = tr("graph.decodeFailed");
      }, events);
      img.src = objectUrl;
    } catch (error) {
      if (!disposed && seq === sequence && error.name !== "AbortError") host.querySelector("[data-media-status]").textContent = errorText(error, ctx.t);
    }
  }
  function click(event) {
    const node = event.target.closest("[data-node]");
    if (node && host.contains(node)) { selected = Number(node.dataset.node); paintSelection(); }
    const edge = event.target.closest("[data-edge-target]");
    if (edge && graph) { selected = graph.nodes.findIndex((n) => n.id === graph.edges[Number(edge.dataset.edgeTarget)].target); paintSelection(true); }
  }
  function input(event) {
    if (event.target.matches("[data-zoom]")) { zoom = Number(event.target.value); host.querySelector(".gbc-image").style.width = `${zoom * 100}%`; }
  }
  function key(event) {
    if (!graph || !event.target.closest("[data-node]")) return;
    if (["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft", "Home", "End"].includes(event.key)) {
      event.preventDefault(); const last = graph.nodes.length - 1;
      selected = event.key === "Home" ? 0 : event.key === "End" ? last : (selected + (event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : -1) + last + 1) % (last + 1);
      paintSelection(true);
    }
  }
  host.addEventListener("click", click); host.addEventListener("input", input); host.addEventListener("keydown", key);
  return { show, cleanup() { disposed = true; sequence++; release(); host.removeEventListener("click", click); host.removeEventListener("input", input); host.removeEventListener("keydown", key); } };
}
