"""Exercise the profile's pure renderers with Node, without browser dependencies."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src/kohakumoss_ui/web"


@unittest.skipUnless(shutil.which("node"), "Node unavailable for ES module render checks")
class ProfileRenderTest(unittest.TestCase):
    def test_calendar_windows_and_dataset_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "package.json").write_text('{"type":"module"}')
            for name in ("heatmap.js", "format.js"):
                shutil.copy2(WEB / name, target / name)
            script = r'''
import assert from 'node:assert/strict';
import {renderHeatmap, groupHistory, renderHistory, renderPublishStatus} from './heatmap.js';
const t = (key, vars={}) => key + ' ' + Object.values(vars).join(' ');
const leap = renderHeatmap({year:2028,today:'2028-12-31',days:[],t});
assert.ok(leap.includes('--hm-columns:54'));
assert.equal((leap.match(/data-date="2028-02-29"/g)||[]).length,1);
assert.ok(!leap.includes('class="empty"'));
const calendar = renderHeatmap({year:2025,today:'2026-10-23',years:[2024,2025,2026],
  days:[{date:'2024-12-31',steps:999,seconds:1},{date:'2025-01-02',steps:20,seconds:30}],t});
assert.ok(calendar.includes('studio.heatmap.work 20 '));
assert.ok(calendar.includes('data-id="2024"'));
const future = renderHeatmap({year:2026,today:'2026-01-03',days:[{date:'2026-04-01',steps:500}],t});
assert.ok(future.includes('studio.heatmap.work 0 0m'));
const a={path:'/one/data',source:'folder',name:'data'},b={path:'/two/data',source:'folder',name:'data'};
const rows=[{name:'old',started_at:1,datasets:[a],status:'done'},
 {name:'<mixed>',started_at:2,datasets:[a,b],status:'stopped'},
 {name:'unknown',started_at:3,datasets:[],status:'unknown'}];
const groups=groupHistory(rows);
assert.equal(groups.length,3);
assert.equal(groups.find(g=>g.dataset.path===a.path).runs[0].name,'<mixed>');
assert.equal(groups.find(g=>g.dataset.path===b.path).runs.length,1);
const html=renderHistory(rows,{t});
assert.ok(html.includes('&lt;mixed&gt;'));
assert.ok(html.includes('studio.history.dataset.unknown'));
assert.ok(html.includes('class="activity-branches"'));
assert.ok(!html.includes('<table'));
assert.equal(groupHistory(rows,2025).length,0);
const publication={civitai:{lastSync:1},publish:{
  a:{state:'published',runs:['a'],url:'https://civitai.com/models/42'},
  b:{state:'draft',runs:['b']},c:{state:'unknown',ambiguous:true,runs:['c']}}};
assert.ok(renderPublishStatus(publication,'a',{t}).includes('data-publish="published"'));
assert.ok(renderPublishStatus(publication,'a',{t}).includes('href="https://civitai.com/models/42"'));
assert.ok(renderPublishStatus(publication,'b',{t}).includes('data-publish="unpublished"'));
for(const name of ['c','missing'])assert.ok(renderPublishStatus(publication,name,{t}).includes('data-publish="unknown"'));
assert.ok(renderPublishStatus(null,'a',{t,pending:true}).includes('data-publish="pending"'));
assert.ok(renderPublishStatus({...publication,error:'offline'},'a',{t}).includes('data-publish="unknown"'));
assert.ok(renderPublishStatus({...publication,civitai:{lastSync:0}},'b',{t}).includes('data-publish="unknown"'));
assert.ok(!renderPublishStatus(publication,'b',{t}).includes('<button'));
'''
            (target / "check.mjs").write_text(script)
            result = subprocess.run(["node", str(target / "check.mjs")], text=True,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
