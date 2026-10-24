/* Offline synthetic annotation/translation workbench. Pure memory: no fixture file
 * is written, no download directory is read and no outbound request is made.
 * node tests/annotation_browser.mjs [port]
 * node tests/annotation_browser.mjs --smoke
 * Everything not listed here fails closed with a structured error.
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, sep, extname } from "node:path";
import assert from "node:assert/strict";
import { docs, candidates, images, id, hash } from "./annotation_fixture.mjs";
import { betaImages, betaDoc } from "./annotation_workbench_fixture.mjs";

const web = resolve(fileURLToPath(new URL("../src/kohakumoss_ui/web/", import.meta.url)));
const dictionary = JSON.parse(await readFile(resolve(web, "lang/en.json"), "utf8"));
const groups = { alpha: images, beta: betaImages };
const plainFormats = new Set(["txt", "caption"]);
const structuredFormats = new Set(["json", "jsonl"]);
const clone = value => structuredClone(value);
const byteLength = value => Buffer.byteLength(value, "utf8");
const now = () => 1;
let serial = 2000, docSerial = 700, verSerial = 700, hashSerial = 700, segmentSerial = 900000;
let failDocument = null;

const snapshots = new Map(), plans = new Map(), jobs = new Map();
const translationPlans = new Map(), translationStores = new Map();
const settings = { configVersion: null, endpoint: "", model: "", sourceLanguage: "en", targetLanguage: "zh-CN",
  timeoutSeconds: 30, maxOutputTokens: 2048, temperature: 0, concurrency: 2, enabled: false, hasKey: false, keyHint: "" };
const dictionaryState = { status: "missing", dictionaryVersion: null, tableName: null, rows: 0, ref: null,
  repositoryUrl: "https://example.invalid/dictionary", license: "MIT", error: null };
const DICTIONARY_VERSION = `dict_${"c".repeat(64)}`;

/* ---------------------------------------------------------------- doc helpers */
function bindingFor(doc, item) {
  return doc?.bindings.find(b => b.bindingId === item.bindingId && b.itemIds.includes(item.itemId))
    || doc?.bindings.find(b => b.itemIds.includes(item.itemId)) || null;
}
function docForItem(item) {
  for (const candidate of item.candidates || []) {
    const doc = docs.get(candidate.documentId);
    if (doc && candidate.bindingIds.includes(item.bindingId)) return doc;
  }
  return null;
}
function fieldsFor(doc, binding) {
  if (!doc || !binding) return [];
  return doc.fields.filter(f => binding.recordIds.includes(f.recordId)
    && binding.spec.fields.some(pointer => pointer.pointer === f.pointer));
}
function tokenRanges(text, delimiter) {
  const ranges = [];
  const expression = delimiter === "space" ? /\S+/g : /[^,\r\n]+/g;
  let match;
  while ((match = expression.exec(text))) {
    const raw = match[0], left = raw.length - raw.trimStart().length, right = raw.trimEnd().length;
    if (right > left) ranges.push({ text: raw.trim(), start: match.index + left, end: match.index + right });
  }
  return ranges;
}
function paragraphRanges(text) {
  const lines = [];
  let start = 0;
  while (start <= text.length) {
    let stop = start;
    while (stop < text.length && text[stop] !== "\n" && text[stop] !== "\r") stop++;
    lines.push({ start, stop, text: text.slice(start, stop) });
    if (stop >= text.length) break;
    start = text[stop] === "\r" && text[stop + 1] === "\n" ? stop + 2 : stop + 1;
  }
  const output = [];
  let group = null;
  for (const line of lines) {
    if (/^[ \t]*$/.test(line.text)) { if (group) { output.push(group); group = null; } continue; }
    if (!group) group = { start: line.start, end: line.stop, text: line.text };
    else { group.end = line.stop; group.text = text.slice(group.start, line.stop); }
  }
  if (group) output.push(group);
  return output;
}
function pointerGet(record, pointer) {
  if (!pointer) return record;
  return pointer.replace(/^\//, "").split("/").reduce((value, key) => value?.[key], record);
}
function makeSegment(doc, field, ordinal, text, rawRanges, valueRange, elementIndex) {
  return { segmentId: id("seg", ++segmentSerial), recordId: field.recordId, pointer: field.pointer, ordinal, kind: field.kind,
    text, sourceHash: doc.sourceHash, sourceVersion: doc.sourceVersion, generation: doc.generation,
    rawRanges, valueRange, elementIndex };
}
function rebuildPlain(doc) {
  const field = doc.fields[0];
  field.rawRange = { start: 0, end: doc.rawText.length };
  if (field.kind === "tags") {
    field.value = doc.rawText;
    field.segments = tokenRanges(doc.rawText, field.delimiter || "comma").map((range, ordinal) =>
      makeSegment(doc, field, ordinal, range.text, [{ start: range.start, end: range.end }],
        { start: range.start, end: range.end }, null));
  } else {
    field.value = doc.rawText;
    field.segments = paragraphRanges(doc.rawText).map((range, ordinal) =>
      makeSegment(doc, field, ordinal, range.text, [{ start: range.start, end: range.end }],
        { start: range.start, end: range.start + range.text.length }, null));
  }
  field.delimiter = field.kind === "tags" ? (field.delimiter || "comma") : null;
}
function rebuildStructured(doc) {
  const records = doc.format === "jsonl"
    ? doc.rawText.split(/\r\n|\r|\n/).filter(line => line.trim()).map(line => JSON.parse(line))
    : [JSON.parse(doc.rawText)];
  let cursor = 0;
  for (const [index, field] of doc.fields.entries()) {
    const record = doc.format === "json" ? records[0] : records[index];
    const value = pointerGet(record, field.pointer);
    if (value === undefined) throw Object.assign(new Error("fixture: pointer missing"), { code: "INVALID_DOCUMENT" });
    const token = JSON.stringify(value);
    const start = doc.rawText.indexOf(token, cursor);
    if (start < 0) throw Object.assign(new Error("fixture: serialized field not found"), { code: "INVALID_DOCUMENT" });
    field.value = value; field.rawRange = { start, end: start + token.length }; cursor = start + token.length;
    if (field.type === "string_array") {
      let inner = start + 1;
      field.segments = value.map((text, ordinal) => {
        const element = JSON.stringify(text), at = doc.rawText.indexOf(element, inner);
        inner = at + element.length;
        return makeSegment(doc, field, ordinal, text, [{ start: at + 1, end: at + element.length - 1 }],
          { start: 0, end: text.length }, ordinal);
      });
    } else {
      field.segments = [makeSegment(doc, field, 0, value, [{ start: start + 1, end: start + token.length - 1 }],
        { start: 0, end: value.length }, null)];
    }
  }
}
function publishCandidate(doc) {
  for (const list of candidates.values()) for (const candidate of list) if (candidate.documentId === doc.documentId)
    Object.assign(candidate, { path: doc.path, format: doc.format, bytes: doc.bytes, sourceHash: doc.sourceHash, sourceVersion: doc.sourceVersion });
}
function commitRaw(doc, rawText) {
  doc.rawText = rawText; doc.bytes = byteLength(rawText);
  doc.generation += 1; doc.sourceVersion = id("ver", ++verSerial); doc.sourceHash = hash(++hashSerial);
  if (structuredFormats.has(doc.format)) rebuildStructured(doc); else rebuildPlain(doc);
  publishCandidate(doc);
}
function serializePlain(field, value) {
  if (Array.isArray(value)) return value.join(field.delimiter === "space" ? " " : ", ");
  return value;
}
function patchStructured(doc, updates) {
  let raw = doc.rawText;
  for (const update of [...updates].sort((a, b) => b.field.rawRange.start - a.field.rawRange.start))
    raw = raw.slice(0, update.field.rawRange.start) + JSON.stringify(update.value) + raw.slice(update.field.rawRange.end);
  return raw;
}
function afterText(doc, updates) {
  if (!updates.length) return doc.rawText;
  if (plainFormats.has(doc.format)) return serializePlain(doc.fields[0], updates[0].value);
  return patchStructured(doc, updates);
}
function sameValue(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

/* ------------------------------------------------------------ operation model */
function evaluateField(field, operation) {
  const tags = field.kind === "tags";
  const tokens = tags ? (field.type === "string_array" ? [...field.value] : tokenRanges(field.value, field.delimiter || "comma").map(r => r.text)) : null;
  const text = tags ? null : String(field.value ?? "");
  const op = operation.op;
  if (op === "clear") return { value: tags ? (field.type === "string_array" ? [] : "") : "", matched: 0, changed: 1, duplicate: 0 };
  if (op === "normalize_newlines") {
    const newline = operation.newline;
    const next = text.replace(/\r\n|\r|\n/g, newline === "crlf" ? "\r\n" : "\n");
    return { value: next, matched: 0, changed: next === text ? 0 : 1, duplicate: 0, code: next === text ? "NO_CHANGE" : null };
  }
  if (tags) {
    if (op === "tag_add") {
      const list = [...tokens], inputs = operation.tokens || [], seen = new Set();
      let matched = 0, duplicate = 0, changed = 0;
      for (const token of inputs) matched += list.filter(value => value === token).length;
      for (const token of inputs) {
        if (seen.has(token)) { duplicate++; continue; }
        seen.add(token);
        if (list.includes(token)) duplicate++;
        else { list.push(token); changed++; }
      }
      return { value: list, matched, changed, duplicate, code: changed ? null : "NO_CHANGE" };
    }
    if (op === "tag_remove") {
      const remove = new Set(operation.tokens || []), next = tokens.filter(token => !remove.has(token));
      return { value: next, matched: tokens.length - next.length, changed: tokens.length - next.length, duplicate: 0,
        code: next.length === tokens.length ? "NO_MATCH" : null };
    }
    if (op === "tag_replace") {
      let matched = 0, changed = 0;
      const next = tokens.map(token => {
        if (token !== operation.match) return token;
        matched++; if (operation.replacement !== operation.match) changed++;
        return operation.replacement;
      });
      return { value: next, matched, changed, duplicate: 0, code: changed ? null : "NO_CHANGE" };
    }
    if (op === "tag_dedupe") {
      const seen = new Set(), next = [];
      for (const token of tokens) { if (seen.has(token)) continue; seen.add(token); next.push(token); }
      const removed = tokens.length - next.length;
      return { value: next, matched: removed, changed: removed, duplicate: removed, code: removed ? null : "NO_CHANGE" };
    }
    if (op === "tag_set") {
      const next = operation.tokens || [];
      return { value: next, matched: 0, changed: sameValue(next, tokens) ? 0 : 1, duplicate: 0,
        code: sameValue(next, tokens) ? "NO_CHANGE" : null };
    }
    return { skip: true, code: "NO_MATCH" };
  }
  if (op === "nl_prepend" || op === "nl_append") {
    const separator = operation.separator ?? "\n";
    const next = !text ? operation.text : op === "nl_prepend" ? `${operation.text}${separator}${text}` : `${text}${separator}${operation.text}`;
    return { value: next, matched: 0, changed: next === text ? 0 : 1, duplicate: 0, code: next === text ? "NO_CHANGE" : null };
  }
  if (op === "nl_replace" || op === "nl_delete") {
    const match = operation.match;
    if (!match) return { skip: true, code: "INVALID_OPERATION" };
    const occurrences = text.split(match).length - 1;
    const replacement = op === "nl_delete" ? "" : operation.replacement ?? "";
    const next = text.split(match).join(replacement);
    return { value: next, matched: occurrences, changed: next === text ? 0 : 1, duplicate: 0, code: next === text ? "NO_CHANGE" : null };
  }
  if (op === "whole_text") {
    const next = operation.text;
    return { value: next, matched: 0, changed: next === text ? 0 : 1, duplicate: 0, code: next === text ? "NO_CHANGE" : null };
  }
  return { skip: true, code: "NO_MATCH" };
}

/* --------------------------------------------------------------- plan helpers */
function pageOf(snapshot, offset) {
  return { snapshotId: snapshot.snapshotId, libraryId: snapshot.libraryId, group: snapshot.group, filters: snapshot.filters,
    total: snapshot.total, offset, limit: snapshot.limit, items: snapshot.items.slice(offset, offset + snapshot.limit),
    truncated: snapshot.truncated, createdAt: snapshot.createdAt, expiresAt: snapshot.expiresAt };
}
function filteredItems(group, filters = {}) {
  let list = [...(groups[group] || [])];
  const format = filters.format || "any", status = filters.status || "any", search = String(filters.search || "").toLowerCase();
  if (format !== "any") list = list.filter(item => (item.candidates || []).some(candidate => candidate.format === format));
  if (status !== "any") list = list.filter(item => item.annotationStatus === status);
  if (search) list = list.filter(item => item.imagePath.toLowerCase().includes(search));
  return list;
}
function resolveScope(scope) {
  const snapshot = snapshots.get(scope?.snapshotId);
  if (!snapshot) throw wireError(404, "NOT_FOUND");
  if (scope.mode === "page_selected") return { snapshot, picked: snapshot.items.filter(item => (scope.itemIds || []).includes(item.itemId)) };
  if (scope.mode === "page_all") { const offset = scope.pageOffset || 0; return { snapshot, picked: snapshot.items.slice(offset, offset + 60) }; }
  if (scope.mode === "all_filtered") return { snapshot, picked: [...snapshot.items] };
  throw wireError(400, "INVALID_REQUEST");
}
function planRows(scope, operation, missingPolicy = "skip", selections = null) {
  const { picked } = resolveScope(scope);
  const rows = [], internals = [];
  const summary = { images: picked.length, documents: 0, rows: 0, fields: 0, matchedOccurrences: 0,
    changedOccurrences: 0, duplicateOccurrences: 0, ready: 0, skipped: 0, errors: 0 };
  if (operation.op === "create") {
    if (picked.length !== 1) throw wireError(422, "INVALID_OPERATION");
    const item = picked[0];
    if (item.annotationStatus !== "missing" && docForItem(item)) throw wireError(422, "INVALID_OPERATION");
    const documentId = id("doc", ++docSerial);
    const format = operation.spec?.format === "caption" ? "caption" : operation.spec?.format === "txt" ? "txt" : operation.spec?.format;
    if (!["txt", "caption", "json"].includes(format)) throw wireError(422, "INVALID_OPERATION");
    const kind = operation.spec?.kind === "tags" ? "tags" : "nl";
    const field = { recordId: id("rec", ++serial), pointer: format === "json" ? (kind === "tags" ? "/tags" : "/caption") : "",
      type: format === "json" && kind === "tags" ? "string_array" : "string", kind, delimiter: kind === "tags" ? (operation.spec?.delimiter || "comma") : null };
    const initial = operation.initialValue;
    const rawText = format === "json" ? JSON.stringify(kind === "tags" ? { tags: initial } : { caption: initial })
      : serializePlain(field, initial);
    const row = { documentId, path: operation.path, itemIds: [item.itemId], beforeHash: null, afterHash: hash(++hashSerial),
      beforeVersion: null, bindingRevisions: [], action: "create", status: "ready", code: null,
      matchedOccurrences: 0, changedOccurrences: 1, duplicateOccurrences: 0, diff: { before: null, after: rawText } };
    rows.push(row);
    internals.push({ row, kind: "create", item, documentId, path: operation.path, spec: operation.spec, format, field, rawText,
      initialValue: initial, itemBefore: { candidates: clone(item.candidates), bindingId: item.bindingId, annotationStatus: item.annotationStatus } });
    summary.documents = 1; summary.rows = 1; summary.fields = 1; summary.ready = 1; summary.changedOccurrences = 1;
    return { rows, internals, summary };
  }
  const grouped = new Map();
  for (const item of picked) {
    const doc = docForItem(item);
    const binding = doc ? bindingFor(doc, item) : null;
    const selection = selections?.find(entry => entry.itemId === item.itemId);
    if (!doc || !binding) {
      const missing = item.annotationStatus === "missing" || !doc;
      const status = missing ? (missingPolicy === "error" ? "error" : "skip") : "error";
      const row = { documentId: null, path: null, itemIds: [item.itemId], beforeHash: null, afterHash: null, beforeVersion: null,
        bindingRevisions: [], action: "noop", status, code: missing ? "MISSING_ANNOTATION" : "SCHEMA_REQUIRED",
        matchedOccurrences: 0, changedOccurrences: 0, duplicateOccurrences: 0, diff: { before: null, after: null } };
      rows.push(row); internals.push({ row, kind: "noop" });
      summary.rows++; if (status === "error") summary.errors++; else summary.skipped++;
      continue;
    }
    if (doc.readOnly || !binding) {
      const row = { documentId: doc.documentId, path: doc.path, itemIds: [item.itemId], beforeHash: doc.sourceHash,
        afterHash: null, beforeVersion: doc.sourceVersion, bindingRevisions: [{ bindingId: binding.bindingId, bindingRevision: binding.bindingRevision }],
        action: "noop", status: "error", code: "SCHEMA_REQUIRED", matchedOccurrences: 0, changedOccurrences: 0,
        duplicateOccurrences: 0, diff: { before: doc.rawText, after: doc.rawText } };
      rows.push(row); internals.push({ row, kind: "noop" }); summary.rows++; summary.errors++; continue;
    }
    if (!grouped.has(doc.documentId)) grouped.set(doc.documentId, { doc, items: [], bindings: new Map(), fields: new Map() });
    const entry = grouped.get(doc.documentId);
    entry.items.push(item); entry.bindings.set(binding.bindingId, binding);
    const candidatesFields = fieldsFor(doc, binding);
    const selected = selection ? candidatesFields.filter(field => selection.recordIds.includes(field.recordId) && selection.pointers.includes(field.pointer)) : candidatesFields;
    if (selection && !selected.length) {
      const row = { documentId: doc.documentId, path: doc.path, itemIds: [item.itemId], beforeHash: doc.sourceHash, afterHash: null,
        beforeVersion: doc.sourceVersion, bindingRevisions: [{ bindingId: binding.bindingId, bindingRevision: binding.bindingRevision }],
        action: "noop", status: "error", code: "INVALID_OPERATION", matchedOccurrences: 0, changedOccurrences: 0,
        duplicateOccurrences: 0, diff: { before: doc.rawText, after: doc.rawText } };
      rows.push(row); internals.push({ row, kind: "noop" }); summary.rows++; summary.errors++; continue;
    }
    for (const field of selected) entry.fields.set(`${field.recordId}:${field.pointer}`, field);
  }
  for (const entry of grouped.values()) {
    const { doc } = entry, updates = [];
    let matched = 0, changed = 0, duplicate = 0, skippedFields = 0, skipCode = null;
    const whole = operation.op === "whole_document";
    let records = null;
    if (whole) {
      try {
        records = structuredFormats.has(doc.format)
          ? (doc.format === "jsonl" ? operation.rawText.split(/\r\n|\r|\n/).filter(line => line.trim()).map(line => JSON.parse(line)) : [JSON.parse(operation.rawText)])
          : null;
      } catch { throw wireError(422, "INVALID_OPERATION"); }
    }
    for (const field of entry.fields.values()) {
      if (whole) {
        const record = records ? records[doc.format === "json" ? 0 : doc.fields.indexOf(field)] : undefined;
        const value = records ? pointerGet(record, field.pointer) : operation.rawText;
        if (value === undefined) throw wireError(422, "INVALID_OPERATION");
        if (!sameValue(value, field.value)) { updates.push({ field, value }); changed += 1; matched += 1; }
        continue;
      }
      const result = evaluateField(field, operation);
      if (result.skip) { skippedFields++; skipCode = skipCode || result.code; continue; }
      matched += result.matched || 0; changed += result.changed || 0; duplicate += result.duplicate || 0;
      if (result.changed) updates.push({ field, value: result.value });
    }
    const deleteFile = operation.op === "delete_file";
    let action = deleteFile ? "delete" : updates.length ? "write" : "noop";
    let status = "ready", code = null;
    if (deleteFile) {
      const binding = [...entry.bindings.values()][0];
      if (entry.items.length !== 1 || doc.bindings.length !== 1 || binding?.spec.mapping.mode !== "sidecar" || binding.itemIds.length !== 1)
        throw wireError(422, "INVALID_OPERATION");
      changed = 1; matched = 0;
    } else if (!updates.length) { status = "skip"; code = skipCode || (skippedFields ? "NO_MATCH" : "NO_CHANGE"); }
    const resultText = deleteFile ? null : status === "ready" ? (whole ? operation.rawText : afterText(doc, updates)) : doc.rawText;
    const row = { documentId: doc.documentId, path: doc.path, itemIds: entry.items.map(item => item.itemId),
      beforeHash: doc.sourceHash, afterHash: deleteFile ? null : status === "ready" ? hash(++hashSerial) : doc.sourceHash,
      beforeVersion: doc.sourceVersion,
      bindingRevisions: [...entry.bindings.values()].map(binding => ({ bindingId: binding.bindingId, bindingRevision: binding.bindingRevision })),
      action, status, code, matchedOccurrences: matched, changedOccurrences: changed, duplicateOccurrences: duplicate,
      diff: { before: doc.rawText, after: resultText } };
    rows.push(row);
    internals.push({ row, kind: deleteFile ? "delete" : status === "ready" ? "write" : "noop", doc, updates,
      bindings: [...entry.bindings.values()], rawTextOverride: whole ? operation.rawText : null });
    summary.rows++; summary.fields += entry.fields.size;
    summary.matchedOccurrences += matched; summary.changedOccurrences += changed; summary.duplicateOccurrences += duplicate;
    if (status === "error") summary.errors++; else if (status === "skip") summary.skipped++; else summary.ready++;
  }
  summary.documents = new Set(rows.filter(row => row.documentId).map(row => row.documentId)).size;
  return { rows, internals, summary };
}

/* ------------------------------------------------------------------ wire bits */
function wireError(status, code, message = `offline fixture: ${code}`) {
  return Object.assign(new Error(message), { status, code, details: {}, retryable: false });
}
function fail(status, code, details = {}) {
  return { status, body: { ok: false, error: { code, message: `offline fixture: ${code}`, details, retryable: false } } };
}
function jobWire(job) {
  return { jobId: job.jobId, planId: job.planId, kind: job.kind, status: job.status, total: job.total, processed: job.processed,
    written: job.written, skipped: job.skipped, failed: job.failed, currentDocumentId: job.currentDocumentId,
    cancelRequested: job.cancelRequested, createdAt: job.createdAt, updatedAt: job.updatedAt, finishedAt: job.finishedAt,
    error: job.error, undoAvailable: job.undoAvailable };
}
function planWire(plan) {
  const { internals, restores, ...wire } = plan;
  return wire;
}
function translationTargetWire(target) {
  const { translation, ...wire } = target;
  return wire;
}
function translationPlanWire(plan) {
  const { targets, ...wire } = plan;
  return wire;
}
function makeJob(kind, planId, total) {
  return { jobId: id("job", ++serial), planId, kind, status: "running", total, processed: 0, written: 0, skipped: 0, failed: 0,
    currentDocumentId: null, cancelRequested: false, createdAt: now(), updatedAt: now(), finishedAt: null, error: null,
    undoAvailable: false, polls: 0, pending: null, rows: [] };
}
function finishJob(job, { written, skipped, failed, rows = [], undoAvailable = false }) {
  job.status = "done"; job.processed = job.total; job.written = written; job.skipped = skipped; job.failed = failed;
  job.finishedAt = now(); job.updatedAt = now(); job.rows = rows; job.undoAvailable = undoAvailable;
}
function completeJob(job) {
  if (job.completed) return;
  job.completed = true;
  if (job.kind === "apply") {
    for (const entry of job.pending.entries) {
      if (entry.row.status !== "ready") continue;
      if (entry.kind === "create") {
        const doc = createDocument(entry);
        entry.snapshot = null; entry.createdDocumentId = doc.documentId; entry.afterVersion = doc.sourceVersion;
      } else if (entry.kind === "delete") {
        entry.snapshot = clone(entry.doc);
        entry.itemSnapshot = entry.doc.bindings.flatMap(binding => binding.itemIds).map(itemId => {
          const item = findItem(itemId);
          return item ? { itemId, candidates: clone(item.candidates), bindingId: item.bindingId, annotationStatus: item.annotationStatus } : null;
        }).filter(Boolean);
        removeDocument(entry.doc);
        for (const itemId of entry.row.itemIds) {
          const item = findItem(itemId); if (!item) continue;
          item.candidates = []; item.bindingId = null; item.annotationStatus = "missing"; candidates.set(itemId, []);
        }
      } else {
        entry.snapshot = clone(entry.doc);
        commitRaw(entry.doc, entry.rawTextOverride ?? afterText(entry.doc, entry.updates));
        entry.afterVersion = entry.doc.sourceVersion; entry.row.afterHash = entry.doc.sourceHash;
      }
    }
    job.undoEntries = job.pending.entries.filter(entry => entry.row.status === "ready").map(entry => ({
      documentId: entry.row.documentId, itemIds: [...entry.row.itemIds], kind: entry.kind,
      snapshot: entry.snapshot ? clone(entry.snapshot) : null, afterVersion: entry.afterVersion || null,
      itemBefore: entry.itemBefore ? clone(entry.itemBefore) : null, itemSnapshot: entry.itemSnapshot ? clone(entry.itemSnapshot) : null,
    }));
    const rows = job.pending.entries.map(entry => ({ documentId: entry.row.documentId,
      status: entry.row.status === "skip" ? "skipped" : entry.row.status === "error" ? "failed" : "written",
      code: entry.row.code, beforeHash: entry.row.beforeHash, afterHash: entry.row.afterHash, beforeVersion: entry.row.beforeVersion,
      afterVersion: entry.afterVersion || (entry.doc ? entry.doc.sourceVersion : null),
      backupId: entry.row.status === "ready" ? `${job.jobId}:${entry.row.documentId}` : null }));
    finishJob(job, { written: rows.filter(row => row.status === "written").length, skipped: rows.filter(row => row.status === "skipped").length,
      failed: rows.filter(row => row.status === "failed").length, rows, undoAvailable: true });
    job.pending.plan.status = "consumed";
  } else if (job.kind === "undo") {
    for (const entry of job.pending.restores) {
      if (entry.row.status !== "ready") continue;
      if (entry.row.action === "delete") {
        const doc = docs.get(entry.row.documentId); if (doc) removeDocument(doc);
        for (const itemId of entry.row.itemIds) {
          const item = findItem(itemId); if (!item) continue;
          Object.assign(item, clone(entry.entry.itemBefore));
          candidates.set(itemId, item.candidates);
        }
      } else if (entry.row.action === "create") {
        restoreDocument(entry.entry, entry.entry.itemSnapshot);
      } else {
        restoreDocument(entry.entry, entry.entry.itemSnapshot);
      }
      entry.row.afterHash = docs.get(entry.row.documentId)?.sourceHash || null;
    }
    const rows = job.pending.restores.map(entry => ({ documentId: entry.row.documentId,
      status: entry.row.status === "skip" ? "skipped" : entry.row.status === "error" ? "failed" : "written",
      code: entry.row.code, beforeHash: entry.row.beforeHash, afterHash: entry.row.afterHash, beforeVersion: entry.row.beforeVersion,
      afterVersion: docs.get(entry.row.documentId)?.sourceVersion || null,
      backupId: entry.row.status === "ready" ? `${job.jobId}:${entry.row.documentId}` : null }));
    finishJob(job, { written: rows.filter(row => row.status === "written").length, skipped: rows.filter(row => row.status === "skipped").length,
      failed: rows.filter(row => row.status === "failed").length, rows, undoAvailable: false });
    job.pending.plan.status = "consumed";
  } else if (job.kind === "translation") {
    let written = 0, skipped = 0;
    const results = [];
    for (const target of job.pending.targets) {
      if (target.status !== "ready") { skipped++; results.push({ ...target.translation, code: target.code || "MISSING_TRANSLATION" }); continue; }
      const doc = docs.get(target.documentId);
      const store = doc ? ensureStore(doc) : null;
      const record = store?.segments.get(target.segment.segmentId);
      if (!doc || !store || store.sourceVersion !== doc.sourceVersion || store.generation !== doc.generation) {
        skipped++; results.push({ ...target.translation, status: "stale", staleReason: "source_changed", code: "SOURCE_CHANGED" }); continue;
      }
      const translated = { ...(record || target.translation), targetText: job.pending.engine === "dictionary" ? `词 ${target.segment.text}` : `译 ${target.segment.text}`,
        status: "translated", engine: job.pending.engine, dictionaryVersion: job.pending.engine === "dictionary" ? dictionaryState.dictionaryVersion : null,
        configVersion: job.pending.engine === "api" ? settings.configVersion : null, model: job.pending.engine === "api" ? settings.model : null,
        manual: false, staleReason: null, code: null, segmentRevision: (record?.segmentRevision || 0) + 1, updatedAt: 2 };
      store.segments.set(target.segment.segmentId, translated); store.revision += 1; written++; results.push(translated);
    }
    finishJob(job, { written, skipped, failed: 0, rows: [], undoAvailable: false });
    job.pending.plan.status = "consumed";
    job.results = results;
  } else if (job.kind === "dictionary") {
    dictionaryState.status = "ready"; dictionaryState.dictionaryVersion = DICTIONARY_VERSION; dictionaryState.tableName = "fixture_tags";
    dictionaryState.rows = 100; dictionaryState.ref = "fixture-commit"; dictionaryState.error = null;
    finishJob(job, { written: 1, skipped: 0, failed: 0, rows: [], undoAvailable: false });
  }
  job.pending = null;
}
function findItem(itemId) {
  for (const list of Object.values(groups)) { const item = list.find(entry => entry.itemId === itemId); if (item) return item; }
  return null;
}
function createDocument(entry) {
  const { spec, format, field, rawText, documentId, path } = entry;
  const bindingId = id("bind", ++serial);
  const recordId = field.recordId;
  const doc = { libraryId: id("lib", 1), documentId, group: entry.item.group, path, format, sourceHash: hash(++hashSerial),
    sourceVersion: id("ver", ++verSerial), generation: 1, bytes: byteLength(rawText), bom: false,
    newline: rawText.includes("\r\n") ? "crlf" : "none", finalNewline: false, rawText,
    bindings: [{ bindingId, bindingRevision: 1, itemIds: [entry.item.itemId], recordIds: [recordId], spec, status: "confirmed" }],
    fields: [{ ...field, recordId, value: format === "json" ? entry.initialValue : rawText,
      rawRange: { start: 0, end: rawText.length }, segments: [] }], readOnly: false, readOnlyReason: null };
  if (format === "json") rebuildStructured(doc); else rebuildPlain(doc);
  docs.set(documentId, doc);
  const candidate = { documentId, path, format, sourceHash: doc.sourceHash, sourceVersion: doc.sourceVersion, bytes: doc.bytes,
    bindingIds: [bindingId], suggestedKind: field.kind, status: "confirmed" };
  entry.item.candidates = [candidate]; candidates.set(entry.item.itemId, entry.item.candidates);
  entry.item.bindingId = bindingId; entry.item.annotationStatus = "confirmed";
  return doc;
}
function restoreDocument(entry, itemSnapshot) {
  const snapshot = entry.snapshot;
  if (!snapshot) return null;
  let doc = docs.get(snapshot.documentId);
  if (doc) Object.assign(doc, clone(snapshot)); else { doc = clone(snapshot); docs.set(doc.documentId, doc); }
  commitRaw(doc, snapshot.rawText);
  for (const itemId of doc.bindings.flatMap(binding => binding.itemIds)) {
    const item = findItem(itemId); if (!item) continue;
    const binding = doc.bindings.find(entryBinding => entryBinding.itemIds.includes(itemId));
    const saved = itemSnapshot?.find(record => record.itemId === itemId);
    item.bindingId = saved?.bindingId ?? binding.bindingId;
    item.annotationStatus = saved?.annotationStatus ?? "confirmed";
    if (saved) { item.candidates = clone(saved.candidates); candidates.set(itemId, item.candidates); }
    else if (!item.candidates.some(candidate => candidate.documentId === doc.documentId))
      item.candidates.push({ documentId: doc.documentId, path: doc.path, format: doc.format, sourceHash: doc.sourceHash,
        sourceVersion: doc.sourceVersion, bytes: doc.bytes, bindingIds: [binding.bindingId], suggestedKind: binding.spec.kind, status: "confirmed" });
  }
  publishCandidate(doc);
  return doc;
}
function removeDocument(doc) {
  docs.delete(doc.documentId);
  for (const list of candidates.values()) {
    for (const candidate of list) if (candidate.documentId === doc.documentId) candidate.bindingIds = [];
  }
}

/* ----------------------------------------------------------- translation view */
function allSegments(doc) {
  const output = [];
  for (const field of doc.fields) for (const segment of field.segments) output.push(segment);
  return output;
}
function ensureStore(doc) {
  if (translationStores.has(doc.documentId)) return translationStores.get(doc.documentId);
  const segments = new Map();
  for (const [index, segment] of allSegments(doc).entries()) {
    const missing = index % 7 === 5;
    segments.set(segment.segmentId, { segmentId: segment.segmentId, segmentRevision: 1, sourceText: segment.text,
      sourceHash: segment.sourceHash, sourceVersion: segment.sourceVersion, generation: segment.generation,
      recordId: segment.recordId, pointer: segment.pointer, ordinal: segment.ordinal,
      targetText: missing ? segment.text : `手工译文 ${index + 1}`, status: missing ? "missing" : "translated",
      engine: "dictionary", dictionaryVersion: DICTIONARY_VERSION, configVersion: null, model: null,
      sourceLanguage: "en", targetLanguage: "zh-CN", manual: false, staleReason: missing ? null : "dictionary_changed",
      updatedAt: 1, code: missing ? "MISSING_TRANSLATION" : null });
  }
  const store = { sourceVersion: doc.sourceVersion, generation: doc.generation, revision: 5, segments, history: [] };
  translationStores.set(doc.documentId, store);
  return store;
}
function viewFor(doc, sourceLanguage, targetLanguage) {
  const store = ensureStore(doc);
  const languagePair = sourceLanguage === "en" && targetLanguage === "zh-CN";
  if (!languagePair) return { documentId: doc.documentId, sourceLanguage, targetLanguage, sourceVersion: doc.sourceVersion,
    generation: doc.generation, revision: store.revision, snapshotRevision: null, snapshotSourceVersion: null,
    snapshotGeneration: null, segments: [], historyCount: 0, history: [] };
  const stale = store.sourceVersion !== doc.sourceVersion || store.generation !== doc.generation;
  const segments = [...store.segments.values()].map(segment => ({ ...segment, sourceLanguage, targetLanguage,
    status: stale ? "stale" : segment.status, staleReason: stale ? "source_changed" : segment.staleReason,
    code: stale ? "SOURCE_CHANGED" : segment.code }));
  return { documentId: doc.documentId, sourceLanguage, targetLanguage, sourceVersion: doc.sourceVersion,
    generation: doc.generation, revision: store.revision, snapshotRevision: store.revision,
    snapshotSourceVersion: store.sourceVersion, snapshotGeneration: store.generation,
    segments, historyCount: 0, history: [] };
}

/* ------------------------------------------------------------- HTTP fixture */
const html = `<!doctype html><html lang="en" data-theme="kohaku" data-mode="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Annotation offline fixture</title>
<link rel="stylesheet" href="/web/themes/kohaku.css"><link rel="stylesheet" href="/web/base.css"><link rel="stylesheet" href="/web/app.css">
<body data-fixture="offline-synthetic"><p>OFFLINE SYNTHETIC FIXTURE · no writes / translations / downloads</p><button id="fixture-fail-document">Fail next document 61 read</button><main id="fixture-main" style="height:calc(100vh - 64px);overflow:auto"></main>
<script type="module">
import {grabberView} from '/web/grabber.js';
const dictionary=${JSON.stringify(dictionary).replaceAll("<", "\\u003c")};
window.fixtureCleanup=await grabberView(document.querySelector('#fixture-main'),{t:(key,vars={})=>Object.entries(vars).reduce((text,[k,v])=>text.replaceAll('{'+k+'}',v),dictionary[key]||key)});
document.querySelector('#fixture-fail-document').onclick=()=>fetch('/fixture/fail-document');
</script></body></html>`;

function readBody(request) {
  return new Promise((resolvePromise, reject) => {
    let raw = "";
    request.setEncoding("utf8");
    request.on("data", part => { raw += part; if (raw.length > 1_000_000) reject(wireError(413, "BODY_TOO_LARGE")); });
    request.on("end", () => { try { resolvePromise(raw ? JSON.parse(raw) : {}); } catch { reject(wireError(400, "INVALID_JSON")); } });
    request.on("error", reject);
  });
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1"), path = url.pathname, method = request.method;
  const send = (status, value, type = "application/json") => {
    response.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
    const json = type === "application/json" && typeof value !== "string" && !Buffer.isBuffer(value);
    response.end(json ? JSON.stringify(value) : value);
  };
  const ok = (data, status = 200) => send(status, { ok: true, data });
  try {
    if (path === "/" && method === "GET") return send(200, html, "text/html");
    if (path === "/fixture/fail-document" && method === "GET") { failDocument = id("doc", 61); return ok({ ok: true }); }
    if (path.startsWith("/web/") && method === "GET") {
      const file = resolve(web, path.slice(5));
      if (!file.startsWith(web + sep)) return send(403, fail(403, "UNSAFE_PATH").body);
      return send(200, await readFile(file), ({ ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".woff2": "font/woff2" })[extname(file)] || "application/octet-stream");
    }
    if (path === "/api/grabber/preview" && method === "GET")
      return send(200, '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="320"><rect width="480" height="320" fill="#e8e3dc"/><text x="50" y="160" font-size="24">Synthetic image fixture</text></svg>', "image/svg+xml");
    if (method === "GET") {
      const legacy = { "/api/grabber/status": { available: true, running: true },
        "/api/grabber/sources": { sources: [{ id: "yandere", label: "fixture", auth_fields: [] }], capabilities: { library: true, retag: false, annotations_v1: true } },
        "/api/grabber/progress": { status: "idle", items: [], logs: [], item_count: 0, log_count: 0 },
        "/api/grabber/library/groups": { groups: [{ name: "alpha", image_count: images.length }, { name: "beta", image_count: betaImages.length }], truncated: false },
        "/api/grabber/credentials": { values: {}, configured: [] } };
      if (path in legacy) return send(200, legacy[path]);
    }
    const query = Object.fromEntries(url.searchParams);

    /* ---------------------------------------------------------- annotations */
    if (path.startsWith("/api/grabber/annotations/")) {
      const route = path.slice("/api/grabber/annotations/".length);
      if (route === "snapshots" && method === "GET") {
        const snapshot = snapshots.get(query.snapshotId);
        if (!snapshot) return send(404, fail(404, "NOT_FOUND").body);
        return ok(pageOf(snapshot, Number(query.offset || 0)));
      }
      if (route === "snapshots" && method === "POST") {
        const body = await readBody(request);
        if (!groups[body.group]) return send(400, fail(400, "INVALID_REQUEST").body);
        const snapshot = { snapshotId: id("snap", ++serial), libraryId: id("lib", 1), group: body.group,
          filters: body.filters || { format: "any", status: "any", search: "" }, limit: Number(body.limit || 60),
          createdAt: now(), expiresAt: 2147483647, truncated: false, total: 0, items: [] };
        snapshot.items = filteredItems(snapshot.group, snapshot.filters);
        snapshot.total = snapshot.items.length;
        snapshots.set(snapshot.snapshotId, snapshot);
        return ok(pageOf(snapshot, 0), 201);
      }
      if (route === "candidates" && method === "GET") {
        const item = findItem(query.itemId);
        if (!item) return send(404, fail(404, "NOT_FOUND").body);
        return ok({ itemId: item.itemId, candidates: item.candidates || [] });
      }
      if (route === "documents" && method === "GET") {
        const doc = docs.get(query.documentId);
        if (!doc) return send(404, fail(404, "NOT_FOUND").body);
        if (query.documentId === failDocument) { failDocument = null; return send(504, fail(504, "UPSTREAM_TIMEOUT").body); }
        if (query.expectedVersion && query.expectedVersion !== doc.sourceVersion) return send(409, fail(409, "VERSION_CONFLICT").body);
        return ok(doc);
      }
      if (route === "bindings/confirm" && method === "POST") {
        const body = await readBody(request);
        const doc = docs.get(body.documentId);
        if (!doc) return send(404, fail(404, "NOT_FOUND").body);
        if (body.expectedVersion !== doc.sourceVersion) return send(409, fail(409, "VERSION_CONFLICT").body);
        const item = findItem(body.itemId);
        if (!item) return send(404, fail(404, "NOT_FOUND").body);
        let binding = doc.bindings.find(entry => entry.itemIds.includes(item.itemId));
        const bindingId = binding?.bindingId || item.bindingId || id("bind", ++serial);
        const recordIds = Array.isArray(body.recordIds) && body.recordIds.length ? body.recordIds
          : binding?.recordIds || [doc.fields[0]?.recordId || id("rec", ++serial)];
        const spec = body.spec;
        if (!spec || !Array.isArray(spec.fields)) return send(422, fail(422, "INVALID_REQUEST").body);
        if (binding) { binding.spec = spec; binding.bindingRevision += 1; binding.recordIds = recordIds; }
        else {
          binding = { bindingId, bindingRevision: 1, itemIds: [item.itemId], recordIds, spec, status: "confirmed" };
          doc.bindings.push(binding);
        }
        doc.format = spec.format || doc.format; doc.readOnly = false; doc.readOnlyReason = null;
        doc.fields = recordIds.map((recordId, index) => ({
          recordId, pointer: spec.fields[0]?.pointer ?? "", type: spec.fields[0]?.type === "string_array" ? "string_array" : "string",
          kind: spec.kind, delimiter: spec.delimiter ?? null, value: "", rawRange: { start: 0, end: doc.rawText.length }, segments: [] }));
        doc.fields = doc.fields.map((field, index) => ({ ...field, recordId: recordIds[index] }));
        if (structuredFormats.has(doc.format)) rebuildStructured(doc); else rebuildPlain(doc);
        item.bindingId = bindingId; item.annotationStatus = "confirmed"; item.candidates = (item.candidates || []).map(candidate =>
          candidate.documentId === doc.documentId ? { ...candidate, bindingIds: [...new Set([...candidate.bindingIds, bindingId])], suggestedKind: spec.kind, status: "confirmed" } : candidate);
        candidates.set(item.itemId, item.candidates);
        return ok({ bindingId, bindingRevision: binding.bindingRevision, document: doc });
      }
      if (route === "bindings/select" && method === "POST") {
        const body = await readBody(request);
        const doc = docs.get([...docs.values()].find(entry => entry.bindings.some(binding => binding.bindingId === body.bindingId))?.documentId);
        const binding = doc?.bindings.find(entry => entry.bindingId === body.bindingId);
        if (!binding) return send(404, fail(404, "NOT_FOUND").body);
        if (body.expectedBindingRevision !== binding.bindingRevision) return send(409, fail(409, "VERSION_CONFLICT").body);
        const item = findItem(body.itemId); if (item) item.bindingId = binding.bindingId;
        return ok({ itemId: body.itemId, bindingId: binding.bindingId, bindingRevision: binding.bindingRevision });
      }
      if (route === "plans" && method === "POST") {
        const body = await readBody(request);
        if (!body.scope || !body.operation) return send(400, fail(400, "INVALID_REQUEST").body);
        const built = planRows(body.scope, body.operation, body.missingPolicy, body.selections);
        const plan = { planId: id("plan", ++serial), libraryId: id("lib", 1), requestId: body.requestId, kind: "edit",
          status: built.rows.some(row => row.status === "error") ? "blocked" : "ready", scope: body.scope, operation: body.operation,
          summary: built.summary, createdAt: now(), expiresAt: 2147483647, manifestHash: hash(++hashSerial) };
        plan.internals = built.internals;
        plans.set(plan.planId, plan);
        return ok(planWire(plan), 201);
      }
      if (route === "plans" && method === "GET") {
        const plan = plans.get(query.planId); return plan ? ok(planWire(plan)) : send(404, fail(404, "NOT_FOUND").body);
      }
      if (route === "plans/rows" && method === "GET") {
        const plan = plans.get(query.planId); if (!plan) return send(404, fail(404, "NOT_FOUND").body);
        const offset = Number(query.offset || 0), limit = Number(query.limit || 60), includeDiff = query.includeDiff !== "false";
        const sourceRows = plan.kind === "undo" ? plan.restores.map(entry => entry.row) : plan.internals.map(entry => entry.row);
        return ok({ planId: plan.planId, total: sourceRows.length, offset, limit,
          rows: sourceRows.slice(offset, offset + limit).map(row => ({ ...row, diff: includeDiff ? row.diff : null })) });
      }
      if (route === "plans/apply" && method === "POST") {
        const body = await readBody(request);
        const plan = plans.get(body.planId); if (!plan) return send(404, fail(404, "NOT_FOUND").body);
        if (plan.jobId && jobs.has(plan.jobId)) return ok({ job: jobWire(jobs.get(plan.jobId)) }, 202);
        if (plan.status !== "ready") return send(409, fail(409, plan.status === "consumed" ? "PLAN_CONSUMED" : "INVALID_OPERATION").body);
        if (body.confirm !== true || body.manifestHash !== plan.manifestHash) return send(409, fail(409, "VERSION_CONFLICT").body);
        const job = plan.kind === "undo" ? makeJob("undo", plan.planId, plan.restores.length) : makeJob("apply", plan.planId, plan.internals.length);
        if (plan.kind === "undo") {
          job.pending = { plan, restores: plan.restores };
          job.rows = plan.restores.map(entry => ({ documentId: entry.row.documentId, status: entry.row.status === "skip" ? "skipped" : "written",
            code: entry.row.code, beforeHash: entry.row.beforeHash, afterHash: entry.row.afterHash, beforeVersion: entry.row.beforeVersion,
            afterVersion: null, backupId: entry.row.status === "ready" ? `${job.jobId}:${entry.row.documentId}` : null }));
        } else {
          job.pending = { plan, entries: plan.internals };
          job.rows = plan.internals.map(entry => ({ documentId: entry.row.documentId, status: entry.row.status === "skip" ? "skipped" : entry.row.status === "error" ? "failed" : "written",
            code: entry.row.code, beforeHash: entry.row.beforeHash, afterHash: entry.row.afterHash, beforeVersion: entry.row.beforeVersion,
            afterVersion: null, backupId: entry.row.status === "ready" ? `${job.jobId}:${entry.row.documentId}` : null }));
        }
        plan.status = "applying"; plan.jobId = job.jobId; jobs.set(job.jobId, job);
        return ok({ job: jobWire(job) }, 202);
      }
      if (route === "undo/plans" && method === "POST") {
        const body = await readBody(request);
        const source = jobs.get(body.jobId);
        if (!source) return send(404, fail(404, "NOT_FOUND").body);
        const rows = [], restores = [];
        for (const entry of source.undoEntries || []) {
          const current = docs.get(entry.documentId);
          let status = "ready", code = null;
          if (entry.kind === "write" && (!current || current.sourceVersion !== entry.afterVersion)) { status = "skip"; code = "VERSION_CONFLICT"; }
          if (entry.kind === "create" && !current) { status = "skip"; code = "VERSION_CONFLICT"; }
          if (entry.kind === "delete" && current) { status = "skip"; code = "VERSION_CONFLICT"; }
          const row = { documentId: entry.documentId, path: entry.snapshot?.path || current?.path || null, itemIds: entry.itemIds,
            beforeHash: current?.sourceHash || null, afterHash: entry.snapshot ? hash(++hashSerial) : null, beforeVersion: current?.sourceVersion || null,
            bindingRevisions: [], action: entry.kind === "create" ? "delete" : entry.kind === "delete" ? "create" : "write",
            status, code, matchedOccurrences: 0, changedOccurrences: status === "ready" ? 1 : 0, duplicateOccurrences: 0,
            diff: { before: current?.rawText ?? null, after: entry.snapshot?.rawText ?? null } };
          rows.push(row); restores.push({ row, entry, current });
        }
        const ready = rows.filter(row => row.status === "ready").length;
        const plan = { planId: id("plan", ++serial), libraryId: id("lib", 1), requestId: body.requestId, kind: "undo",
          status: ready ? "ready" : "blocked", scope: null, operation: { op: "restore_job", jobId: body.jobId },
          summary: { images: rows.flatMap(row => row.itemIds).length, documents: rows.length, rows: rows.length, fields: rows.length,
            matchedOccurrences: 0, changedOccurrences: ready, duplicateOccurrences: 0, ready, skipped: rows.length - ready, errors: 0 },
          createdAt: now(), expiresAt: 2147483647, manifestHash: hash(++hashSerial), restores };
        plans.set(plan.planId, plan);
        return ok(planWire(plan), 201);
      }
      if (route === "jobs" && method === "GET") {
        const job = jobs.get(query.jobId); if (!job) return send(404, fail(404, "NOT_FOUND").body);
        if (job.status === "running" && ++job.polls >= 2) completeJob(job);
        return ok(jobWire(job));
      }
      if (route === "jobs/rows" && method === "GET") {
        const job = jobs.get(query.jobId); if (!job) return send(404, fail(404, "NOT_FOUND").body);
        const offset = Number(query.offset || 0), limit = Number(query.limit || 60);
        return ok({ jobId: job.jobId, total: job.rows.length, offset, limit, rows: job.rows.slice(offset, offset + limit) });
      }
      if (route === "jobs/stop" && method === "POST") {
        const body = await readBody(request); const job = jobs.get(body.jobId);
        if (!job) return send(404, fail(404, "NOT_FOUND").body);
        if (job.status === "running") { job.status = "stopped"; job.finishedAt = now(); job.cancelRequested = true; }
        return ok({ job: jobWire(job) });
      }
      return send(422, fail(422, "INVALID_OPERATION").body);
    }

    /* ----------------------------------------------------------- translation */
    if (path.startsWith("/api/translation/")) {
      const route = path.slice("/api/translation/".length);
      if (route === "settings" && method === "GET") return ok(settings);
      if (route === "settings" && method === "POST") {
        const body = await readBody(request);
        if (body.expectedConfigVersion !== settings.configVersion) return send(409, fail(409, "SETTINGS_CHANGED").body);
        for (const key of ["endpoint", "model", "sourceLanguage", "targetLanguage", "timeoutSeconds", "maxOutputTokens", "temperature", "concurrency", "enabled"]) {
          if (key in body) settings[key] = body[key];
        }
        settings.configVersion = id("cfg", ++serial);
        return ok(settings);
      }
      if (route === "credentials" && method === "POST") {
        const body = await readBody(request);
        if (body.expectedConfigVersion !== settings.configVersion) return send(409, fail(409, "SETTINGS_CHANGED").body);
        if (body.clear === true) { settings.hasKey = false; settings.keyHint = ""; }
        else if (typeof body.apiKey === "string" && body.apiKey.length) { settings.hasKey = true; settings.keyHint = "configured"; }
        settings.configVersion = id("cfg", ++serial);
        return ok({ configVersion: settings.configVersion, hasKey: settings.hasKey, keyHint: settings.keyHint });
      }
      if (route === "dictionary" && method === "GET") return ok(dictionaryState);
      if (route === "dictionary/import" && method === "POST") {
        const body = await readBody(request);
        if (typeof body.path !== "string" || !body.path.startsWith("/")) return send(400, fail(400, "INVALID_REQUEST").body);
        const job = makeJob("dictionary", null, 1); jobs.set(job.jobId, job);
        return ok({ job: jobWire(job) }, 202);
      }
      if (["dictionary/install", "dictionary/update"].includes(route) && method === "POST")
        return send(422, fail(422, "INVALID_OPERATION").body);
      if (route === "view" && method === "GET") {
        const doc = docs.get(query.documentId); if (!doc) return send(404, fail(404, "NOT_FOUND").body);
        return ok(viewFor(doc, query.sourceLanguage || settings.sourceLanguage, query.targetLanguage || settings.targetLanguage));
      }
      if (route === "plans" && method === "POST") {
        const body = await readBody(request);
        if (body.engine === "dictionary" && dictionaryState.status !== "ready") return send(409, fail(409, "DICTIONARY_MISSING").body);
        const { picked } = resolveScope(body.scope);
        const language = { sourceLanguage: body.sourceLanguage || settings.sourceLanguage, targetLanguage: body.targetLanguage || settings.targetLanguage };
        const targets = [];
        const seen = new Set();
        for (const item of picked) {
          const doc = docForItem(item); if (!doc) continue;
          const binding = bindingFor(doc, item); if (!binding) continue;
          const selection = body.selections?.find(entry => entry.itemId === item.itemId);
          const fields = fieldsFor(doc, binding).filter(field => !selection
            || (selection.recordIds.includes(field.recordId) && selection.pointers.includes(field.pointer)));
          for (const field of fields) for (const segment of field.segments) {
            if (seen.has(segment.segmentId)) continue; seen.add(segment.segmentId);
            if (Array.isArray(body.segmentIds) && body.segmentIds.length && !body.segmentIds.includes(segment.segmentId)) continue;
            const store = ensureStore(doc), record = store.segments.get(segment.segmentId);
            const manual = record?.manual && !body.replaceManual;
            const missing = record?.status === "missing" && body.engine === "dictionary";
            const status = manual ? "skip" : missing ? "skip" : "ready";
            targets.push({ documentId: doc.documentId, itemIds: [item.itemId], segment, expectedTranslationRevision: store.revision,
              expectedSegmentRevision: record?.segmentRevision || 0, status,
              code: manual ? "MANUAL_PROTECTED" : missing ? "MISSING_TRANSLATION" : null,
              translation: { ...(record || {}), segmentId: segment.segmentId, sourceText: segment.text, targetText: record?.targetText ?? segment.text,
                status: record?.status || "missing", engine: record?.engine || null, dictionaryVersion: record?.dictionaryVersion ?? null,
                configVersion: record?.configVersion ?? null, model: record?.model ?? null, sourceLanguage: language.sourceLanguage,
                targetLanguage: language.targetLanguage, manual: record?.manual ?? false, staleReason: record?.staleReason ?? null,
                updatedAt: record?.updatedAt || 1, code: record?.code ?? null, segmentRevision: record?.segmentRevision || 0 } });
          }
        }
        const ready = targets.filter(target => target.status === "ready");
        const api = body.engine === "api";
        const batches = api ? Math.ceil(ready.length / 20) : 0;
        const plan = { planId: id("plan", ++serial), requestId: body.requestId, status: "ready", engine: body.engine,
          scope: body.scope, sourceLanguage: language.sourceLanguage, targetLanguage: language.targetLanguage,
          dictionaryVersion: body.engine === "dictionary" ? dictionaryState.dictionaryVersion : null,
          configVersion: settings.configVersion, model: api ? settings.model || "fixture-model" : null,
          replaceManual: body.replaceManual === true, targetSegments: targets.length,
          sourceCharacters: ready.reduce((total, target) => total + target.segment.text.length, 0), cacheHits: 0,
          remoteBatches: batches, maximumOutputTokens: api ? batches * 2048 : 0,
          manifestHash: hash(++hashSerial), createdAt: now(), expiresAt: 2147483647, targets };
        translationPlans.set(plan.planId, plan);
        return ok(translationPlanWire(plan), 201);
      }
      if (route === "plans" && method === "GET") {
        const plan = translationPlans.get(query.planId); return plan ? ok(translationPlanWire(plan)) : send(404, fail(404, "NOT_FOUND").body);
      }
      if (route === "plans/segments" && method === "GET") {
        const plan = translationPlans.get(query.planId); if (!plan) return send(404, fail(404, "NOT_FOUND").body);
        const offset = Number(query.offset || 0), limit = Number(query.limit || 60);
        return ok({ planId: plan.planId, total: plan.targets.length, offset, limit,
          segments: plan.targets.slice(offset, offset + limit).map(translationTargetWire) });
      }
      if (route === "jobs/start" && method === "POST") {
        const body = await readBody(request);
        if (typeof body.planId !== "string" || typeof body.manifestHash !== "string") return send(422, fail(422, "INVALID_REQUEST").body);
        const plan = translationPlans.get(body.planId);
        if (!plan) return send(404, fail(404, "NOT_FOUND").body);
        if (body.manifestHash !== plan.manifestHash) return send(409, fail(409, "VERSION_CONFLICT").body);
        if (plan.status !== "ready") return send(409, fail(409, plan.status === "consumed" ? "PLAN_CONSUMED" : "INVALID_OPERATION").body);
        if (plan.engine === "api" && (!body.confirmRemote || !body.confirmCost || body.acceptMaximumOutputTokens !== plan.maximumOutputTokens))
          return send(422, fail(422, "INVALID_REQUEST").body);
        const job = makeJob("translation", plan.planId, plan.targets.length);
        job.pending = { targets: plan.targets, engine: plan.engine, plan };
        plan.status = "consumed"; jobs.set(job.jobId, job);
        return ok({ job: jobWire(job) }, 202);
      }
      if (route === "jobs" && method === "GET") {
        const job = jobs.get(query.jobId); if (!job) return send(404, fail(404, "NOT_FOUND").body);
        if (job.status === "running" && ++job.polls >= 2) completeJob(job);
        return ok(jobWire(job));
      }
      if (route === "jobs/segments" && method === "GET") {
        const job = jobs.get(query.jobId); if (!job) return send(404, fail(404, "NOT_FOUND").body);
        const offset = Number(query.offset || 0), limit = Number(query.limit || 60), results = job.results || [];
        return ok({ jobId: job.jobId, total: results.length, offset, limit, segments: results.slice(offset, offset + limit) });
      }
      if (route === "jobs/stop" && method === "POST") {
        const body = await readBody(request); const job = jobs.get(body.jobId);
        if (!job) return send(404, fail(404, "NOT_FOUND").body);
        if (job.status === "running") { job.status = "stopped"; job.cancelRequested = true; job.finishedAt = now(); }
        return ok({ job: jobWire(job) });
      }
      if (route === "manual" && method === "POST") {
        const body = await readBody(request);
        const doc = docs.get(body.documentId); if (!doc) return send(404, fail(404, "NOT_FOUND").body);
        const store = ensureStore(doc);
        if (store.sourceVersion !== doc.sourceVersion || store.generation !== doc.generation) return send(409, fail(409, "TRANSLATION_STALE").body);
        if (body.expectedRevision !== store.revision) return send(409, fail(409, "VERSION_CONFLICT").body);
        const old = store.segments.get(body.segmentId);
        if (!old) return send(409, fail(409, "TRANSLATION_STALE").body);
        const segment = { ...old, targetText: String(body.targetText ?? ""), manual: true, engine: "manual", status: "translated",
          dictionaryVersion: null, configVersion: null, model: null, staleReason: null, code: null,
          segmentRevision: old.segmentRevision + 1, updatedAt: 3 };
        store.segments.set(segment.segmentId, segment); store.revision += 1;
        return ok({ revision: store.revision, segment });
      }
      return send(422, fail(422, "INVALID_OPERATION").body);
    }

    return send(404, fail(404, "NOT_FOUND").body);
  } catch (error) {
    if (error?.status) return send(error.status, fail(error.status, error.code, error.details || {}).body);
    return send(500, fail(500, "INTERNAL").body);
  }
});

const smoke = process.argv.includes("--smoke"), port = smoke ? 0 : Number(process.argv[2] || 0);
await new Promise(resolvePromise => server.listen(port, "127.0.0.1", resolvePromise));
const origin = `http://127.0.0.1:${server.address().port}`;
if (smoke) {
  try {
    assert.equal((await fetch(origin)).status, 200);
    assert.equal((await fetch(`${origin}/web/grabber.js`)).status, 200);
    assert.equal(typeof (await (await fetch(`${origin}/web/lang/en.json`)).json())["grabber.tab.library"], "string");
    const document = await (await fetch(`${origin}/api/grabber/annotations/documents?documentId=${id("doc", 3)}`)).json();
    assert.equal(document.data.format, "json");
    const denied = await fetch(`${origin}/api/translation/jobs/start`, { method: "POST", body: "{}" });
    assert.equal(denied.status, 422);
    const snapshot = await (await fetch(`${origin}/api/grabber/annotations/snapshots`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestId: "req_smoke", group: "alpha", filters: { format: "any", status: "any", search: "" }, limit: 60 }) })).json();
    assert.equal(snapshot.data.items.length, 60);
    const plan = await (await fetch(`${origin}/api/grabber/annotations/plans`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestId: "req_smoke", scope: { mode: "page_selected", snapshotId: snapshot.data.snapshotId, pageOffset: 0, itemIds: [id("item", 1)] },
        operation: { op: "tag_add", tokens: ["smoke"] }, missingPolicy: "error" }) })).json();
    assert.equal(plan.data.status, "ready");
    const rows = await (await fetch(`${origin}/api/grabber/annotations/plans/rows?planId=${plan.data.planId}&offset=0&limit=60&includeDiff=true`)).json();
    assert.equal(rows.data.rows[0].diff.before, "cat, cat, dog");
    const applied = await (await fetch(`${origin}/api/grabber/annotations/plans/apply`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestId: "req_smoke", planId: plan.data.planId, manifestHash: plan.data.manifestHash, confirm: true }) })).json();
    const first = await (await fetch(`${origin}/api/grabber/annotations/jobs?jobId=${applied.data.job.jobId}`)).json();
    const second = await (await fetch(`${origin}/api/grabber/annotations/jobs?jobId=${applied.data.job.jobId}`)).json();
    assert.equal(first.data.status, "running"); assert.equal(second.data.status, "done");
    const updated = await (await fetch(`${origin}/api/grabber/annotations/documents?documentId=${id("doc", 1)}`)).json();
    assert.equal(updated.data.rawText, "cat, cat, dog, smoke");
    const view = await (await fetch(`${origin}/api/translation/view?documentId=${id("doc", 15)}`)).json();
    assert.equal(view.data.sourceLanguage, "en");
    const imported = await (await fetch(`${origin}/api/translation/dictionary/import`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ requestId: "req_smoke", path: "/tmp/km-annotation-browser/fixture.sqlite" }) })).json();
    assert.equal(imported.data.job.kind, "dictionary");
    await fetch(`${origin}/api/translation/jobs?jobId=${imported.data.job.jobId}`);
    await fetch(`${origin}/api/translation/jobs?jobId=${imported.data.job.jobId}`);
    assert.equal((await (await fetch(`${origin}/api/translation/dictionary`)).json()).data.status, "ready");
    console.log("offline browser fixture: entry, snapshot, plan preview/apply/poll, translation view, dictionary import and fail-closed mutation smoke passed");
  } finally { server.closeAllConnections(); await new Promise(resolvePromise => server.close(resolvePromise)); }
} else console.log(`${origin}/?token=fixture-token#/grabber`);
