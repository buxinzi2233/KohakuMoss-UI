"""Static and dependency-free JS checks for the multi-source collector UI."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

WEB = Path(__file__).resolve().parents[1] / 'src' / 'kohakumoss_ui' / 'web'


def inline_annotations(script):
    translation = (WEB / "grabber-translation.js").read_text()
    annotations = (WEB / "grabber-annotations.js").read_text().replace("import { createTranslation } from \"./grabber-translation.js\";", translation)
    transport = (WEB / "annotation-api.js").read_text().replace("import { TOKEN } from \"./api.js\";", "const TOKEN = api.TOKEN || \"\";")
    return script.replace("import { createAnnotations } from \"./grabber-annotations.js\";", annotations).replace("import { createAnnotationClient } from \"./annotation-api.js\";", transport)


class GrabberUiTests(unittest.TestCase):
    def setUp(self):
        self.script = (WEB / 'grabber.js').read_text()

    def test_shared_layout_and_route(self):
        for marker in ('vhead vhead-sticky', 'readystrip surface', 'form newrun-head surface', 'gallery', 'data-act'):
            self.assertIn(marker, self.script)
        for forbidden in ('studio-split', 'grid2', 'localStorage', 'sessionStorage', 'setInterval', 'drawer'):
            self.assertNotIn(forbidden, self.script)
        shell = (WEB / 'app.js').read_text()
        self.assertIn('studio?.grabberView', shell)
        self.assertIn('case "grabber"', shell)
        self.assertIn('main.__studioCleanup?.()', shell)
        html = (WEB / 'index.html').read_text()
        order = re.findall(r'data-view="([^"]+)"', html)
        self.assertEqual(order, ['new', 'queue', 'runs', 'grabber', 'datasets', 'artifacts', 'profile', 'notebook', 'doctor', 'settings'])

    def test_lifecycle_and_contract_guards(self):
        for marker in ('clearTimeout(timer)', 'removeEventListener', 'epoch !== progressEpoch', 'epoch !== sourceEpoch', 'epoch !== tagEpoch', 'items: 0', 'result.ok === false', 'stopRequested = true', 'Number(snapshot.target) > 0'):
            self.assertIn(marker, self.script)
        for endpoint in ('/api/grabber/status', '/api/grabber/sources', '/api/grabber/progress', '/api/grabber/credentials', 'task/start', 'task/stop', 'artists/search', 'source/test', 'tagger/test', 'service/start'):
            self.assertIn(endpoint, self.script)
        self.assertIn('cfg.source === "danbooru"', self.script)
        self.assertIn('!remoteConsent', self.script)
        self.assertIn('rel="noopener noreferrer"', self.script)
        self.assertNotIn('/api/start', self.script)

    def test_local_styles_only(self):
        css = (WEB / 'app.css').read_text().split('/* Image collection:', 1)[1]
        selectors = re.findall(r'([^{}]+)\{', css.split('*/', 1)[1])
        for selector in selectors:
            part = selector.strip()
            # The at-rule prelude carries the viewport condition; the selectors
            # inside it are still checked for the .grabber scope below.
            if part.startswith('@'):
                continue
            for part in selector.split(','):
                self.assertTrue(part.strip().startswith('.grabber '), part)
        self.assertNotRegex(css, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotIn('backdrop-filter', css)

    def test_annotation_layout_and_action_copy(self):
        css = (WEB / 'app.css').read_text()
        self.assertIn('.grabber .annotation-columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)', css)
        self.assertRegex(css, r'@media \(max-width:760px\)\{\.grabber \.annotation-columns\{grid-template-columns:minmax\(0,1fr\)\}\}')
        script = (WEB / 'grabber-annotations.js').read_text()
        self.assertIn('tr("filteredTotal")', script)
        self.assertIn('node.dataset.field === "apiKey"', script)
        self.assertIn('else if (name === "apiKey") return true', (WEB / 'grabber-translation.js').read_text())
        self.assertIn('invalidate: invalidatePane', self.script)
        en = json.loads((WEB / 'lang/en.json').read_text())
        zh = json.loads((WEB / 'lang/zh.json').read_text())
        self.assertIn('kept', en['grabber.annotation.delete'].lower())
        self.assertIn('图片', zh['grabber.annotation.delete'])
        self.assertIn('grabber.annotation.filteredTotal', en)
        self.assertEqual(set(en), set(zh))

    def test_grabber_literal_dictionary_keys(self):
        en = json.loads((WEB / 'lang/en.json').read_text())
        zh = json.loads((WEB / 'lang/zh.json').read_text())
        script = self.script + (WEB / 'grabber-library.js').read_text() + (WEB / 'grabber-media.js').read_text()
        keys = re.findall(r'\b(?:gt|label)\("([a-zA-Z0-9_.-]+)"', script)
        for key in keys:
            self.assertIn('grabber.' + key, en)
            self.assertIn('grabber.' + key, zh)
        self.assertEqual(set(en), set(zh))

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_annotation_transport(self):
        result = subprocess.run(['node', str(Path(__file__).parent / 'annotation_transport.mjs')], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('annotation transport: PASS', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_translation_lifecycle(self):
        result = subprocess.run(['node', str(Path(__file__).parent / 'translation_lifecycle.mjs')], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('translation lifecycle groups', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_annotation_workbench_dom(self):
        result = subprocess.run(['node', str(Path(__file__).parent / 'annotation_dom.mjs')], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('annotation DOM:', result.stdout)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_workspace_behaviors(self):
        # Execute the complete view and real event handlers against a small DOM IO
        # double. No source/model requests, user service or browser storage involved.
        module = self.script.replace('import * as api from "./api.js";', 'const api = globalThis.testApi;').replace('import { esc } from "./format.js";', 'const esc = v => String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(\'"\', "&quot;");')
        module = module.replace('import { createLibrary } from "./grabber-library.js";', (WEB / 'grabber-library.js').read_text())
        module = module.replace('import { createGrabberMedia } from "./grabber-media.js";', (WEB / 'grabber-media.js').read_text())
        module = inline_annotations(module)
        module = module.replace('  render();\n  try { await connect(); }', '''  ctx.expose({ action, capture, change, poll, connect, render, body, validate, queryTypes, canSearch, library,
    state: () => ({cfg, tab, checked, sourceCheckMessage, message, messageOrigin, snapshot, taskConfig, items, logs, stopRequested, sourceValues, tagValues, pendingClears, progressKnown, sourceDrafts, selectedSources}) });
  render();
  try { await connect(); }''')
        runner = r'''
import assert from "node:assert/strict";
import {installDOM} from DOM_URL;
const stats = {rootWrites:0, removed:0, imageSources:[]};
const dom = installDOM(stats);
let queuedTimer = null;
globalThis.setTimeout = fn => { queuedTimer = fn; return 1; };
globalThis.clearTimeout = () => { queuedTimer = null; };
let view, root;
const main = {scrollTop:0, replaceChildren:(node) => { root=node; dom.mount(node); }};
// Exercise synchronous scroll restoration even when a parent replacement causes
// native anchoring. Real CSS geometry is covered by the offline browser fixture.
let scrollClamps = 0;
stats.paneReplaced = (host, hadDetail) => {
  if (hadDetail && host.querySelector('.grabber-media-detail')) {
    main.scrollTop = Math.max(0, main.scrollTop - 64); scrollClamps++;
  }
};
let online = true, supported = true, failStart = false, failSource = false, delayed = null, pendingResolve;
let progress = {status:"idle", items:[], logs:[]};
let retagJob = {status:"idle"}, librarySupported = true, retagSupported = true, delayedGroup = "", groupResolve;
let savedTags = false, failLibrary = false, failRetagProgress = false, delayRetag = false, retagResolve;
let libraryBulk = false, libraryDeleted = false;
// Independent 64-image fixture for navigation; never changes the existing 65-image checks.
let navigationItems = null, navigationDelay = null, navigationPending = null;
let navigationGroupsDelay = false, navigationGroupsResolve;
const navigationPage = (offset=0) => ({group:"alpha",total:navigationItems.length,offset,limit:60,items:navigationItems.slice(offset,offset+60)});
const libraryPage = (group,offset=0) => ({group,total:libraryDeleted ? 0 : group === "alpha" ? 65 : 1,offset,limit:60,items:libraryDeleted ? [] : Array.from({length:libraryBulk && group === "alpha" ? Math.min(60,65-offset) : 1},(_,i)=>({filename:group+(offset+i)+".png",image_url:"/files/"+group+"/sample"+(offset+i)+".png",tags:savedTags?["fresh_tag"]:["saved_tag"],has_tags:true}))});
const requests = [];
let sourceList = ["yandere","danbooru","openverse","pixiv","gelbooru","safebooru","konachan","twitter"].map(id => ({id,label:id,supports_artist_search:true,auth_fields:[{id:"password",secret:true},{id:"api_key",secret:true}]}));
globalThis.testApi = {
  get:async (path,args) => {
    requests.push({path,args});
    if (path.endsWith("/status")) return {running:online,available:true,error:online ? "" : "not started"};
    if (path.endsWith("/sources")) return {sources:sourceList,capabilities:supported ? {images_only:true,multi_source:true,library:librarySupported,retag:retagSupported} : {}};
    if (path.endsWith("/credentials")) return {values:args.source === "tagger:local_onnx" ? {model_path:"/models/saved.onnx",tags_path:"/models/saved.csv",general_threshold:0.42} : {},configured:[]};
    if (path.endsWith("/library/retag/progress")) { if(failRetagProgress) throw Error("retag network down"); if(delayRetag) { delayRetag=false; return new Promise(resolve=>{retagResolve=resolve;}); } return retagJob; }
    if (path.endsWith("/library/groups")) {
      if(failLibrary) throw Error("disk unavailable");
      const data={groups:[{name:"alpha",image_count:navigationItems?.length ?? 65,tagged_count:savedTags?65:3},{name:"beta",image_count:1,tagged_count:1}]};
      if(navigationGroupsDelay) { navigationGroupsDelay=false; return new Promise(resolve=>{navigationGroupsResolve=()=>resolve(data);}); }
      return data;
    }
    if (path.endsWith("/library/items")) {
      if(args.group === delayedGroup) { delayedGroup=""; return new Promise(resolve=>{groupResolve=resolve;}); }
      if(navigationItems && args.group === "alpha") {
        if(navigationDelay === args.offset) { navigationDelay=null; return new Promise((resolve,reject)=>{navigationPending={resolve,reject,args};}); }
        return navigationPage(args.offset);
      }
      return libraryPage(args.group,args.offset);
    }
    if (path.endsWith("/progress")) {
      if (delayed === "progress") { delayed=null; return new Promise(resolve => {pendingResolve=resolve;}); }
      return {...progress, items:progress.items.slice(args.items),logs:progress.logs.slice(args.logs),item_count:progress.items.length,log_count:progress.logs.length};
    }
    throw Error(path);
  },
  post:async (path,body) => {
    requests.push({path,body});
    if (delayed === path.split("/").slice(-2).join("/")) { delayed=null; return new Promise(resolve => {pendingResolve=resolve;}); }
    if (path.endsWith("/source/test")) return failSource ? {ok:false,error:"source refused"} : {ok:true,message:"source connected"};
    if (path.endsWith("/task/start")) {
      if (failStart) return {ok:false,error:"start refused"};
      progress={status:"running",active_source:body.sources[0],sources:body.sources.map(id=>({id,status:"running",done:0,target:body.count})),items:[],logs:[]}; return {ok:true};
    }
    if (path.endsWith("/models/scan")) return {directory:"/models",models:[{name:"paired",model_path:"/models/paired.onnx",tags_path:"/models/paired.csv"},{name:"unpaired",model_path:"/models/unpaired.onnx",tags_path:null}]};
    if (path.endsWith("/library/retag/start")) { retagJob={id:"job1",status:"running",group:body.group,total:65,processed:0,logs:[]}; return {ok:true,job:retagJob}; }
    if (path.endsWith("/library/retag/stop")) return {ok:true};
    if (path.endsWith("/task/stop")) return {ok:true};
    if (path.endsWith("/artists/search")) return {ok:true,artists:[{name:"candidate"}]};
    if (path.endsWith("/credentials")) return {ok:true,values:{},configured:[]};
    return {ok:true};
  }
};
const {grabberView} = await import(MODULE_URL);
const cleanup = await grabberView(main,{t:k=>k,expose:v=>{view=v;}});
function control(field,scope="cfg") { return root.querySelectorAll("input,select,textarea").find(n=>n.dataset.field===field && n.dataset.scope===scope); }
function button(act,tab) { return root.querySelectorAll("[data-act]").find(n=>n.dataset.act===act && (!tab || n.dataset.tab===tab)); }
async function click(act,tab) { const node=button(act,tab); assert.ok(node,act); await view.action({target:node}); }
async function fill(field,value,scope="cfg") {
  const node=control(field,scope); assert.ok(node,field); assert.equal(node.disabled,false,field+" editable");
  if(node.type === "checkbox") node.checked=value; else node.value=String(value);
  view.capture({target:node}); await view.change({target:node});
}
assert.equal(stats.rootWrites,1);
assert.equal(root.querySelectorAll("[data-act]").filter(n=>n.dataset.act==="tab").length,8);
assert.deepEqual(view.queryTypes(),["artist","character","tag"]);
await fill("artist","unchanged input");
const artistNode=control("artist"); artistNode.selectionStart=2; artistNode.selectionEnd=5; artistNode.focus();
const basicWrites=root.hosts.get("grabber-basic").writes;
await view.poll();
assert.equal(control("artist"),artistNode,"idle polling retains input node");
assert.equal(root.hosts.get("grabber-basic").writes,basicWrites);
assert.equal(document.activeElement,artistNode);
assert.equal(view.state().checked,false,"service readiness is not source verification");
online=false; await view.connect(); assert.equal(view.state().message,"not started");
online=true; await view.connect(); assert.equal(view.state().message,"");
assert.equal(view.state().checked,false,"service reconnect does not validate source");
await click("test-source");
assert.equal(view.state().checked,true);
assert.ok(root.hosts.get("grabber-ready").innerHTML.includes("grabber.checked"));
assert.ok(!root.hosts.get("grabber-message").innerHTML.includes("source connected"));
await fill("artist","new input"); assert.equal(view.state().checked,false);
failSource=true; await click("test-source"); assert.equal(view.state().message,"source refused");
failSource=false; await click("test-source"); assert.equal(view.state().message,"");
await fill("query_type","tag"); assert.equal(view.canSearch(),false); assert.equal(button("search").disabled,true);
const beforeSearch=requests.length; await click("search"); assert.equal(requests.length,beforeSearch,"yandere tag does not call artist search");
await fill("source","danbooru"); assert.equal(view.state().cfg.artist,""); await fill("artist","danbooru query"); await fill("query_type","tag"); assert.equal(view.canSearch(),true); await click("search");
assert.equal(requests.at(-1).body.query_type,"tag");
await fill("query_type","character"); assert.equal(view.canSearch(),true);
await fill("source","pixiv"); assert.equal(view.state().cfg.query_type,"artist");
await fill("source","openverse"); await fill("query_type","tag"); assert.equal(view.canSearch(),false);
assert.ok(root.hosts.get("grabber-basic").innerHTML.includes("query.keyword.hint"));
sourceList.find(s=>s.id==="openverse").query_types=["artist"];
sourceList.find(s=>s.id==="openverse").search_query_types=[];
await view.connect(); assert.deepEqual(view.queryTypes(),["artist"]); assert.equal(view.state().cfg.query_type,"artist"); assert.equal(view.canSearch(),false);
// Source drafts, including credentials and query mode, stay independent.
await fill("source","yandere"); assert.equal(view.state().cfg.artist,"new input"); assert.equal(view.state().cfg.query_type,"tag");
await click("tab","auth"); await fill("password","secret-y","source"); await fill("api_key","key-y","source");
await fill("danbooru",true,"selection");
await fill("source","danbooru"); assert.equal(view.state().cfg.artist,"danbooru query"); assert.equal(view.state().cfg.query_type,"character"); assert.equal(control("password","source").value,"");
await fill("password","secret-d","source"); await fill("api_key","key-d","source");
let multi=view.body(); assert.deepEqual(multi.sources,["yandere","danbooru"]); assert.ok(!("source" in multi)); assert.ok(!("artist" in multi)); assert.ok(!("password" in multi));
assert.equal(multi.source_configs.yandere.password,"secret-y"); assert.equal(multi.source_configs.danbooru.password,"secret-d");
assert.ok(!("api_key" in multi)); assert.equal(multi.source_configs.yandere.api_key,"key-y"); assert.equal(multi.source_configs.danbooru.api_key,"key-d");
await fill("source","yandere"); assert.equal(control("password","source").value,"secret-y");
await fill("twitter",true,"selection"); assert.throws(view.validate,/twitter.*validation.artist/);
await fill("twitter","own-account","mapping"); assert.doesNotThrow(view.validate);
await fill("twitter",false,"selection"); await fill("danbooru",false,"selection");
await fill("yandere",false,"selection"); assert.throws(view.validate,/multi.required/); await fill("yandere",true,"selection");
assert.equal(requests.filter(r=>r.path.endsWith("/library/groups")).length,0,"no automatic disk browse");
await click("tab","tagging"); await fill("tagger_id","openai_compatible");
await fill("api_key","test-secret","tag"); await fill("model","draft-model","tag");
assert.throws(view.validate,/remote.required/);
await click("tab","images"); await fill("images_only",true);
assert.equal(control("include_artist").disabled,true); assert.equal(control("skip_video").disabled,false);
assert.doesNotThrow(view.validate); const body=view.body();
assert.equal(body.images_only,true); assert.equal(body.tagger_id,"none"); assert.deepEqual(body.tagger_config,{}); assert.ok(!JSON.stringify(body).includes("test-secret"));
await click("tab","tagging"); assert.equal(control("tagger_id").disabled,true); assert.equal(button("test-tag").disabled,true); assert.equal(button("save-tag").disabled,true);
await click("tab","images"); await fill("images_only",false);
await click("tab","tagging"); assert.equal(control("model","tag").value,"draft-model"); assert.equal(control("api_key","tag").value,"test-secret");
await click("tab","images"); supported=false; await view.connect(); assert.equal(control("images_only").disabled,true); assert.ok(root.hosts.get("grabber-pane").innerHTML.includes("imagesOnly.unsupported"));
supported=true; await view.connect(); await fill("images_only",true);
failStart=true; await click("start"); assert.equal(view.state().snapshot.status,"idle"); assert.equal(view.state().cfg.artist,"new input"); assert.equal(view.state().tagValues.api_key,"test-secret");
await fill("danbooru",true,"selection"); failStart=false; await click("start"); assert.deepEqual(requests.findLast(r=>r.path.endsWith("/task/start")).body.sources,["yandere","danbooru"]); assert.equal(view.state().tab,"images","start does not force results tab"); assert.equal(stats.rootWrites,1);
assert.equal(view.state().snapshot.status,"running"); assert.equal(control("artist").disabled,true);
assert.ok(root.querySelectorAll("[data-act]").filter(n=>n.dataset.act==="tab").every(n=>!n.disabled));
assert.equal(view.state().taskConfig.tagger_id,"none");
assert.match(root.hosts.get("grabber-progress").innerHTML,/yandere/); assert.match(root.hosts.get("grabber-progress").innerHTML,/danbooru/);
assert.equal(view.state().taskConfig.source_configs.yandere.api_key,"[redacted]");
await click("tab","results"); assert.equal(control("filter","ui").disabled,false); await fill("filter","failed","ui");
await click("stop"); assert.equal(view.state().stopRequested,true); assert.equal(view.state().snapshot.status,"running");
progress={...progress,status:"done",items:[{id:1,status:"ok",final_tags:["merged"]}],logs:["finished"]};
await view.poll(); assert.equal(view.state().stopRequested,false); assert.equal(view.state().message,"");
assert.equal(view.state().items[0].final_tags[0],"merged");
assert.equal(requests.filter(r=>r.path === "/api/grabber/progress").at(-1).args.items,0,"terminal full item resync");
// Disk library uses paginated, independently fetched groups and explicit re-tagging.
await fill("danbooru",false,"selection");
await click("tab","library"); assert.equal(view.library.state.group,"alpha"); assert.equal(view.library.state.total,65);
assert.ok(root.hosts.get("grabber-pane").innerHTML.includes("/api/grabber/preview?"));
await click("library-next"); assert.equal(requests.filter(r=>r.path.endsWith("/library/items")).at(-1).args.offset,60);
await click("library-prev"); assert.equal(view.library.state.offset,0);
await click("library-detail"); assert.ok(root.hosts.get("grabber-pane").innerHTML.includes("saved_tag")); await click("library-back");
delayedGroup="alpha"; const staleGroup=view.library.load("alpha"); await Promise.resolve();
await fill("group","beta","library"); groupResolve(libraryPage("alpha")); await staleGroup;
assert.equal(view.library.state.group,"beta"); assert.equal(view.library.state.items[0].filename,"beta0.png");
await fill("group","alpha","library");
assert.equal(view.state().cfg.images_only,true); assert.equal(control("tagger_id").disabled,false,"explicit library model settings independent of images-only");
await fill("tagger_id","none"); const beforeMissing=requests.length; await click("library-start"); assert.equal(requests.length,beforeMissing); assert.match(view.state().message,/modelRequired/);
await fill("tagger_id","local_onnx"); assert.equal(control("model_path","tag").value,"/models/saved.onnx"); assert.equal(control("general_threshold","tag").value,"0.42");
assert.ok(root.hosts.get("grabber-pane").innerHTML.includes('<details data-details="onnx">'),"advanced defaults closed");
function checkCompatibility(inLibrary) {
  const info=root.querySelector('[data-onnx-compatibility]');
  assert.ok(info,"local ONNX compatibility present in shared form");
  assert.equal(info.hidden,false);
  assert.equal(info.closest('[data-details="onnx"]'),null,"compatibility is not hidden in ONNX advanced settings");
  assert.equal(Boolean(info.closest('[data-details="retag"]')),inLibrary);
  assert.equal(root.querySelector('[data-details="onnx"]').open,false);
  for(const key of ["compatibility","inputOutput","csvCategories","validationScope"]) assert.ok(info.textContent.includes("grabber.models."+key));
}
checkCompatibility(true);
await click("models-scan"); await fill("modelSelected","0","model"); assert.equal(control("tags_path","tag").value,"/models/paired.csv");
await fill("modelSelected","1","model"); assert.equal(control("tags_path","tag").value,""); assert.match(root.hosts.get("grabber-pane").innerHTML,/models.noCsv/);
await fill("tags_path","/models/manual.csv","tag");
const modelNode=control("tags_path","tag"); modelNode.focus(); const paneWrites=root.hosts.get("grabber-pane").writes; const diskReads=requests.filter(r=>r.path.endsWith("/library/items")).length;
await view.poll(); assert.equal(control("tags_path","tag"),modelNode); assert.equal(document.activeElement,modelNode); assert.equal(root.hosts.get("grabber-pane").writes,paneWrites); assert.equal(requests.filter(r=>r.path.endsWith("/library/items")).length,diskReads,"poll never re-fetches idle library items");
failRetagProgress=true; await view.poll(); assert.equal(button("library-start").disabled,true); assert.equal(button("start").disabled,true); failRetagProgress=false; await view.poll(); assert.equal(button("library-start").disabled,false);

await fill("mode","replace","library"); const beforeReplace=requests.length; await click("library-start"); assert.equal(requests.length,beforeReplace); assert.match(view.state().message,/replaceRequired/);
await fill("confirm_replace",true,"library");
delayRetag=true; const staleRetag=view.library.poll(); await Promise.resolve(); const oldRetag=retagJob;
await click("library-start"); retagResolve(oldRetag); await staleRetag; assert.equal(view.library.state.job.status,"running","stale status cannot overwrite accepted retag");
let retagRequest=requests.findLast(r=>r.path.endsWith("/library/retag/start")); assert.equal(retagRequest.body.tagger_id,"local_onnx"); assert.equal(retagRequest.body.tagger_config.tags_path,"/models/manual.csv"); assert.equal(retagRequest.body.confirm_replace,true); assert.equal(retagRequest.body.confirm_remote,false); assert.equal(view.state().cfg.images_only,true);
assert.equal(button("start").disabled,true,"collection and retag mutually exclusive"); assert.equal(view.state().message,"");
assert.match(root.hosts.get("grabber-pane").innerHTML,/data-act="library-stop"[^>]*>grabber\.library\.stop<\/button>/);
await click("library-stop"); assert.equal(view.library.state.stopping,true); assert.equal(view.library.state.job.status,"running");
assert.match(root.hosts.get("grabber-pane").innerHTML,/data-act="library-stop"[^>]*>grabber\.library\.stopping<\/button>/);
retagJob={...retagJob,status:"stopped",processed:2,written:1,failed:1,error:"tag failure",backup_dir:"/backups/job1",logs:["failure detail"]}; savedTags=true;
await view.poll(); assert.equal(view.library.state.stopping,false); assert.equal(view.library.state.items[0].tags[0],"fresh_tag");
assert.match(root.hosts.get("grabber-library-progress").innerHTML,/tag failure/); assert.match(root.hosts.get("grabber-library-progress").innerHTML,/backups/);
await fill("mode","merge","library"); await fill("tagger_id","openai_compatible"); assert.equal(control("model","tag").value,"draft-model");
const beforeRemote=requests.length; await click("library-start"); assert.equal(requests.length,beforeRemote); assert.match(view.state().message,/remote.required/);
await fill("confirm_remote",true,"library"); await click("library-start"); assert.equal(requests.findLast(r=>r.path.endsWith("/library/retag/start")).body.confirm_remote,true);
await fill("group","beta","library"); retagJob={...retagJob,id:"job2",status:"done",group:"alpha"};
const groupReads=requests.filter(r=>r.path.endsWith("/library/items")).length; await view.poll(); assert.equal(view.library.state.group,"beta"); assert.equal(requests.filter(r=>r.path.endsWith("/library/items")).length,groupReads,"completion must not refresh the other selected group");
failLibrary=true; await click("library-refresh"); assert.match(root.hosts.get("grabber-pane").innerHTML,/disk unavailable/); failLibrary=false;
librarySupported=false; await view.connect(); assert.match(root.hosts.get("grabber-pane").innerHTML,/library.unsupported/); librarySupported=true;
retagSupported=false; await view.connect(); assert.match(root.hosts.get("grabber-pane").innerHTML,/retagUnsupported/); retagSupported=true; await view.connect();
online=false; await view.connect(); await click("tab","library"); assert.match(root.hosts.get("grabber-pane").innerHTML,/service.hint/); online=true; await view.connect();
// Scanning is a UI-backend operation and works without the collection service.
await click("tab","images"); await fill("images_only",false); await click("tab","tagging"); await fill("tagger_id","local_onnx");
checkCompatibility(false);
online=false; await view.connect(); assert.equal(button("models-scan").disabled,false); await click("models-scan"); assert.ok(requests.some(r=>r.path.endsWith("/models/scan")));
online=true; await view.connect(); await click("tab","images"); await fill("images_only",true);
const oldConfig=JSON.stringify(view.state().taskConfig);
await click("tab","range"); await fill("artist","next draft"); await view.poll();
assert.equal(control("artist").value,"next draft"); assert.equal(JSON.stringify(view.state().taskConfig),oldConfig);
await click("tab","config"); assert.ok(!root.hosts.get("grabber-pane").innerHTML.includes("next draft"));
failStart=true; await click("start"); assert.equal(JSON.stringify(view.state().taskConfig),oldConfig); assert.equal(view.state().items.length,1); failStart=false;
// A stale terminal read cannot overwrite a newly accepted task, even if it was
// already polling when Start was clicked. The next normal poll catches up.
delayed="progress"; const stalePoll=view.poll(); await Promise.resolve(); const previous={...progress};
await click("start"); assert.equal(view.state().tab,"config"); assert.equal(view.state().snapshot.status,"preparing");
pendingResolve({...previous,item_count:1,log_count:1}); await stalePoll;
assert.equal(view.state().snapshot.status,"preparing"); await view.poll(); assert.equal(view.state().snapshot.status,"running");
assert.equal(view.state().items.length,0);
await click("tab","logs"); root.hosts.get("grabber-pane").logbox.scrollTop=17;
progress={...progress,logs:["second run"]}; await view.poll(); assert.equal(root.hosts.get("grabber-pane").logbox.scrollTop,17);
progress={...progress,status:"done"}; await view.poll();
await click("tab","auth"); await fill("password","delete me","source");
const clear=root.querySelectorAll("[data-clear]")[0]; clear.checked=true; view.capture({target:clear});
await click("tab","logs"); await click("tab","auth"); assert.equal(root.querySelectorAll("[data-clear]")[0].checked,true);
delayed="grabber/credentials"; const saving=click("save-source"); await Promise.resolve();
assert.ok(root.querySelectorAll("[data-act]").filter(n=>n.dataset.act==="tab").every(n=>!n.disabled));
await click("tab","logs"); await view.poll();
assert.deepEqual(requests.findLast(r=>r.path.endsWith("/credentials") && r.body).body.clear,["password"]);
assert.ok(!("password" in requests.findLast(r=>r.path.endsWith("/credentials") && r.body).body.values));
pendingResolve({ok:true,values:{},configured:[]}); await saving;
assert.equal(view.state().pendingClears.size,0);
// Unknown live task must not pretend the editable draft describes that task.
progress={status:"running",source:"yandere",artist:"someone else",items:[],logs:[]}; await view.poll();
assert.equal(view.state().taskConfig,null); assert.ok(root.hosts.get("grabber-basic").innerHTML.includes("config.unknown"));
await click("tab","images"); assert.ok(root.hosts.get("grabber-pane").innerHTML.includes("config.unknown")); assert.equal(control("count"),undefined);
// Real media nodes, not HTML-string counts: bounded results and stable identities.
await click("tab","results"); await fill("filter","all","ui");
const resultItem = i => ({id:i, filename:`image-${i}.png`, status:i%2 ? "ok" : "failed", image_url:`/files/results/image-${i}.png`, final_tags:["initial"], url:`https://art.example/${i}`});
progress={status:"running",items:Array.from({length:360},(_,i)=>resultItem(i)),logs:[]};
await view.poll();
const pane=()=>root.hosts.get("grabber-pane");
const images=()=>pane().querySelectorAll("img");
const details=()=>pane().querySelectorAll('[data-act="detail"]');
assert.equal(images().length,60); assert.equal(details().length,60);
assert.ok(images().every(image=>image.src.startsWith("/api/grabber/preview?") && image.src.includes("size=thumb") && image.loading==="lazy" && image.decoding==="async" && image.width==="384"));
const firstPage=images(); const sourceCount=stats.imageSources.length; const stableWrites=pane().writes;
await view.poll(); await view.poll();
assert.equal(pane().writes,stableWrites); assert.equal(stats.imageSources.length,sourceCount);
assert.equal(images()[0],firstPage[0]);
progress={...progress,done:17,logs:["background progress"]}; await view.poll();
assert.equal(pane().writes,stableWrites,"progress/logs never rebuild the visible gallery");
progress.items.push(resultItem(360)); await view.poll();
assert.equal(images().length,60); assert.equal(images()[0],firstPage[0],"new totals retain same image objects");
assert.equal(stats.imageSources.length,sourceCount,"new off-page results do not load images");
await click("results-next"); assert.equal(details()[0].dataset.index,"60");
const secondPage=images();
progress.items.push(resultItem(361)); await view.poll();
assert.equal(details()[0].dataset.index,"60","append must not reset page"); assert.equal(images()[0],secondPage[0]);
await fill("filter","ok","ui"); assert.equal(details()[0].dataset.index,"1","filter resets to first matching original index");
await click("results-next"); assert.equal(details()[0].dataset.index,"121");
const filteredPage=images();
await view.action({target:details()[0]});
assert.equal(images().length,1); assert.ok(images()[0].src.includes("image-121.png")); assert.ok(images()[0].src.includes("size=detail")); assert.equal(images()[0].decoding,"async");
assert.ok(pane().querySelectorAll("a").some(a=>a.getAttribute("href").startsWith("/api/grabber/file?") && a.getAttribute("rel")==="noopener noreferrer"));
assert.ok(pane().querySelectorAll("a").some(a=>a.getAttribute("href").startsWith("https://art.example/")),"external work and local full-size links remain distinct");
assert.ok(stats.imageSources.every(url=>!url.startsWith("/api/grabber/file?")),"original is never assigned to an image");
const detailNode=images()[0];
assert.equal(filteredPage[0].parentNode.parentNode,null,"cached media must not retain discarded gallery ancestors and siblings");
filteredPage[1].complete=true; filteredPage[1].naturalWidth=0;
main.scrollTop=600;
progress={...progress,status:"done",items:progress.items.map(item=>({...item,final_tags:["terminal-merged"]}))}; await view.poll();
assert.equal(main.scrollTop,600,"collection detail shares the scroll-safe patch");
assert.equal(images()[0],detailNode); assert.match(pane().innerHTML,/terminal-merged/);
await click("detail-close"); assert.equal(images()[0],filteredPage[0],"back restores decoded thumbnail nodes");
assert.equal(images()[1].hidden,true,"a preview failure while detached is displayed on return");
await view.action({target:details()[0]}); assert.equal(images()[0],detailNode,"reopen reuses detail");
detailNode.dispatchEvent({type:"error"});
assert.equal(detailNode.hidden,true); assert.equal(detailNode.parentNode.querySelector(".grabber-preview-error").hidden,false);
const failedCount=stats.imageSources.length; await view.poll();
assert.equal(stats.imageSources.length,failedCount,"preview failure never retries or falls back to original during polling");
assert.ok(pane().querySelector("a").getAttribute("href").startsWith("/api/grabber/file?"));
await click("detail-close");
const thumbFailure=images()[0]; thumbFailure.dispatchEvent({type:"error"});
assert.equal(thumbFailure.hidden,true); assert.equal(thumbFailure.parentNode.querySelector(".grabber-preview-error").hidden,false);
await view.action({target:details()[0]}); assert.equal(images()[0],detailNode); assert.equal(images()[0].hidden,true);
// Explicit refresh revalidates the exact same URL (backend ETag), never a nonce.
const previousUrl=detailNode.src; await click("refresh");
assert.notEqual(images()[0],detailNode); assert.equal(images()[0].src,previousUrl); assert.equal(images()[0].hidden,false);
progress={status:"running",items:[resultItem(999)],logs:[]}; await view.poll();
assert.equal(details()[0].dataset.index,"0"); assert.equal(button("results-prev").disabled,true,"new task resets page");
// Library: only the requested page, preserve images across model and retag changes.
progress={...progress,status:"done"}; retagJob={status:"idle"}; await view.poll();
libraryBulk=true; savedTags=false; await click("tab","library"); await fill("group","alpha","library");
assert.equal(images().length,60); assert.ok(images().every(image=>image.src.includes("size=thumb") && image.loading==="lazy" && image.decoding==="async"));
const libraryThumbs=images(); const readsBefore=requests.filter(r=>r.path.endsWith("/library/items")).length;
await view.poll(); await view.poll(); assert.equal(images()[0],libraryThumbs[0]);
assert.equal(requests.filter(r=>r.path.endsWith("/library/items")).length,readsBefore,"unchanged polls fetch no extra library pages");
await click("library-detail"); const libraryDetail=images()[0]; assert.equal(images().length,1); assert.ok(libraryDetail.src.includes("size=detail"));
await fill("mode","replace","library"); assert.equal(images()[0],libraryDetail,"retag form changes retain image nodes");
const librarySrcCount=stats.imageSources.length;
retagJob={id:"media-job",group:"alpha",status:"running",logs:[],processed:1}; await view.poll();
retagJob={...retagJob,processed:3,logs:["tagging progress"]}; await view.poll(); assert.equal(images()[0],libraryDetail);
savedTags=true; retagJob={...retagJob,status:"done"}; await view.poll();
assert.equal(view.library.state.selected,0); assert.match(pane().innerHTML,/fresh_tag/); assert.equal(images()[0],libraryDetail,"retag completion updates tags without decoding the same image again");
assert.equal(stats.imageSources.length,librarySrcCount);
await click("library-back"); assert.equal(images()[0],libraryThumbs[0]);
await view.action({target:pane().querySelectorAll('[data-act="library-detail"]')[1]});
const otherDetail=images()[0]; assert.notEqual(otherDetail,libraryDetail);
await click("library-back"); await click("library-detail");
assert.notEqual(images()[0],libraryDetail,"only one large detail decode is retained"); assert.equal(images()[0].src,libraryDetail.src);
await click("library-back");
await click("library-next"); assert.equal(images().length,5); assert.ok(images()[0].src.includes("sample60.png"));
assert.equal(requests.filter(r=>r.path.endsWith("/library/items")).at(-1).args.limit,60);
await fill("group","beta","library"); assert.equal(images().length,1); assert.ok(images()[0].src.includes("beta"));
await click("library-detail"); const betaImage=images()[0]; betaImage.dispatchEvent({type:"error"});
assert.equal(betaImage.hidden,true); assert.equal(betaImage.parentNode.querySelector(".grabber-preview-error").hidden,false);
assert.ok(pane().querySelector("a").getAttribute("href").includes("/api/grabber/file?"));
libraryDeleted=true; await click("library-refresh"); assert.equal(view.library.state.selected,null); assert.equal(images().length,0,"deleted images disappear after explicit metadata refresh");
assert.ok(stats.imageSources.every(url=>!url.startsWith("/api/grabber/file?")));
libraryDeleted=false;
// 64 images: actual UI actions, counters, requests and media identities across page edges.
const fixture64=Array.from({length:64},(_,i)=>({filename:`nav-${i+1}.png`,image_url:`/files/alpha/nav-${i+1}.png`,tags:["before"],has_tags:true}));
navigationItems=[...fixture64]; retagJob={status:"idle"};
await fill("group","alpha","library");
const ls=view.library.state;
const itemReads=()=>requests.filter(r=>r.path.endsWith("/library/items"));
const counter=()=>button("library-prev").parentNode.querySelector("span").textContent;
const openImage=async index=>view.action({target:pane().querySelectorAll('[data-act="library-detail"]')[index]});
const flush=async()=>{for(let i=0;i<8;i++) await Promise.resolve();};
const atImage=n=>{
  assert.equal(ls.offset+ls.selected+1,n); assert.notEqual(ls.selected,null);
  assert.equal(counter(),`${n} / ${ls.total}`); assert.equal(images().length,1);
  assert.equal(images()[0].width,"1600"); assert.ok(images()[0].src.includes(`nav-${n}.png`));
  assert.equal(button("library-prev").textContent,"grabber.library.prevImage");
  assert.equal(button("library-next").textContent,"grabber.library.nextImage");
};
assert.equal(counter(),"1\u201360 / 64"); assert.equal(images().length,60);
const navThumbs=images(); await openImage(0); atImage(1);
main.scrollTop=700;
const assertScroll = (expected=700) => assert.equal(main.scrollTop,expected,"detail commit preserves the actual main scroll container");
button("library-next").focus();
await click("library-next"); atImage(2); assertScroll();
assert.equal(document.activeElement,button("library-next")); assert.equal(stats.lastFocusOptions.preventScroll,true);
await click("library-prev"); atImage(1); assertScroll();
assert.equal(button("library-prev").disabled,true);
let reads=itemReads().length;
await view.library.action("library-prev",{}); assert.equal(itemReads().length,reads); atImage(1);
for(let n=2;n<=60;n++) { await click("library-next"); atImage(n); assertScroll(); }
assert.equal(itemReads().length,reads,"1 through 60 navigates locally");
await click("library-next"); atImage(61); assert.equal(ls.offset,60); assert.equal(ls.selected,0);
assert.equal(itemReads().length,reads+1); assert.equal(itemReads().at(-1).args.offset,60);
for(let n=62;n<=64;n++) { await click("library-next"); atImage(n); }
assert.equal(button("library-next").disabled,true);
await view.library.action("library-next",{}); assert.equal(itemReads().length,reads+1); atImage(64);
for(let n=63;n>=1;n--) { await click("library-prev"); atImage(n); assertScroll(); }
assert.equal(itemReads().length,reads+2,"61 to 60 fetches only the preceding page");
assert.equal(itemReads().at(-1).args.offset,0);
await click("library-back"); assert.equal(images()[0],navThumbs[0],"detail navigation retains cached page thumbnails");
console.log("64-image boundaries: 1/60/61/64 forward and backward, local moves and terminal guards passed");

// Transactional forward/backward failures, retained detail and one in-flight request.
await openImage(59); atImage(60); const heldItems=ls.items, heldDetail=images()[0]; reads=itemReads().length;
navigationDelay=60; const failedNext=click("library-next"); await flush();
assert.equal(ls.loading,true); assert.equal(ls.items,heldItems); atImage(60); assert.equal(images()[0],heldDetail); assertScroll();
assert.ok(pane().innerHTML.indexOf('grabber.library.loading') > pane().innerHTML.indexOf('grabber-detail'),"loading does not push detail down");
assert.equal(button("library-next").disabled,true); assert.equal(button("library-prev").disabled,true);
await click("library-next"); await view.library.action("library-next",{}); await view.library.action("library-prev",{});
assert.equal(itemReads().length,reads+1,"rapid repeated handlers send exactly one request");
navigationPending.reject(Error("adjacent page unavailable")); await failedNext;
atImage(60); assert.equal(ls.items,heldItems); assert.equal(images()[0],heldDetail); assert.equal(ls.loading,false); assertScroll();
assert.ok(pane().innerHTML.indexOf('adjacent page unavailable') > pane().innerHTML.indexOf('grabber-detail'),"failure does not push detail down");
assert.match(pane().textContent,/adjacent page unavailable.*library.retry/); assert.equal(button("library-next").disabled,false);
await click("library-next"); atImage(61); assert.equal(ls.error,"");
navigationDelay=0; const failedPrev=click("library-prev"); await flush(); atImage(61);
navigationPending.reject(Error("previous page unavailable")); await failedPrev; atImage(61);
await click("library-prev"); atImage(60);
console.log("64-image failure/retry: retained page/detail/counter and duplicate-request guard passed");
// A pending request does not own the scroll position: capture again at commit.
navigationDelay=60; const userScrolled=click("library-next"); await flush(); assertScroll();
main.scrollTop=430; navigationPending.resolve(navigationPage(60)); await userScrolled; atImage(61); assertScroll(430);
images()[0].dispatchEvent({type:"load"}); assertScroll(430);
await click("library-prev"); atImage(60); assertScroll(430); main.scrollTop=700;

// Back cancels a pending cross-page transaction; it must not reopen details later.
navigationDelay=60; const closedNavigation=click("library-next"); await flush();
await click("library-back"); assert.equal(ls.selected,null); assert.equal(ls.offset,0); assert.equal(images().length,60);
main.scrollTop=222; navigationPending.resolve(navigationPage(60)); await closedNavigation; assertScroll(222); main.scrollTop=700;
assert.equal(ls.selected,null); assert.equal(counter(),"1\u201360 / 64");
await click("library-next"); assert.equal(ls.offset,60); assert.equal(ls.selected,null); assert.equal(counter(),"61\u201364 / 64");
assert.equal(button("library-prev").textContent,"grabber.library.prev"); assert.equal(button("library-next").disabled,true);
await openImage(3); atImage(64); await click("library-back"); assert.equal(ls.offset,60); assert.equal(images().length,4);
reads=itemReads().length; await view.library.action("library-next",{}); assert.equal(itemReads().length,reads);
await click("library-prev"); assert.equal(ls.selected,null); assert.equal(counter(),"1\u201360 / 64");
console.log("64-image grid: back preserves current page, pending back stays closed, grid paging unchanged");

// Switching groups and leaving the tab invalidate pending page responses, including ABA re-entry.
await openImage(59); navigationDelay=60; const changedGroup=click("library-next"); await flush();
await fill("group","beta","library"); main.scrollTop=111; navigationPending.resolve(navigationPage(60)); await changedGroup; assertScroll(111); main.scrollTop=700;
assert.equal(ls.group,"beta"); assert.equal(ls.offset,0); assert.equal(ls.selected,null); assert.equal(ls.items[0].filename,"beta0.png");
await fill("group","alpha","library"); await openImage(59);
navigationDelay=60; const leftTab=click("library-next"); await flush();
await click("tab","results"); const resultsHtml=pane().innerHTML, resultsWrites=pane().writes;
main.scrollTop=321; navigationPending.resolve(navigationPage(60)); await leftTab; assertScroll(321); main.scrollTop=700;
assert.equal(view.state().tab,"results"); assert.equal(pane().innerHTML,resultsHtml); assert.equal(pane().writes,resultsWrites);
assert.equal(ls.offset,0); assert.equal(ls.selected,59); assert.equal(ls.loading,false);
await click("tab","library"); atImage(60);
navigationDelay=60; const reentered=click("library-next"); await flush();
await click("tab","results"); await click("tab","library");
navigationPending.resolve(navigationPage(60)); await reentered; atImage(60);
// Existing groups epoch guard still prevents refresh from replacing a newly chosen group.
navigationGroupsDelay=true; const staleGroups=view.library.refresh(); await flush();
await fill("group","beta","library"); reads=itemReads().length;
navigationGroupsResolve(); await staleGroups; assert.equal(ls.group,"beta"); assert.equal(itemReads().length,reads);
navigationGroupsDelay=true; const leftGroups=view.library.refresh(); await flush();
const groupSnapshot=ls.groups; await click("tab","results"); navigationGroupsResolve(); await leftGroups;
assert.equal(ls.groups,groupSnapshot); assert.equal(ls.loading,false);
// Retag progress keeps updating outside the tab, but cannot start hidden item refreshes.
retagJob={id:"hidden-navigation-job",group:"beta",status:"done"}; reads=itemReads().length;
await view.poll(); assert.equal(itemReads().length,reads); assert.equal(ls.job.id,"hidden-navigation-job");
await click("tab","library"); await fill("group","alpha","library");
console.log("64-image races: group switch, leave, re-entry, groups epoch and hidden retag refresh passed");

// Preserve selection by URL, not index; never search other pages for a vanished selection.
await click("library-next"); await openImage(1); atImage(62); const selectedUrl=ls.items[ls.selected].image_url;
navigationItems=[...fixture64]; [navigationItems[60],navigationItems[61]]=[navigationItems[61],navigationItems[60]];
const selectedNode=images()[0]; await view.library.refresh();
assert.equal(ls.selected,0); assert.equal(ls.items[ls.selected].image_url,selectedUrl); assert.equal(images()[0],selectedNode);
navigationItems=navigationItems.map(item=>({...item,tags:["retagged"]}));
retagJob={id:"navigation-retag",group:"alpha",status:"done"}; await view.poll();
assert.equal(ls.items[ls.selected].image_url,selectedUrl); assert.equal(images()[0],selectedNode); assert.match(pane().textContent,/retagged/);
navigationItems=navigationItems.filter(item=>item.image_url!==selectedUrl); reads=itemReads().length;
await view.library.refresh(); assert.equal(ls.selected,null); assert.equal(ls.offset,60); assert.equal(ls.total,63);
assert.equal(itemReads().length,reads+1,"a disappeared selection does not trigger a whole-group search");
assert.equal(images().length,3); assert.equal(pane().querySelector(".grabber-detail"),null);
// Last page shrinks: fetch only the final valid page and commit once it succeeds.
await openImage(2); const beforeShrinkItems=ls.items, beforeShrinkDetail=images()[0];
navigationItems=fixture64.slice(0,59); navigationDelay=0;
reads=itemReads().length; const failedShrink=view.library.refresh(); await flush();
assert.deepEqual(itemReads().slice(reads).map(r=>r.args.offset),[60,0]);
assert.equal(ls.offset,60); assert.equal(ls.items,beforeShrinkItems); assert.equal(images()[0],beforeShrinkDetail);
navigationPending.reject(Error("replacement page unavailable")); await failedShrink;
assert.equal(ls.offset,60); assert.equal(ls.items,beforeShrinkItems); assert.notEqual(ls.selected,null);
await click("library-refresh"); assert.equal(ls.offset,0); assert.equal(ls.selected,null); assert.equal(ls.total,59); assert.equal(images().length,59);
assert.equal(counter(),"1\u201359 / 59"); assert.equal(button("library-next").disabled,true);
// A preserved URL can move within the clamped last page too.
navigationItems=[...fixture64]; await view.library.refresh(); await click("library-next"); await openImage(3);
navigationItems=fixture64.slice(1); await view.library.refresh();
assert.equal(ls.offset,60); assert.equal(ls.selected,2); assert.equal(ls.items[ls.selected].image_url,fixture64[63].image_url);
assert.equal(counter(),"63 / 63");
navigationItems=[]; await view.library.refresh(); assert.equal(ls.offset,0); assert.equal(ls.total,0); assert.equal(ls.selected,null); assert.equal(images().length,0);
assert.equal(button("library-prev").disabled,true); assert.equal(button("library-next").disabled,true);
console.log("64-image refresh: URL identity, retag content, deletion, shrink retry/clamp and empty group passed");

// A late retag refresh or shrink replacement cannot resurrect a closed detail or old group.
navigationItems=[...fixture64]; await view.library.refresh(); await openImage(0);
navigationDelay=0; retagJob={id:"navigation-back-retag",group:"alpha",status:"done"}; const retagRefresh=view.poll(); await flush();
assert.equal(ls.loading,true); await click("library-back"); navigationPending.resolve(navigationPage(0)); await retagRefresh;
assert.equal(ls.selected,null); assert.equal(images().length,60);
await click("library-next"); await openImage(3); navigationItems=fixture64.slice(0,59); navigationDelay=0;
const shrinkRace=view.library.refresh(); await flush();
await fill("group","beta","library"); navigationPending.resolve(navigationPage(0)); await shrinkRace;
assert.equal(ls.group,"beta"); assert.equal(ls.items[0].filename,"beta0.png"); assert.equal(ls.selected,null); assert.equal(ls.loading,false);
// Returned grid thumbnails and current detail reuse are still bounded; URLs never use nonces or originals.
navigationItems=[...fixture64]; await fill("group","alpha","library"); const cachedGrid=images();
await openImage(0); const cachedDetail=images()[0]; await click("library-back"); assert.equal(images()[0],cachedGrid[0]);
await openImage(0); assert.equal(images()[0],cachedDetail); await click("library-next"); await click("library-prev");
assert.notEqual(images()[0],cachedDetail,"at most one detail node is retained"); assert.equal(images()[0].src,cachedDetail.src);
assert.ok(pane().querySelectorAll("a").some(a=>a.getAttribute("href").startsWith("/api/grabber/file?")));
assert.ok(stats.imageSources.every(url=>!url.startsWith("/api/grabber/file?") && !/[?&](nonce|t|ts|_)=/.test(url)));
assert.ok(itemReads().every(r=>r.args.limit===60));
await click("library-back"); const boundedThumbs=images(); await click("library-next");
assert.equal(images().length,4); await click("library-prev"); assert.equal(images().length,60);
assert.notEqual(images()[0],boundedThumbs[0],"four second-page thumbnails evict the oldest first-page nodes");
assert.equal(images()[4],boundedThumbs[4],"remaining 56 thumbnails stay cached, total bound is 60");
console.log("64-image lifecycle/cache: retag-back and shrink races, thumb60/detail1, explicit originals and stable URLs passed");
// An outstanding response after disposal cannot repaint or schedule another poll.
delayed="progress"; const pending=view.poll(); await Promise.resolve(); const writes=stats.rootWrites;
delayedGroup="alpha"; const pendingLibrary=view.library.load("alpha"); await Promise.resolve(); const oldPaneWrites=root.hosts.get("grabber-pane").writes;
cleanup(); main.scrollTop=123; pendingResolve({status:"done",items:[],logs:[]}); groupResolve(libraryPage("alpha")); await Promise.all([pending,pendingLibrary]); assertScroll(123); assert.equal(root.hosts.get("grabber-pane").writes,oldPaneWrites); assert.equal(view.library.state.items.length,0);
assert.equal(stats.rootWrites,writes); assert.equal(stats.removed,9); assert.equal(queuedTimer,null); assert.equal(main.__studioCleanup,null);
assert.ok(scrollClamps>120,"navigation exercised simulated browser anchoring");
assert.equal(stats.liveMediaRemovals || 0,0,"media is never detached while its old ancestors are live");
assert.equal(view.state().pendingClears.size,0);
console.log("workspace behaviors passed");
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'grabber.mjs'
            path.write_text(module)
            result = subprocess.run(['node', '--input-type=module'], input=runner.replace('MODULE_URL', json.dumps(path.as_uri())).replace('DOM_URL', json.dumps((Path(__file__).parent / 'grabber_dom.mjs').as_uri())), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('workspace behaviors passed', result.stdout)
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_terminal_poll_clears_only_stop_notice(self):
        poll = 'async function poll() {' + self.script.split('  async function poll() {', 1)[1].split('  async function connect()', 1)[0]
        runner = r'''
import assert from "node:assert/strict";
const TERMINAL = new Set(["done", "stopped", "error"]);
const service = {running:true};
const library = {poll:async()=>{}};
let polling = false, timer = null, progressEpoch = 0, finalSynced = true;
let progressKnown = true, logOffset = 0, itemOffset = 0, connectionLost = false;
const ACTIVE = new Set(["preparing", "running"]);
let taskConfig = null;
const resetProgress = () => {};
let stopRequested = true, message = "waiting for stop", messageOrigin = "stop";
let next = {status:"running"}, snapshot = next;
const api = {get:async () => next};
const alive = () => true, render = () => {}, clean = v => v, gt = v => v;
const running = () => snapshot.status === "running";
const applySnapshot = value => { snapshot = value; };
const notice = (text, error, origin) => { message = text; messageOrigin = origin; };
const clearTimeout = () => {}, setTimeout = () => 1;
'''
        runner += poll + r'''
await poll();
assert.equal(stopRequested, true);
assert.equal(message, "waiting for stop", "active task keeps the stop notice");
for (const status of TERMINAL) {
  next = {status}; stopRequested = true; message = "waiting for stop"; messageOrigin = "stop";
  await poll();
  assert.equal(stopRequested, false, status);
  assert.equal(message, "", status);
  stopRequested = true; message = "unrelated business failure"; messageOrigin = "action";
  await poll();
  assert.equal(stopRequested, false, status);
  assert.equal(message, "unrelated business failure", status);
}
console.log("terminal poll clears stop state without erasing business failures");
'''
        result = subprocess.run(['node', '--input-type=module'], input=runner, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('notice(gt("stop.hint"), false, "stop")', self.script)
        self.assertIn('alive() && !TERMINAL.has(snapshot.status)', self.script)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_js_syntax_and_url_redaction(self):
        for name in ('grabber.js', 'grabber-library.js', 'grabber-media.js', 'grabber-annotations.js', 'grabber-translation.js', 'annotation-api.js', 'app.js', 'studio.js'):
            result = subprocess.run(['node', '--input-type=module', '--check'], input=(WEB / name).read_text(), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        # Helpers are pure; load without a DOM or browser token store.
        text = self.script.replace('import * as api from "./api.js";', 'const api = {TOKEN: "ui-only-token"};').replace('import { esc } from "./format.js";', '')
        text = text.replace('import { createLibrary } from "./grabber-library.js";', (WEB / 'grabber-library.js').read_text())
        text = text.replace('import { createGrabberMedia } from "./grabber-media.js";', (WEB / 'grabber-media.js').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp) / 'grabber.mjs'
            module.write_text(inline_annotations(text))
            runner = f'''import assert from "node:assert/strict";
import {{previewUrl,originalUrl,safeLink,redact}} from {json.dumps(module.as_uri())};
assert.equal(previewUrl("https://external.test/files/a.png"), "");
assert.equal(previewUrl("//external.test/files/a.png"), "");
assert.equal(previewUrl("/files/a.png?token=x"), "");
assert.ok(previewUrl("/files/a.png").startsWith("/api/grabber/preview?"));
assert.ok(previewUrl("/files/a.png").includes("token=ui-only-token"));
assert.ok(previewUrl("/files/a.png").includes("size=thumb"));
assert.ok(previewUrl("/files/a.png", "detail").includes("size=detail"));
assert.equal(previewUrl("/files/a.png", "original"), "");
assert.ok(originalUrl("/files/a.png").startsWith("/api/grabber/file?"));
assert.equal(originalUrl("https://external.test/a.png"), "");
assert.equal(safeLink("javascript:alert(1)"), "");
assert.equal(safeLink("https://user:pass@example.com/"), "");
assert.equal(safeLink("https://example.com/?token=secret&id=1"), "https://example.com/?id=1");
const clean=redact({{api_key:"key",max_tokens:512,log:"Bearer xyz auth_token=hello custom-password"}},["custom-password"]);
assert.equal(clean.api_key,"[redacted]"); assert.equal(clean.max_tokens,512);
assert.ok(!clean.log.includes("xyz")); assert.ok(!clean.log.includes("hello")); assert.ok(!clean.log.includes("custom-password"));
console.log("URL and credential boundaries passed");'''
            result = subprocess.run(['node', '--input-type=module'], input=runner, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
