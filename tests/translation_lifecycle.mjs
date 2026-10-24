/* Run: node tests/translation_lifecycle.mjs. Synthetic wire only, real transport
 * and controller; never starts a service or contacts provider/download endpoints. */
import assert from "node:assert/strict";
import {readFile,writeFile,mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {pathToFileURL} from "node:url";
import {webcrypto} from "node:crypto";
import {installDOM} from "./grabber_dom.mjs";
import {id,hash,clone,settings,dictionary,documentFixture,structuredFixture,translationSegment,viewFixture,planFixture,targetFixture,jobFixture} from "./translation_fixture.mjs";
const web=new URL("../src/kohakumoss_ui/web/",import.meta.url), tmp=await mkdtemp(join(tmpdir(),"translation-lifecycle-"));
const stats={rootWrites:0,removed:0,imageSources:[]},dom=installDOM(stats),timers=new Map();let timerId=0;
if (!globalThis.crypto) globalThis.crypto=webcrypto;
globalThis.setTimeout=fn=>{timers.set(++timerId,fn);return timerId;};globalThis.clearTimeout=n=>timers.delete(n);
const listeners=new Map();globalThis.window={addEventListener:(t,f)=>listeners.set(t,f),removeEventListener:t=>listeners.delete(t)};
const esc=v=>String(v??"").replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;").replaceAll(">","&gt;");
const words=JSON.parse(await readFile(new URL("lang/en.json",web),"utf8"));
const gt=k=>{assert.ok(Object.hasOwn(words,`grabber.${k}`),`missing i18n: ${k}`);return words[`grabber.${k}`];};
const zh=JSON.parse(await readFile(new URL("lang/zh.json",web),"utf8"));
assert.deepEqual(Object.keys(words).sort(),Object.keys(zh).sort(),"bilingual key parity");
// These assertions validate actual fixture ranges/decoded values, not just shape.
for (const doc of [documentFixture(),documentFixture(2,"nl"),structuredFixture()]) {
  for(const f of doc.fields) {
    if(doc.format === "jsonl") assert.deepEqual(JSON.parse(doc.rawText.slice(f.rawRange.start,f.rawRange.end)),f.value);
    for(const s of f.segments) {
      const value=f.type === "string_array" ? f.value[s.elementIndex] : f.value;
      assert.equal(value.slice(s.valueRange.start,s.valueRange.end),s.text);
      const raw=s.rawRanges.map(r=>doc.rawText.slice(r.start,r.end)).join("");
      assert.equal(doc.format === "jsonl" ? JSON.parse(`"${raw}"`) : raw,s.text);
    }
  }
}
assert.deepEqual(documentFixture(2,"nl").fields[0].segments.map(s=>s.text),["😀 A\r\nline","Second"]);
const tick=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const draft=(h,id,value)=>h.c.capture({dataset:{scope:"translation",field:`manual:${id}`},value});
let count=0;
try {
  for(const name of ["grabber-translation","annotation-api"]) await writeFile(join(tmp,`${name}.mjs`),(await readFile(new URL(`${name}.js`,web),"utf8")).replace('"./api.js"','"./api.mjs"'));
  await writeFile(join(tmp,"api.mjs"),'export const TOKEN="synthetic-fixture";');
  const {createTranslation}=await import(pathToFileURL(join(tmp,"grabber-translation.mjs")));
  const {createAnnotationClient}=await import(pathToFileURL(join(tmp,"annotation-api.mjs")));
  function harness(doc=documentFixture()) {
    const root=document.createElement("section");dom.mount(root);
    let currentDoc=doc,renderCount=0,wireView=viewFixture(doc),wireSettings=clone(settings),wireJob=jobFixture();
    const requests=[],delays=new Map(),client=createAnnotationClient();
    globalThis.fetch=async(url,options)=>{
      const parsed=new URL(url,"http://fixture.invalid"),route=parsed.pathname.replace("/api/translation/",""),method=options.method;
      assert.ok(parsed.pathname.startsWith("/api/translation/"));assert.equal(options.headers["X-UI-Token"],"synthetic-fixture");
      const payload=method === "GET" ? Object.fromEntries(parsed.searchParams) : JSON.parse(options.body);
      if(method === "POST")assert.match(payload.requestId,/^req_[a-f0-9]{32}$/);
      requests.push({method,route,payload});
      const wait=delays.get(`${method}:${route}`);let data;
      if(wait) data=await wait.promise;
      else if(route === "settings") {if(method === "POST") {const {requestId,expectedConfigVersion,...draft}=payload;wireSettings={...wireSettings,...draft,configVersion:id("cfg",2)};}data=wireSettings;}
      else if(route === "dictionary")data=dictionary;
      else if(route === "view") {assert.ok(payload.sourceLanguage && payload.targetLanguage);data=wireView;}
      else if(route === "plans")data=planFixture(currentDoc);
      else if(route === "plans/segments")data={planId:payload.planId,total:currentDoc.fields[0].segments.length,offset:Number(payload.offset),limit:60,segments:currentDoc.fields[0].segments.map(s=>targetFixture(currentDoc,s))};
      else if(route === "jobs/start" || route === "jobs/stop")data={job:wireJob};
      else if(route === "jobs")data=wireJob;
      else if(route === "jobs/segments")data={jobId:payload.jobId,total:0,offset:Number(payload.offset),limit:60,segments:[]};
      else if(route === "manual") {const source=currentDoc.fields.flatMap(f=>f.segments).find(s=>s.segmentId===payload.segmentId);data={revision:payload.expectedRevision+1,segment:translationSegment(source,{engine:"manual",manual:true,dictionaryVersion:null,targetText:payload.targetText,segmentRevision:2})};}
      else if(route === "credentials")data={configVersion:id("cfg",2),hasKey:!payload.clear,keyHint:payload.clear?"":"configured"};
      else if(route.startsWith("dictionary/"))data={job:jobFixture("running","dictionary")};
      else throw Error(`unexpected wire ${method} ${route}`);
      return {ok:true,status:200,json:async()=>({ok:true,data:clone(data)})};
    };
    const c=createTranslation({root,api:client.translation,esc,gt,alive:()=>true,document:()=>currentDoc,onChange:()=>renderCount++});
    function render(field=currentDoc.fields[0],raw=false) {
      c.setWindow({key:`${currentDoc.documentId}:${field.recordId}:${field.pointer}`,segments:field.segments});
      const source=field.segments.map(s=>`<p data-side="source" data-segment-id="${s.segmentId}" tabindex="0">${esc(s.text)}</p>`).join("");
      root.innerHTML=`<img src="fixture.png">${source}<textarea data-field="${raw?"rawText":"source"}" data-record-id="${field.recordId}" data-pointer="${field.pointer}"></textarea>${c.pane()}`;
      root.querySelector(`[data-field="${raw?"rawText":"source"}"]`).value=(raw?currentDoc.rawText:typeof field.value === "string" ? field.value : "").replaceAll("\r\n","\n").replaceAll("\r","\n");
    }
    const event=(type,segmentId,side="source",relatedTarget=null)=>c.highlight({type,target:root.querySelectorAll("[data-segment-id]").find(n=>n.dataset.segmentId===segmentId && n.dataset.side===side),relatedTarget});
    const linked=()=>root.querySelectorAll(".annotation-linked").map(n=>`${n.dataset.side}:${n.dataset.segmentId}`).sort();
    return {c,root,requests,delays,event,linked,render,get paints(){return renderCount;},get doc(){return currentDoc;},set doc(d){currentDoc=d;wireView=viewFixture(d);},set view(v){wireView=v;},set job(j){wireJob=j;},dispose(){c.dispose();client.dispose();}};
  }
  async function test(name,fn){const h=harness();try{await h.c.load();h.render();await fn(h);console.log(`ok ${++count} - ${name}`);}finally{h.dispose();assert.equal(timers.size,0,"no timers leaked");}}
  await test("dual direction focus dominates hover; independent leave/blur; no render/API/image change",async h=>{
    const [a,b]=h.doc.fields[0].segments,paints=h.paints,calls=h.requests.length,writes=stats.rootWrites,img=h.root.querySelector("img");
    h.event("pointerover",a.segmentId);assert.equal(h.linked().length,2);
    h.event("focusin",b.segmentId,"translation");assert.ok(h.linked().every(s=>s.endsWith(b.segmentId)));
    h.event("pointerleave");assert.equal(h.linked().length,2);
    h.event("pointerover",a.segmentId,"translation");h.event("blur");assert.ok(h.linked().every(s=>s.endsWith(a.segmentId)));
    h.event("pointerout",a.segmentId,"translation");assert.deepEqual(h.linked(),[]);
    assert.equal(h.paints,paints);assert.equal(h.requests.length,calls);assert.equal(stats.rootWrites,writes);assert.equal(h.root.querySelector("img"),img);
  });
  await test("reject complete segment identity mismatches and omissions",async h=>{
    const original=clone(h.c.state.view),sid=h.doc.fields[0].segments[0].segmentId;
    for(const [key,value] of Object.entries({segmentId:id("seg",999),recordId:id("rec",999),pointer:"/wrong",ordinal:9,sourceHash:hash(999),sourceVersion:id("ver",999),generation:2,sourceLanguage:"ja",targetLanguage:"de",sourceText:"same-looking",segmentRevision:null})) {
      for(const missing of [false,true]) {h.c.state.view=clone(original);if(missing)delete h.c.state.view.segments[0][key];else h.c.state.view.segments[0][key]=value;h.event("pointerover",sid);assert.deepEqual(h.linked(),[],`${key} missing=${missing}`);}
    }
  });
  await test("reject document/library/hash, duplicate IDs, view language and snapshot mismatches",async h=>{
    const original=clone(h.c.state.view),sid=h.doc.fields[0].segments[0].segmentId;
    for(const [key,value] of Object.entries({documentId:id("doc",999),sourceVersion:id("ver",999),generation:2,sourceLanguage:"ja",targetLanguage:"de",snapshotRevision:null,snapshotSourceVersion:id("ver",999),snapshotGeneration:2})) {
      h.c.state.view=clone(original);h.c.state.view[key]=value;h.event("pointerover",sid);assert.deepEqual(h.linked(),[],key);
    }
    h.c.state.view=clone(original);h.c.state.view.segments.push(clone(original.segments[0]));h.event("pointerover",sid);assert.deepEqual(h.linked(),[]);
    for(const key of ["libraryId","documentId","sourceHash"]) {h.c.state.view=clone(original);const old=h.doc[key];h.doc[key]="changed";h.event("pointerover",sid);assert.deepEqual(h.linked(),[],key);h.doc[key]=old;}
  });
  await test("source draft/stale clears; manual draft only keeps legal translated linkage",async h=>{
    const sid=h.doc.fields[0].segments[0].segmentId,s=h.c.state.view.segments[0];
    const draft={dataset:{scope:"translation",field:`manual:${sid}`},value:"draft"};
    h.event("focusin",sid);h.c.capture(draft);assert.equal(h.linked().length,2);
    for(const status of ["missing","failed"]) {s.status=status;h.c.capture(draft);assert.deepEqual(h.linked(),[]);}
    s.status="stale";for(const reason of ["dictionary_changed","settings_changed","language_changed"]) {s.staleReason=reason;h.event("pointerover",sid);assert.equal(h.linked().length,2);}
    s.staleReason="source_changed";h.event("pointerover",sid);assert.deepEqual(h.linked(),[]);
    s.status="translated";s.staleReason=null;h.event("pointerover",sid);h.c.state.sourceDirty=true;h.c.clearHighlight();assert.deepEqual(h.linked(),[]);assert.match(h.root.querySelector("#annotation-linked-description").textContent,/disabled/);
  });
  await test("paragraph-v1 CRLF decoded selection and raw JSONL across records never focus",async h=>{
    h.c.reset();h.doc=documentFixture(2,"nl");await h.c.load();h.render();
    const nl=h.doc.fields[0],sentinel=h.root.querySelector("img");sentinel.focus();
    const editor=h.root.querySelector('[data-field="source"]');
    const sourcePicker=document.createElement("select");
    sourcePicker.setAttribute("data-field","source");sourcePicker.value="danbooru";
    h.root.replaceChildren(sourcePicker,...[...h.root.children]);
    h.event("pointerover",nl.segments[1].segmentId,"translation");
    assert.equal(editor.selectionStart,nl.value.slice(0,nl.segments[1].valueRange.start).replaceAll("\r\n","\n").length);assert.equal(document.activeElement,sentinel);
    assert.equal(sourcePicker.selectionStart,null,"collector source selector is not the annotation editor");
    h.c.reset();h.doc=structuredFixture();await h.c.load();
    for(const field of h.doc.fields) {
      h.render(field,true);const editor=h.root.querySelector('[data-field="rawText"]');
      for(const s of field.segments){h.event("pointerover",s.segmentId,"translation");assert.equal(h.linked().length,2);assert.equal(editor.selectionStart,h.doc.rawText.slice(0,s.rawRanges[0].start).replaceAll("\r\n","\n").length);}
    }
    const field=h.doc.fields[2];h.render(field);h.event("pointerover",field.segments[1].segmentId);assert.equal(h.root.querySelector('[data-field="source"]').selectionStart,null,"array does not use string valueRange on unrelated editor");
  });
  await test("late start after reset/ABA/dispose never attaches job or schedules poll",async h=>{
    for(const mode of ["reset","aba","dispose"]) {
      if(mode!=="reset"){await h.c.load();h.render();}
      h.c.state.plan=planFixture(h.doc);h.c.state.planId=h.c.state.plan.planId;
      const d=deferred();h.delays.set("POST:jobs/start",d);const pending=h.c.start();await tick();
      const old=h.doc;h.c.reset();if(mode==="aba"){h.doc=documentFixture(2);h.c.reset();h.doc=old;}if(mode==="dispose")h.c.dispose();
      const paints=h.paints;d.resolve({job:jobFixture()});assert.equal(await pending,false);assert.equal(h.c.state.job,null);assert.equal(h.c.state.jobId,null);assert.equal(h.paints,paints);assert.equal(timers.size,0);
    }
  });
  await test("late job GET and results after reset/dispose are ignored",async h=>{
    for(const route of ["jobs","jobs/segments"]) {
      h.c.state.job=jobFixture();h.c.state.jobId=id("job",1);const d=deferred();h.delays.set(`GET:${route}`,d);const pending=h.c.poll();await tick();h.c.reset();const paints=h.paints;
      d.resolve(route === "jobs"?jobFixture():{jobId:id("job",1),total:0,offset:0,limit:60,segments:[]});await pending;
      assert.equal(h.c.state.results,null);assert.equal(h.paints,paints);assert.equal(timers.size,0);h.delays.clear();
    }
  });
  await test("plan and result pages reject late selection, wrong identity and out-of-order replies",async h=>{
    h.c.state.plan=planFixture(h.doc);h.c.state.planId=id("plan",1);
    const d=deferred();h.delays.set("GET:plans/segments",d);const p=h.c.action("translation-target-page",{dataset:{offset:"60"}});await tick();h.c.invalidateSelection();d.resolve({planId:id("plan",1),total:61,offset:60,limit:60,segments:[]});await p;assert.equal(h.c.state.targets,null);
    h.c.state.job=jobFixture();h.c.state.jobId=id("job",1);const a=deferred(),b=deferred();h.delays.set("GET:jobs/segments",a);const pa=h.c.action("translation-result-page",{dataset:{offset:"60"}});await tick();h.delays.set("GET:jobs/segments",b);const pb=h.c.action("translation-result-page",{dataset:{offset:"120"}});await tick();b.resolve({jobId:id("job",1),total:121,offset:120,limit:60,segments:[]});await pb;a.resolve({jobId:id("job",1),total:121,offset:60,limit:60,segments:[]});await pa;assert.equal(h.c.state.results.offset,120);
    h.delays.set("GET:jobs/segments",{promise:Promise.resolve({jobId:id("job",999),total:121,offset:60,limit:60,segments:[]})});await h.c.action("translation-result-page",{dataset:{offset:"60"}});assert.equal(h.c.state.results.offset,120);assert.match(h.c.state.error,/UPSTREAM_PROTOCOL/);
  });
  await test("language capture invalidates pending plan and cost consent",async h=>{
    const d=deferred();h.delays.set("POST:plans",d);const p=h.c.plan(planFixture(h.doc).scope);await tick();h.c.capture({dataset:{scope:"translation",field:"settings:targetLanguage"},value:"ja"});d.resolve(planFixture(h.doc));assert.equal(await p,false);assert.equal(h.c.state.plan,null);assert.equal(h.c.state.confirmCost,false);
  });
  await test("late settings, credential, dictionary receipts cannot revive reset/disposed state",async h=>{
    for(const [act,route,result] of [["save-settings","settings",settings],["save-key","credentials",{configVersion:id("cfg",2),hasKey:true,keyHint:"configured"}],["dictionary-import","dictionary/import",{job:jobFixture("running","dictionary")}]] ) {
      h.render();const input=h.root.querySelector('[data-field="apiKey"]');input.value="never-store-this";
      const d=deferred();h.delays.set(`POST:${route}`,d);const p=h.c.action(`translation-${act}`,{});await tick();if(act==="save-key")assert.equal(input.value,"");h.c.reset();const snapshot=JSON.stringify(h.c.state),paints=h.paints;d.resolve(result);await p;assert.equal(JSON.stringify(h.c.state),snapshot);assert.equal(h.paints,paints);assert.ok(!snapshot.includes("never-store-this"));h.delays.clear();
    }
  });
  await test("manual uses root revision, preserves newer draft and rejects late/invalid response",async h=>{
    const sid=h.doc.fields[0].segments[0].segmentId;draft(h,sid,"first");const d=deferred();h.delays.set("POST:manual",d);const p=h.c.saveManual();await tick();assert.equal(h.requests.at(-1).payload.expectedRevision,4);draft(h,sid,"newer");d.resolve({revision:5,segment:translationSegment(h.doc.fields[0].segments[0],{engine:"manual",manual:true,dictionaryVersion:null,targetText:"first"})});assert.equal(await p,true);assert.equal(h.c.state.drafts.get(sid),"newer");assert.equal(h.c.state.view.revision,5);
    const bad=deferred();h.delays.set("POST:manual",bad);const invalid=h.c.saveManual();await tick();bad.resolve({revision:6,segment:translationSegment(h.doc.fields[0].segments[0],{recordId:id("rec",999),engine:"manual",manual:true})});assert.equal(await invalid,false);assert.equal(h.c.state.view.revision,5);assert.equal(h.c.state.drafts.get(sid),"newer");
    const late=deferred();h.delays.set("POST:manual",late);const pending=h.c.saveManual();await tick();h.c.reset();const paints=h.paints;late.resolve({revision:6,segment:translationSegment(h.doc.fields[0].segments[0],{engine:"manual",manual:true})});assert.equal(await pending,false);assert.equal(h.c.state.view,null);assert.equal(h.paints,paints);
  });
  await test("late view rejects mutated library context; invalid snapshot rejected on load",async h=>{
    const d=deferred();h.delays.set("GET:view",d);const p=h.c.load();await tick();h.doc.libraryId=id("lib",99);const paints=h.paints;d.resolve(viewFixture(h.doc));await p;assert.equal(h.paints,paints);
    h.delays.clear();h.view=viewFixture(h.doc,{snapshotGeneration:null});await h.c.load();assert.equal(h.c.state.view,null);assert.match(h.c.state.error,/UPSTREAM_PROTOCOL/);
  });
  await test("successful plan/start uses cost bounds and one poll; uncertain POST has no automatic retry",async h=>{
    assert.equal(await h.c.plan(planFixture(h.doc).scope),true);assert.ok(h.c.state.targets.segments[0].expectedSegmentRevision===1);
    assert.equal(await h.c.start(),true);assert.equal(h.c.state.plan.status,"consumed");assert.equal(timers.size,1);
    const request=h.requests.find(r=>r.route === "jobs/start");assert.equal(request.payload.confirmRemote,false);assert.equal(request.payload.confirmCost,false);assert.equal(request.payload.acceptMaximumOutputTokens,0);
    h.c.reset();h.c.state.plan=planFixture(h.doc);const d=deferred();h.delays.set("POST:jobs/start",d);const p=h.c.start();await tick();d.reject(new TypeError("offline"));assert.equal(await p,false);assert.equal(h.requests.filter(r=>r.route === "jobs/start").length,2);assert.equal(timers.size,0);
  });
  await test("manual receipt supersedes GET started during save; missing draft becomes linked only after valid save",async h=>{
    const source=h.doc.fields[0].segments[0],sid=source.segmentId;h.c.state.view.segments[0].status="missing";draft(h,sid,"manual");h.event("pointerover",sid);assert.deepEqual(h.linked(),[]);
    const m=deferred();h.delays.set("POST:manual",m);const save=h.c.saveManual();await tick();const v=deferred();h.delays.set("GET:view",v);const load=h.c.load();await tick();
    m.resolve({revision:5,segment:translationSegment(source,{engine:"manual",manual:true,dictionaryVersion:null,targetText:"manual"})});assert.equal(await save,true);v.resolve(viewFixture(h.doc));await load;assert.equal(h.c.state.view.revision,5);h.event("pointerover",sid);assert.equal(h.linked().length,2);
  });
  await test("orphan manual draft is never POSTed; view and manual errors after dispose stay silent",async h=>{
    draft(h,id("seg",999),"orphan");const before=h.requests.length;assert.equal(await h.c.saveManual(),false);assert.equal(h.requests.length,before);h.c.state.drafts.clear();
    const d=deferred();h.delays.set("POST:manual",d);draft(h,h.doc.fields[0].segments[0].segmentId,"draft");const p=h.c.saveManual();await tick();h.c.dispose();const paints=h.paints;d.reject(new Error("late"));assert.equal(await p,false);assert.equal(h.c.state.error,"");assert.equal(h.paints,paints);
  });
  await test("late dictionary poll and target page after dispose cannot commit or schedule",async h=>{
    const d=deferred();h.delays.set("GET:jobs",d);const p=h.c.action("translation-dictionary-import",{});await tick();h.c.dispose();const paints=h.paints;d.resolve(jobFixture("running","dictionary"));await p;assert.equal(h.c.state.dictionaryJob,null);assert.equal(h.paints,paints);assert.equal(timers.size,0);
  });
  await test("late target page disposal and late page rejection do not overwrite current state",async h=>{
    h.c.state.plan=planFixture(h.doc);h.c.state.planId=id("plan",1);const d=deferred();h.delays.set("GET:plans/segments",d);const p=h.c.action("translation-target-page",{dataset:{offset:"60"}});await tick();h.c.dispose();const paints=h.paints;d.resolve({planId:id("plan",1),total:61,offset:60,limit:60,segments:[]});await p;assert.equal(h.c.state.targets,null);assert.equal(h.paints,paints);
  });
  await test("old page rejection cannot replace newer successful page error state",async h=>{
    h.c.state.job=jobFixture();h.c.state.jobId=id("job",1);const d=deferred();h.delays.set("GET:jobs/segments",d);const p=h.c.action("translation-result-page",{dataset:{offset:"60"}});await tick();h.delays.delete("GET:jobs/segments");await h.c.action("translation-result-page",{dataset:{offset:"0"}});d.reject(new Error("old page"));await p;assert.equal(h.c.state.error,"");assert.equal(h.c.state.results.offset,0);
  });
  await test("unsaved manual draft cannot migrate into another language view",async h=>{
    const sid=h.doc.fields[0].segments[0].segmentId;draft(h,sid,"Chinese draft");
    h.c.capture({dataset:{scope:"translation",field:"settings:targetLanguage"},value:"ja"});
    const view=viewFixture(h.doc,{targetLanguage:"ja"});for(const s of view.segments)s.targetLanguage="ja";h.view=view;await h.c.load();h.render();
    h.event("pointerover",sid);assert.deepEqual(h.linked(),[]);assert.notEqual(h.root.querySelector(`[data-field="manual:${sid}"]`).value,"Chinese draft");
    const before=h.requests.length;assert.equal(await h.c.saveManual(),false);assert.equal(h.requests.length,before);assert.equal(h.c.state.drafts.get(sid),"Chinese draft");
  });
  await test("leave/enter invalidates old load even after a newer view is accepted",async h=>{
    const d=deferred();h.delays.set("GET:view",d);const p=h.c.load();await tick();h.event("focusin",h.doc.fields[0].segments[0].segmentId);h.c.leave();assert.deepEqual(h.linked(),[]);
    const n=h.requests.length;await h.c.load();await h.c.plan({});await h.c.start();assert.equal(h.requests.length,n,"hidden views cannot issue requests");
    h.delays.clear();h.c.enter();await h.c.load();const view=h.c.state.view,paints=h.paints;d.resolve(viewFixture(h.doc));await p;assert.equal(h.c.state.view,view);assert.equal(h.paints,paints);
  });
  await test("old plan/start/target requests cannot revive after leave and cannot unlock a new request",async h=>{
    for(const route of ["plans","jobs/start","plans/segments"]) {
      h.c.state.plan=planFixture(h.doc);h.c.state.planId=h.c.state.plan.planId;
      const method=route === "plans/segments"?"GET":"POST",d=deferred();h.delays.set(`${method}:${route}`,d);
      const old=route === "plans"?h.c.plan(planFixture(h.doc).scope):route === "jobs/start"?h.c.start():h.c.action("translation-target-page",{dataset:{offset:"60"}});await tick();
      h.c.leave();h.c.enter();h.delays.clear();const next=deferred();h.delays.set("POST:plans",next);const newer=h.c.plan(planFixture(h.doc).scope);await tick();
      d.resolve(route === "plans"?planFixture(h.doc):route === "jobs/start"?{job:jobFixture()}:{planId:id("plan",1),total:61,offset:60,limit:60,segments:[]});await old;
      assert.equal(h.c.state.busy,true);assert.equal(h.c.state.plan,null);assert.equal(h.c.state.job,null);
      next.resolve(planFixture(h.doc));assert.equal(await newer,true);h.delays.clear();
    }
  });
  await test("leave pauses accepted jobs but preserves source/manual drafts and resumes GET only",async h=>{
    const sid=h.doc.fields[0].segments[0].segmentId;draft(h,sid,"keep draft");h.c.state.sourceDirty=true;h.c.state.job=jobFixture();h.c.state.jobId=id("job",1);await h.c.poll();
    const saved=h.c.state.view,job=h.c.state.job,posts=h.requests.filter(r=>r.method === "POST").length;
    h.c.leave();assert.equal(timers.size,0);assert.equal(h.c.state.view,saved);assert.equal(h.c.state.job,job);assert.equal(h.c.state.drafts.get(sid),"keep draft");assert.equal(h.c.state.sourceDirty,true);
    h.c.enter();await tick();assert.equal(h.c.state.jobId,id("job",1));assert.equal(timers.size,1);assert.equal(h.requests.filter(r=>r.method === "POST").length,posts);
  });
  await test("manual marker and escaped dictionary table candidates are visible",async h=>{
    Object.assign(h.c.state.view.segments[0],{manual:true,engine:"manual",dictionaryVersion:null});
    h.c.state.dictionaryJob={...jobFixture("error","dictionary"),error:{code:"DICTIONARY_TABLE_REQUIRED",message:"Choose a table",details:{tables:["table_one","<table_two>"]},retryable:false}};h.render();
    assert.equal(h.root.querySelector('[data-translation-manual]').textContent,gt("translation.manual"));
    assert.match(h.root.querySelector('[data-translation-tables]').textContent,/table_one, <table_two>/);assert.ok(h.root.innerHTML.includes("&lt;table_two&gt;"));
  });
  await test("dispose removes listeners and blocks all subsequent IO",async h=>{
    const n=h.requests.length;h.dispose();await h.c.load();await h.c.start();await h.c.plan({});await h.c.poll();await h.c.saveManual();await h.c.action("translation-save-settings",{});assert.equal(h.requests.length,n);assert.equal(h.root.listeners.size,0);assert.equal(listeners.size,0);
  });
  console.log(`PASS ${count} translation lifecycle groups (real annotation-api transport, full wire fixtures)`);
} finally {await rm(tmp,{recursive:true,force:true});}
