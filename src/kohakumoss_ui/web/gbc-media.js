/* Confirmed GBC v1 media DTO. No source paths/URLs or token-bearing img URLs. */
import { createGbcApi, GbcError } from "./gbc-api.js";
const MAX_BYTES = 4 * 1024 * 1024;
const MAX_BASE64 = 5592408;
const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];

export function mediaBlob(media, graph, size = "detail") {
  if (!media || media.mimeType !== "image/png" || media.encoding !== "base64" || media.orientation !== "stored-image" || media.size !== size || !["thumb", "detail"].includes(size)) throw new GbcError("PROTOCOL_ERROR");
  const dimensions = [media.width, media.height, media.sourceWidth, media.sourceHeight];
  if (!dimensions.every((v) => Number.isSafeInteger(v) && v > 0) || media.sourceWidth * media.sourceHeight > 40000000 || media.width > media.sourceWidth || media.height > media.sourceHeight || Math.max(media.width, media.height) > (size === "thumb" ? 384 : 1600)) throw new GbcError("PROTOCOL_ERROR");
  if (graph?.img_size?.[0] !== media.sourceWidth || graph?.img_size?.[1] !== media.sourceHeight) throw new GbcError("SOURCE_CHANGED");
  // Thumbnail rounding may differ by one pixel; larger ratio changes would misalign boxes.
  if (Math.abs(media.width * media.sourceHeight - media.height * media.sourceWidth) > Math.max(media.sourceWidth, media.sourceHeight)) throw new GbcError("PROTOCOL_ERROR");
  if (typeof media.base64 !== "string" || !media.base64.length || media.base64.length > MAX_BASE64 || media.base64.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(media.base64)) throw new GbcError("PROTOCOL_ERROR");
  let raw;
  try { raw = atob(media.base64); } catch { throw new GbcError("PROTOCOL_ERROR"); }
  if (raw.length > MAX_BYTES || raw.length < 24) throw new GbcError("PROTOCOL_ERROR");
  const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
  if (!PNG_SIGNATURE.every((byte, i) => bytes[i] === byte) || String.fromCharCode(...bytes.slice(12, 16)) !== "IHDR") throw new GbcError("PROTOCOL_ERROR");
  const view = new DataView(bytes.buffer);
  if (view.getUint32(16) !== media.width || view.getUint32(20) !== media.height) throw new GbcError("PROTOCOL_ERROR");
  return new Blob([bytes], { type: "image/png" });
}

export async function loadGraphMedia(row, context, signal, options = {}) {
  const api = createGbcApi({ ...options, signal });
  let route, query;
  if (context.kind === "dataset") {
    if (!context.dataset?.datasetId || !context.dataRuntimeId || typeof row.shard !== "string" || typeof row.key !== "string") throw new GbcError("INVALID_REQUEST");
    route = "datasets/media";
    query = { datasetId: context.dataset.datasetId, dataRuntimeId: context.dataRuntimeId, shard: row.shard, key: row.key, size: "detail" };
  } else if (context.kind === "artifact") {
    if (!row.artifactId || typeof row.sampleId !== "string" || (context.job?.resultArtifactId && row.artifactId !== context.job.resultArtifactId)) throw new GbcError("INVALID_REQUEST");
    route = "graphs/media";
    query = { artifactId: row.artifactId, sampleId: row.sampleId, size: "detail" };
  } else throw new GbcError("INVALID_REQUEST");
  const result = await api.get(route, query);
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  return mediaBlob(result.media, row.graph);
}
