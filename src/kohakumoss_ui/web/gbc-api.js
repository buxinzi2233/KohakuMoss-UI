/* GBC v1 transport: header-only authentication; never retry a mutation. */
import { TOKEN } from "./api.js";
export const API_VERSION = "gbc-ui-v1";
export const PREFIX = "/api/gbc/v1/";
export class GbcError extends Error {
  constructor(code, message = code, details = {}, requestId = null) {
    super(message); this.name = "GbcError"; this.code = code;
    this.details = details; this.requestId = requestId;
  }
}
export function draftRef(value) {
  const ref = value?.draftRef || value;
  if (!ref || !/^draft-[a-f0-9]{32}$/.test(ref.draftId) || !Number.isSafeInteger(ref.revision) || ref.revision < 1 || !/^sha256:[a-f0-9]{64}$/.test(ref.effectiveHash)) {
    throw new GbcError("VERSION_CONFLICT");
  }
  return { draftId: ref.draftId, revision: ref.revision, effectiveHash: ref.effectiveHash };
}
export function assertDraft(actual, expected) {
  if (JSON.stringify(draftRef(actual)) !== JSON.stringify(draftRef(expected))) throw new GbcError("VERSION_CONFLICT");
}
export function createGbcApi({ signal, fetcher = fetch, token = TOKEN } = {}) {
  async function request(route, query, body) {
    if (!/^[a-z][a-z/-]*$/.test(route)) throw new GbcError("INVALID_REQUEST");
    const requestId = body === undefined ? null : `ui-${crypto.randomUUID()}`;
    const search = new URLSearchParams();
    Object.entries(query || {}).forEach(([key, value]) => { if (value !== undefined && value !== null) search.set(key, String(value)); });
    let response, payload;
    try {
      response = await fetcher(PREFIX + route + (search.size ? `?${search}` : ""), {
        method: body === undefined ? "GET" : "POST", signal, cache: "no-store", credentials: "omit",
        headers: { "X-UI-Token": token, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
        ...(body === undefined ? {} : { body: JSON.stringify({ ...body, apiVersion: API_VERSION, requestId }) }),
      });
      payload = await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new GbcError(requestId ? "OUTCOME_UNKNOWN" : "NETWORK_ERROR", "", {}, requestId);
    }
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (payload?.apiVersion !== API_VERSION) throw new GbcError("VERSION_INCOMPATIBLE", "", {}, requestId);
    if (!response.ok || payload.ok !== true) {
      throw new GbcError(payload.error?.code || "PROTOCOL_ERROR", payload.error?.message, payload.error?.details, requestId);
    }
    if (!payload.data || typeof payload.data !== "object" || Array.isArray(payload.data)) throw new GbcError("PROTOCOL_ERROR");
    return payload.data;
  }
  return { get: (route, query = {}) => request(route, query), post: (route, body = {}) => request(route, {}, body) };
}

export function nextPageOffset(page, current, maximum = Number.MAX_SAFE_INTEGER) {
  const next = page.nextOffset;
  if (next === null) return null;
  if (!Number.isSafeInteger(next) || next <= current || next > maximum) throw new GbcError("PROTOCOL_ERROR");
  return next;
}
export function countText(value) { return value === null || value === undefined ? "—" : String(value); }
export function errorText(error, t) {
  const known = new Set(["OUTCOME_UNKNOWN", "NETWORK_ERROR", "VERSION_INCOMPATIBLE", "AUTH_REQUIRED", "PERMISSION_DENIED", "DEPENDENCY_MISSING", "VALIDATION_FAILED", "VERSION_CONFLICT", "SOURCE_CHANGED", "BUSY", "RECOVERY_REQUIRED", "UNSUPPORTED_CAPABILITY", "NOT_FOUND", "INVALID_REQUEST"]);
  const code = known.has(error.code) ? error.code : "OTHER";
  return `${t(`gbc.error.${code}`)}${error.code ? ` [${error.code}]` : ""}${error.message && error.message !== error.code ? ` · ${error.message}` : ""}${error.requestId ? ` · requestId: ${error.requestId}` : ""}`;
}
