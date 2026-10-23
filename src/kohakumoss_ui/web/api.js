/* API client: token from the printed URL, kept for the tab. */
const params = new URLSearchParams(location.search);
const urlToken = params.get("token") || "";
if (urlToken) sessionStorage.setItem("km-token", urlToken);
export const TOKEN = urlToken || sessionStorage.getItem("km-token") || "";

function withToken(path, query = {}) {
  const search = new URLSearchParams(query);
  if (TOKEN) search.set("token", TOKEN);
  return `${path}?${search.toString()}`;
}

export async function get(path, query = {}) {
  const response = await fetch(withToken(path, query), { cache: "no-store" });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || `${response.status} ${response.statusText}`);
  return payload;
}

export async function post(path, body = {}) {
  const response = await fetch(withToken(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || `${response.status} ${response.statusText}`);
  return payload;
}

export async function postRaw(path, body, contentType = "application/octet-stream") {
  const response = await fetch(withToken(path), {
    method: "POST",
    headers: { "Content-Type": contentType },
    body,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || `${response.status} ${response.statusText}`);
  return payload;
}

export async function remove(path) {
  const response = await fetch(withToken(path), { method: "DELETE" });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.error || `${response.status} ${response.statusText}`);
  return payload;
}

/** Absolute URL for the wallpaper layer; keeps an existing ?v= cache buster. */
export function backgroundUrl(url) {
  if (!url) return "";
  if (/^(https?:|data:)/i.test(url)) return url;
  if (!TOKEN) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}`;
}

export function fileUrl(name, path) {
  return withToken("/api/file", { name, path });
}

export function streamUrl(name) {
  return withToken("/api/events", { name });
}

/** Shell-wide event stream: queue version changes arrive here as {"type":"queue"}. */
export function eventUrl() {
  return withToken("/api/events");
}
