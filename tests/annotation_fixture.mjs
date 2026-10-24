const id = (prefix, n) => `${prefix}_${n.toString(16).padStart(32, "0")}`;
const hash = n => `sha256:${n.toString(16).padStart(64, "0")}`;
const docs = new Map(), candidates = new Map();
const images = Array.from({ length: 64 }, (_, n) => ({ itemId: id("item", n + 1), group: "alpha", imagePath: `image-${n + 1}.png`, imageUrl: `/files/alpha/image-${n + 1}.png`, candidates: [], bindingId: id("bind", n + 1), annotationStatus: "confirmed" }));
function documentFixture(n, format = "txt", kind = "tags") {
  const structured = ["json", "jsonl"].includes(format), rawText = structured ? '{\r\n  "caption":"😀\\nA", "keep":1e+02\r\n}' : kind === "tags" ? "cat, cat, dog" : "A quiet room.\r\n\r\nA window.";
  const value = structured ? "😀\nA" : rawText, documentId = id("doc", n), recordId = id("rec", n), bindingId = id("bind", n);
  const sourceVersion = id("ver", n), sourceHash = hash(n), pointer = structured ? "/caption" : "";
  const texts = kind === "tags" ? ["cat", "cat", "dog"] : structured ? [value] : ["A quiet room.", "A window."];
  const segments = texts.map((text, ordinal) => { const start = kind === "tags" ? ordinal * 5 : ordinal ? 17 : 0;
    return { segmentId: id("seg", n * 10 + ordinal), recordId, pointer, ordinal, kind, text, sourceHash, sourceVersion, generation: 1,
      rawRanges: [{ start: structured ? 16 : start, end: structured ? 21 : start + text.length }], valueRange: { start, end: start + text.length }, elementIndex: null }; });
  const spec = { format, kind, delimiter: kind === "tags" ? "comma" : null, mapping: structured ? { mode: "sidecar", recordsPointer: "", recordIdPointer: null, imagePointer: null } : { mode: "sidecar" }, fields: [{ pointer, type: "string" }] };
  const doc = { libraryId: id("lib", 1), documentId, group: "alpha", path: `image-${n}.${format}`, format, sourceHash, sourceVersion, generation: 1,
    bytes: Buffer.byteLength(rawText), bom: false, newline: rawText.includes("\r\n") ? "crlf" : "none", finalNewline: false, rawText,
    bindings: [{ bindingId, bindingRevision: 1, itemIds: [id("item", n)], recordIds: [recordId], spec, status: "confirmed" }],
    fields: [{ recordId, pointer, type: "string", kind, delimiter: spec.delimiter, value, rawRange: { start: structured ? 15 : 0, end: structured ? 22 : rawText.length }, segments }], readOnly: false, readOnlyReason: null };
  docs.set(documentId, doc);
  const candidate = { documentId, path: doc.path, format, sourceHash, sourceVersion, bytes: doc.bytes, bindingIds: [bindingId], suggestedKind: kind, status: "confirmed" };
  candidates.set(id("item", n), [candidate]); images[n - 1].candidates = [candidate]; return doc;
}
for (let n = 1; n <= 64; n++) documentFixture(n, n === 2 ? "caption" : n === 3 ? "json" : n === 4 ? "jsonl" : "txt", [2, 3, 4].includes(n) ? "nl" : "tags");
// A valid multi-record JSONL source: only record 4 belongs to the current binding.
const sharedFixture = docs.get(id("doc",4));
sharedFixture.rawText = JSON.stringify({id:"record-four",image:"image-4.png",caption:"First record"}) + "\r\n" + JSON.stringify({id:"record-five",image:"image-5.png",caption:"Second record"}) + "\r\n";
sharedFixture.path = "shared.jsonl"; sharedFixture.bytes = Buffer.byteLength(sharedFixture.rawText); sharedFixture.finalNewline = true;
sharedFixture.bindings[0].spec.mapping = {mode:"records",recordsPointer:"",recordIdPointer:"/id",imagePointer:"/image"};
sharedFixture.fields = ["First record","Second record"].map((value,index)=>{
 const start=sharedFixture.rawText.indexOf(value), recordId=id("rec",index ? 400 : 4);
 return {recordId,pointer:"/caption",type:"string",kind:"nl",delimiter:null,value,rawRange:{start:start-1,end:start+value.length+1},segments:[{
 segmentId:id("seg",40+index),recordId,pointer:"/caption",ordinal:0,kind:"nl",text:value,sourceHash:sharedFixture.sourceHash,sourceVersion:sharedFixture.sourceVersion,generation:1,rawRanges:[{start,end:start+value.length}],valueRange:{start:0,end:value.length},elementIndex:null}]};
});
// 2,000 server-identified segments within a legal <64KiB structured source.
const largeDoc=docs.get(id("doc",6));
const repeated=Array.from({length:1000},()=>"cat");
largeDoc.format="json"; largeDoc.path="image-6.json"; largeDoc.rawText=JSON.stringify({tags:repeated,other:repeated}); largeDoc.bytes=Buffer.byteLength(largeDoc.rawText);
largeDoc.bindings[0].spec={format:"json",kind:"tags",delimiter:null,mapping:{mode:"sidecar",recordsPointer:"",recordIdPointer:null,imagePointer:null},fields:[{pointer:"/tags",type:"string_array"},{pointer:"/other",type:"string_array"}]};
largeDoc.fields=["/tags","/other"].map((pointer,fi)=>{
 const start=largeDoc.rawText.indexOf("[",fi ? largeDoc.rawText.indexOf("\"other\"") : 0), end=largeDoc.rawText.indexOf("]",start)+1;
 return {recordId:id("rec",6),pointer,type:"string_array",kind:"tags",delimiter:null,value:[...repeated],rawRange:{start,end},segments:repeated.map((text,ordinal)=>({
  segmentId:id("seg",100000+fi*1000+ordinal),recordId:id("rec",6),pointer,ordinal,kind:"tags",text,sourceHash:largeDoc.sourceHash,sourceVersion:largeDoc.sourceVersion,generation:1,
  rawRanges:[{start:start+2+ordinal*6,end:start+5+ordinal*6}],valueRange:{start:0,end:3},elementIndex:ordinal}))};
});
Object.assign(candidates.get(id("item",6))[0],{format:"json",path:largeDoc.path,bytes:largeDoc.bytes});
const manyRecords=docs.get(id("doc",7)), recordLines=[];
manyRecords.format="jsonl"; manyRecords.path="shared-many.jsonl"; manyRecords.fields=[]; manyRecords.bindings[0].recordIds=[];
manyRecords.bindings[0].spec={format:"jsonl",kind:"tags",delimiter:null,mapping:{mode:"records",recordsPointer:"",recordIdPointer:"/id",imagePointer:"/image"},fields:[{pointer:"/tags",type:"string_array"}]};
let recordOffset=0;
for(let n=0;n<200;n++) {
 const values=Array.from({length:10},()=>"cat"), line=JSON.stringify({id:`record-${n}`,image:"image-7.png",tags:values}), start=recordOffset+line.indexOf("["), recordId=id("rec",7000+n);
 manyRecords.bindings[0].recordIds.push(recordId);
 manyRecords.fields.push({recordId,pointer:"/tags",type:"string_array",kind:"tags",delimiter:null,value:values,rawRange:{start,end:recordOffset+line.lastIndexOf("]")+1},segments:values.map((text,ordinal)=>({
  segmentId:id("seg",200000+n*10+ordinal),recordId,pointer:"/tags",ordinal,kind:"tags",text,sourceHash:manyRecords.sourceHash,sourceVersion:manyRecords.sourceVersion,generation:1,
  rawRanges:[{start:start+2+ordinal*6,end:start+5+ordinal*6}],valueRange:{start:0,end:3},elementIndex:ordinal}))});
 recordLines.push(line); recordOffset+=line.length+1;
}
manyRecords.rawText=recordLines.join("\n")+"\n"; manyRecords.bytes=Buffer.byteLength(manyRecords.rawText); manyRecords.newline="lf"; manyRecords.finalNewline=true;
Object.assign(candidates.get(id("item",7))[0],{format:"jsonl",path:manyRecords.path,bytes:manyRecords.bytes});
// A space-delimited txt sidecar: the same Tag kind must not be inferred from a
// comma-only sample. Both delimiter styles stay available to browser acceptance.
const spaceDoc = docs.get(id("doc", 9));
spaceDoc.rawText = "cat dog eel"; spaceDoc.bytes = Buffer.byteLength(spaceDoc.rawText); spaceDoc.newline = "none";
spaceDoc.bindings[0].spec.delimiter = "space";
Object.assign(spaceDoc.fields[0], { delimiter: "space", value: spaceDoc.rawText, rawRange: { start: 0, end: spaceDoc.rawText.length },
  segments: ["cat", "dog", "eel"].map((text, ordinal) => {
    const start = ordinal * 4;
    return { segmentId: id("seg", 90 + ordinal), recordId: spaceDoc.fields[0].recordId, pointer: "", ordinal, kind: "tags", text,
      sourceHash: spaceDoc.sourceHash, sourceVersion: spaceDoc.sourceVersion, generation: 1,
      rawRanges: [{ start, end: start + text.length }], valueRange: { start, end: start + text.length }, elementIndex: null };
  }) });
// Two images may share one physical JSONL document while selecting different
// records. This makes the physical-file count differ from the image count.
const sharedBindingForFive = { bindingId: id("bind", 405), bindingRevision: 1, itemIds: [id("item", 5)], recordIds: [id("rec", 400)],
  spec: docs.get(id("doc", 4)).bindings[0].spec, status: "confirmed" };
docs.get(id("doc", 4)).bindings.push(sharedBindingForFive);
const sharedCandidate = candidates.get(id("item", 4))[0];
const fiveCandidate = { ...sharedCandidate, bindingIds: [sharedBindingForFive.bindingId] };
images[4] = { ...images[4], bindingId: sharedBindingForFive.bindingId, annotationStatus: "confirmed", candidates: [fiveCandidate] };
candidates.set(id("item", 5), [fiveCandidate]);
// Synchronize every Candidate after structured fixtures replace the initial sidecar.
for (const doc of docs.values()) {
  for (const list of candidates.values()) for (const candidate of list) if (candidate.documentId === doc.documentId)
    Object.assign(candidate, { path: doc.path, format: doc.format, bytes: doc.bytes, sourceHash: doc.sourceHash, sourceVersion: doc.sourceVersion });
}
// The shared candidate needs both bindings after the generic synchronization.
const syncedFour = candidates.get(id("item", 4))[0];
const syncedFive = candidates.get(id("item", 5))[0];
syncedFour.bindingIds = [docs.get(id("doc", 4)).bindings[0].bindingId, sharedBindingForFive.bindingId];
syncedFive.bindingIds = [sharedBindingForFive.bindingId];
export { id, hash, docs, candidates, images, documentFixture, largeDoc, manyRecords };
