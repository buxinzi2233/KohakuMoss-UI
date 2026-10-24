/* Bounded, view-local decoded image reuse. Never retain user blobs or full originals. */
export function createGrabberMedia({ root, esc, gt, previewUrl, originalUrl }) {
  const cache = new Map();
  const limits = { thumb: 60, detail: 1 }; // never keep a page of large detail decodes
  function markup(path, size, alt) {
    const url = previewUrl(path, size);
    if (!url) return `<span class="grabber-noimage">${esc(gt("noPreview"))}</span>`;
    // No src until mounting: parsing replacement HTML must not start duplicate requests.
    return `<span class="grabber-media grabber-media-${size}" data-grabber-media="${esc(url)}" data-alt="${esc(alt || "")}"></span>`;
  }
  function original(path) {
    const url = originalUrl(path);
    return url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(gt("viewOriginal"))}</a>` : "";
  }
  function patch(host, html) {
    const template = document.createElement("template");
    template.innerHTML = html;
    // Keep live geometry intact until the replacement slots are mounted. Removing
    // a connected detail first clamps the scroll container to the collapsed height.
    const previous = [...host.querySelectorAll("[data-grabber-media]")];
    const mounts = [];
    const occurrences = new Map();
    for (const slot of template.content.querySelectorAll("[data-grabber-media]")) {
      const url = slot.dataset.grabberMedia;
      const occurrence = occurrences.get(url) || 0;
      occurrences.set(url, occurrence + 1);
      const key = `${url}#${occurrence}`;
      let media = cache.get(key);
      if (!media) {
        media = slot.cloneNode(false);
        const image = document.createElement("img");
        image.decoding = "async";
        const thumb = slot.classList.contains("grabber-media-thumb");
        image.loading = thumb ? "lazy" : "eager";
        image.width = thumb ? 384 : 1600;
        image.height = thumb ? 384 : 1600;
        image.src = url;
        const error = document.createElement("span");
        error.className = "grabber-preview-error";
        error.setAttribute("role", "status");
        error.textContent = gt("previewFailed");
        error.hidden = true;
        media.append(image, error);
      }
      media.querySelector("img").alt = slot.dataset.alt;
      cache.delete(key);
      cache.set(key, media);
      mounts.push([slot, media]);
    }
    host.replaceChildren(template.content);
    // Only detach from discarded ancestors, so the cache retains no old gallery.
    for (const media of previous) media.remove();
    for (const [slot, media] of mounts) slot.replaceWith(media);
    const kind = media => media.classList.contains("grabber-media-thumb") ? "thumb" : "detail";
    const counts = { thumb: 0, detail: 0 };
    for (const media of cache.values()) counts[kind(media)]++;
    for (const [key, media] of cache) {
      const size = kind(media);
      if (counts[size] > limits[size] && !host.contains(media)) { cache.delete(key); counts[size]--; }
    }
    // An error may have completed while a cached node was detached from the root.
    for (const image of host.querySelectorAll("img")) {
      if (image.complete && image.naturalWidth === 0) failed({ target: image });
    }
  }
  function failed(event) {
    const image = event.target;
    const media = image.closest?.("[data-grabber-media]");
    if (!media || image.tagName !== "IMG" || !root.contains(media)) return;
    image.hidden = true;
    media.querySelector(".grabber-preview-error").hidden = false;
  }
  root.addEventListener("error", failed, true);
  return { markup, original, patch, clear: () => cache.clear(),
    dispose: () => { root.removeEventListener("error", failed, true); cache.clear(); } };
}
