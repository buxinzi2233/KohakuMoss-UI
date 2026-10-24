/* Dedicated envelope transport; never use the legacy string-error wrapper. */
import { TOKEN } from "./api.js";
const routes = {
  annotation: { GET: ["snapshots", "candidates", "documents", "plans", "plans/rows", "jobs", "jobs/rows"], POST: ["snapshots", "bindings/confirm", "bindings/select", "plans", "plans/apply", "jobs/stop", "undo/plans"] },
  translation: { GET: ["settings", "dictionary", "view", "plans", "plans/segments", "jobs", "jobs/segments"], POST: ["settings", "credentials", "dictionary/import", "dictionary/install", "dictionary/update", "plans", "jobs/start", "jobs/stop", "manual"] },
};
export const requestId = () => `req_${crypto.randomUUID().replaceAll("-", "")}`;
const wireObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
const wireKeys = (value, keys) => wireObject(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
export function wireError(status, error = {}) {
  error = wireObject(error) ? error : {};
  return Object.assign(new Error(typeof error.message === "string" ? error.message : `HTTP ${status}`), { status, code: typeof error.code === "string" ? error.code : "UPSTREAM_PROTOCOL", details: wireObject(error.details) ? error.details : {}, retryable: error.retryable === true });
}
function transportError(error) {
  if (error?.name === "AbortError") return Object.assign(wireError(0, { code: "REQUEST_ABORTED", message: "Request aborted" }), { name: "AbortError" });
  if (error?.name === "TimeoutError") return wireError(504, { code: "UPSTREAM_TIMEOUT", message: "Request timed out", retryable: true });
  return wireError(503, { code: "UPSTREAM_UNAVAILABLE", message: "Request unavailable", retryable: true });
}
function validateInput(domain, method, route, payload, needsId = true) {
  if (typeof method !== "string" || !["GET", "POST"].includes(method) || typeof route !== "string" || !routes[domain][method].includes(route))
    throw wireError(400, { code: "INVALID_REQUEST", message: "Invalid annotation route" });
  if (!wireObject(payload) || (method === "GET" && Object.hasOwn(payload, "requestId")) ||
      (method === "POST" && (needsId || Object.hasOwn(payload, "requestId")) &&
        (typeof payload.requestId !== "string" || !/^req_[0-9a-f]{32}$/.test(payload.requestId))))
    throw wireError(400, { code: "INVALID_REQUEST", message: "Invalid annotation payload" });
}
async function request(domain, method, route, payload, signal) {
  validateInput(domain, method, route, payload);
  const prefix = domain === "annotation" ? "/api/grabber/annotations/" : "/api/translation/";
  let query, body;
  try {
    query = method === "GET" ? `?${new URLSearchParams(payload)}` : "";
    body = method === "POST" ? JSON.stringify(payload) : undefined;
  } catch { throw wireError(400, { code: "INVALID_REQUEST", message: "Invalid annotation payload" }); }
  let response;
  try {
    response = await fetch(prefix + route + query, { method, cache: "no-store", credentials: "same-origin", signal,
      headers: { "X-UI-Token": TOKEN, ...(method === "POST" ? { "Content-Type": "application/json" } : {}) },
      ...(method === "POST" ? { body } : {}) });
  } catch (error) { throw transportError(error); }
  let envelope;
  try { envelope = await response.json(); }
  catch (error) {
    if (error?.name === "SyntaxError") throw wireError(502);
    throw transportError(error);
  }
  if (!response.ok && response.status >= 400 && wireKeys(envelope, ["ok", "error"]) && envelope.ok === false &&
      wireKeys(envelope.error, ["message", "code", "details", "retryable"]) &&
      typeof envelope.error.message === "string" && typeof envelope.error.code === "string" && envelope.error.code.length > 0 &&
      wireObject(envelope.error.details) && typeof envelope.error.retryable === "boolean") throw wireError(response.status, envelope.error);
  // Every fixed endpoint returns an object. Nested domain validation stays with
  // the backend/controllers; do not invent extra fields or redact source text.
  if (!response.ok || !wireKeys(envelope, ["ok", "data"]) || envelope.ok !== true || !wireObject(envelope.data)) throw wireError(502);
  return envelope;
}
export const requestAnnotation = (method, route, payload = {}) => request("annotation", method, route, payload);
export const requestTranslation = (method, route, payload = {}) => request("translation", method, route, payload);
/* A view-local client can abort reads on cleanup without aborting accepted writes.
   A failed/unknown POST retains its requestId for an explicit same-payload retry. */
export function createAnnotationClient() {
  const reads = new Set(), uncertain = new Map();
  let disposed = false;
  async function call(domain, method, route, payload = {}) {
    if (disposed) throw transportError({ name: "AbortError" });
    validateInput(domain, method, route, payload, false);
    let key;
    try { key = `${domain}:${route}:${JSON.stringify(payload)}`; }
    catch { throw wireError(400, { code: "INVALID_REQUEST", message: "Invalid annotation payload" }); }
    const controller = method === "GET" ? new AbortController() : null;
    if (controller) reads.add(controller);
    const body = method === "POST" ? { ...payload, requestId: payload.requestId || uncertain.get(key) || requestId() } : payload;
    if (method === "POST") uncertain.set(key, body.requestId);
    try {
      const result = await request(domain, method, route, body, controller?.signal);
      uncertain.delete(key);
      return result;
    } catch (error) {
      if (error.status && error.status < 500 && !["BUSY", "RECOVERY_REQUIRED"].includes(error.code)) uncertain.delete(key);
      throw error;
    } finally { if (controller) reads.delete(controller); }
  }
  return { annotation: (m, r, p) => call("annotation", m, r, p), translation: (m, r, p) => call("translation", m, r, p),
    dispose() { disposed = true; for (const controller of reads) controller.abort(); reads.clear(); uncertain.clear(); } };
}
