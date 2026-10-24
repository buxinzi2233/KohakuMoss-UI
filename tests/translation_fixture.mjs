/* Wire fixtures match contract/translation.py:_view/_manual/_get and
 * translation_records.py:make_segment. No fields invented on GET view.
 * Browser-portable; all content is synthetic, no filesystem/provider access. */
export const id = (prefix, n) => `${prefix}_${n.toString(16).padStart(32, "0")}`;
export const hash = n => `sha256:${n.toString(16).padStart(64, "0")}`;
export const clone = value => JSON.parse(JSON.stringify(value));
export const settings = {configVersion:id("cfg",1),endpoint:"",model:"",sourceLanguage:"en",targetLanguage:"zh-CN",timeoutSeconds:30,maxOutputTokens:2048,temperature:0,concurrency:2,enabled:false,hasKey:false,keyHint:""};
export const dictionary = {status:"ready",dictionaryVersion:`dict_${"a".repeat(64)}`,tableName:"fixture",rows:2,ref:"fixture",repositoryUrl:"https://example.invalid",license:"MIT",error:null};
export function documentFixture(n=1, kind="tags") {
  const rawText = kind === "tags" ? "cat, cat, dog" : "😀 A\r\nline\r\n \t\r\nSecond";
  const doc = {libraryId:id("lib",1),documentId:id("doc",n),group:"fixture",path:`fixture-${n}.txt`,format:"txt",sourceHash:hash(n),sourceVersion:id("ver",n),generation:1,bytes:new TextEncoder().encode(rawText).length,bom:false,newline:kind === "tags" ? "none" : "crlf",finalNewline:false,rawText,bindings:[],fields:[],readOnly:false,readOnlyReason:null};
  const recordId=id("rec",n), pointer="";
  const ranges=kind === "tags" ? [[0,3],[5,8],[10,13]] : [[0,10],[16,22]];
  const segments=ranges.map(([start,end],ordinal)=>({segmentId:id("seg",n*100+ordinal),recordId,pointer,ordinal,kind,text:rawText.slice(start,end),sourceHash:doc.sourceHash,sourceVersion:doc.sourceVersion,generation:1,rawRanges:[{start,end}],valueRange:{start,end},elementIndex:null}));
  doc.fields=[{recordId,pointer,type:"string",kind,delimiter:kind === "tags" ? "comma" : null,value:rawText,rawRange:{start:0,end:rawText.length},segments}];
  doc.bindings=[{bindingId:id("bind",n),bindingRevision:1,itemIds:[id("item",n)],recordIds:[recordId],status:"confirmed",spec:{format:"txt",kind,delimiter:kind === "tags" ? "comma" : null,mapping:{mode:"sidecar"},fields:[{pointer,type:"string"}]}}];
  return doc;
}
export function structuredFixture() {
  const doc=documentFixture(3); doc.format="jsonl"; doc.path="shared.jsonl"; doc.newline="crlf"; doc.finalNewline=true; doc.fields=[];
  const lines=[]; let offset=0;
  for (let record=0;record<2;record++) {
    const line=`{"id":"r${record}","image":"fixture-${record}.png","tags":["cat","cat"],"caption":"😀\\nA","keep":1e+02}`;
    const recordId=id("rec",30+record), start=offset+line.indexOf('["cat"');
    const segments=[0,1].map(ordinal=>({segmentId:id("seg",300+record*10+ordinal),recordId,pointer:"/tags",ordinal,kind:"tags",text:"cat",sourceHash:doc.sourceHash,sourceVersion:doc.sourceVersion,generation:1,rawRanges:[{start:start+2+ordinal*6,end:start+5+ordinal*6}],valueRange:{start:0,end:3},elementIndex:ordinal}));
    doc.fields.push({recordId,pointer:"/tags",type:"string_array",kind:"tags",delimiter:null,value:["cat","cat"],rawRange:{start,end:start+13},segments});
    const captionStart=offset+line.indexOf('"😀'), value="😀\nA";
    doc.fields.push({recordId,pointer:"/caption",type:"string",kind:"nl",delimiter:null,value,rawRange:{start:captionStart,end:captionStart+7},segments:[{segmentId:id("seg",305+record*10),recordId,pointer:"/caption",ordinal:0,kind:"nl",text:value,sourceHash:doc.sourceHash,sourceVersion:doc.sourceVersion,generation:1,rawRanges:[{start:captionStart+1,end:captionStart+6}],valueRange:{start:0,end:4},elementIndex:null}]});
    lines.push(line); offset+=line.length+2;
  }
  doc.rawText=lines.join("\r\n")+"\r\n"; doc.bytes=new TextEncoder().encode(doc.rawText).length;
  doc.bindings[0].recordIds=[id("rec",30),id("rec",31)];
  doc.bindings[0].spec={format:"jsonl",kind:"tags",delimiter:null,mapping:{mode:"records",recordsPointer:"",recordIdPointer:"/id",imagePointer:"/image"},fields:[{pointer:"/tags",type:"string_array"}]};
  doc.bindings.push({...clone(doc.bindings[0]),bindingId:id("bind",31),spec:{...clone(doc.bindings[0].spec),kind:"nl",fields:[{pointer:"/caption",type:"string"}]}});
  return doc;
}
export function translationSegment(s, overrides={}) {
  return {segmentId:s.segmentId,sourceHash:s.sourceHash,sourceVersion:s.sourceVersion,generation:s.generation,recordId:s.recordId,pointer:s.pointer,ordinal:s.ordinal,segmentRevision:1,sourceText:s.text,targetText:`译${s.ordinal}`,status:"translated",engine:"dictionary",dictionaryVersion:dictionary.dictionaryVersion,configVersion:null,model:null,sourceLanguage:"en",targetLanguage:"zh-CN",manual:false,staleReason:null,updatedAt:10,code:null,...overrides};
}
export function viewFixture(doc, overrides={}) {
  return {documentId:doc.documentId,sourceLanguage:"en",targetLanguage:"zh-CN",sourceVersion:doc.sourceVersion,generation:doc.generation,revision:4,snapshotRevision:2,snapshotSourceVersion:doc.sourceVersion,snapshotGeneration:doc.generation,segments:doc.fields.flatMap(f=>f.segments.map(s=>translationSegment(s))),historyCount:0,history:[],...overrides};
}
export function planFixture(doc) {
  return {planId:id("plan",1),requestId:id("req",1),status:"ready",engine:"dictionary",scope:{mode:"page_selected",snapshotId:id("snap",1),pageOffset:0,itemIds:[id("item",1)]},sourceLanguage:"en",targetLanguage:"zh-CN",dictionaryVersion:dictionary.dictionaryVersion,configVersion:null,model:null,replaceManual:false,targetSegments:doc.fields[0].segments.length,sourceCharacters:doc.rawText.length,cacheHits:0,remoteBatches:0,maximumOutputTokens:0,manifestHash:hash(999),createdAt:1,expiresAt:601};
}
export function targetFixture(doc,s) { return {documentId:doc.documentId,itemIds:[id("item",1)],segment:clone(s),expectedTranslationRevision:4,expectedSegmentRevision:1,status:"ready",code:null}; }
export function jobFixture(status="running",kind="translation") { return {jobId:id("job",1),planId:kind === "dictionary" ? null : id("plan",1),kind,status,total:3,processed:0,written:0,skipped:0,failed:0,currentDocumentId:null,cancelRequested:false,createdAt:1,updatedAt:1,finishedAt:null,error:null,undoAvailable:false}; }
