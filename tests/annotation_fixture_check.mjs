/* Offline synthetic wire checks, matching contract/annotations.py Document/Field/
 * Segment/Candidate schemas. --json exports reusable full envelopes for backend tests.
 * Does not import a backend or access any data directory/network. */
import assert from "node:assert/strict";
import { docs, candidates, images } from "./annotation_fixture.mjs";
export function exactKeys(value, keys) {
  assert.deepEqual(Object.keys(value).sort(), keys.split(",").sort());
}
export function checkDocument(doc) {
  exactKeys(doc, "libraryId,documentId,group,path,format,sourceHash,sourceVersion,generation,bytes,bom,newline,finalNewline,rawText,bindings,fields,readOnly,readOnlyReason");
  assert.equal(Buffer.byteLength(doc.rawText) + (doc.bom ? 3 : 0), doc.bytes);
  assert.ok(doc.bytes <= 65536); assert.ok(doc.generation > 0);
  assert.match(doc.documentId, /^doc_[0-9a-f]{32}$/);
  const ids = new Set();
  for (const b of doc.bindings) {
    exactKeys(b, "bindingId,bindingRevision,itemIds,recordIds,spec,status");
    exactKeys(b.spec, "format,kind,delimiter,mapping,fields");
  }
  for (const field of doc.fields) {
    exactKeys(field, "recordId,pointer,type,kind,delimiter,value,rawRange,segments");
    const raw = doc.rawText.slice(field.rawRange.start, field.rawRange.end);
    assert.deepEqual(["json", "jsonl"].includes(doc.format) ? JSON.parse(raw) : raw, field.value);
    for (const [ordinal, segment] of field.segments.entries()) {
      exactKeys(segment, "segmentId,recordId,pointer,ordinal,kind,text,sourceHash,sourceVersion,generation,rawRanges,valueRange,elementIndex");
      assert.match(segment.segmentId, /^seg_[0-9a-f]{32}$/); assert.ok(!ids.has(segment.segmentId)); ids.add(segment.segmentId);
      for (const key of ["recordId", "pointer", "kind"]) assert.equal(segment[key], field[key]);
      for (const key of ["sourceHash", "sourceVersion", "generation"]) assert.equal(segment[key], doc[key]);
      assert.equal(segment.ordinal, ordinal);
      const decoded = field.type === "string_array" ? field.value[segment.elementIndex] : field.value;
      assert.equal(decoded.slice(segment.valueRange.start, segment.valueRange.end), segment.text);
      if (field.type === "string") assert.equal(segment.elementIndex, null);
      let previous = field.rawRange.start;
      for (const range of segment.rawRanges) {
        exactKeys(range, "start,end"); assert.ok(range.start >= previous && range.end > range.start && range.end <= field.rawRange.end);
        previous = range.end;
      }
    }
  }
  assert.ok(ids.size <= 2000);
}
for (const doc of docs.values()) checkDocument(doc);
for (const item of images) {
  exactKeys(item, "itemId,group,imagePath,imageUrl,candidates,bindingId,annotationStatus");
  for (const c of candidates.get(item.itemId)) {
    exactKeys(c, "documentId,path,format,sourceHash,sourceVersion,bytes,bindingIds,suggestedKind,status");
    for (const key of ["path", "format", "sourceHash", "sourceVersion", "bytes"]) assert.equal(c[key], docs.get(c.documentId)[key]);
  }
}
if (process.argv.includes("--json")) console.log(JSON.stringify({ documents: [...docs.values()].map(data => ({ok:true,data})), candidates: images.map(i => ({ok:true,data:{itemId:i.itemId,candidates:i.candidates}})) }));
else console.log(`annotation fixture: ${docs.size} full documents, repeated tags, NL paragraphs, escaped UTF16 JSON, arrays and cross-record JSONL checked`);
