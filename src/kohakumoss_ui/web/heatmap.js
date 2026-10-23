/* Profile calendar and dataset activity tree. Pure renderers, no chart library.
   Metric schemas and saved dataset configuration are interpreted by contract/. */
import { duration, esc, when } from "./format.js";
const DAY = 86400000;
const count = value => Math.round(Number(value) || 0).toLocaleString();
const keyOf = date => date.toISOString().slice(0,10);
const parse = key => new Date(String(key) + "T00:00:00Z");
const clock = seconds => seconds > 0 ? duration(seconds) : "0m";
function translate(handler, key, vars = {}) {
  const out = typeof handler === "function" ? handler(key, vars) : key;
  return esc(String(out).replace(/\{(\w+)\}/g, (match,name) => vars[name] ?? match));
}

/* Read-only publication state, independent of the run's training status.
   No query result (including failures) must never become "unpublished". */
export function renderPublishStatus(result, runName, options = {}) {
  const t = (key, vars) => translate(options.t, key, vars);
  const matches = Object.values(result?.publish || {}).filter(item => item
    && Array.isArray(item.runs) && item.runs.includes(runName));
  const known = result && !result.__error && !result.error && result.civitai?.lastSync;
  const published = known && matches.find(item => item.state === "published" && !item.ambiguous);
  const unpublished = known && matches.length && matches.every(item => item.state === "draft" && !item.ambiguous);
  const state = published ? "published" : unpublished ? "unpublished" : options.pending && !result ? "pending" : "unknown";
  const key = "studio.publish.state." + state;
  const label = typeof options.t === "function" ? options.t(key) : key;
  const title = t(published ? "studio.publish.api.match.hint" : key + ".hint");
  const attrs = ' class="st publish-status" data-publish="' + state + '" title="' + title
    + '" aria-label="' + t("studio.publish.state.aria", {state:label}) + '"';
  if (published?.url?.startsWith("https://civitai.com/")) {
    return '<a' + attrs + ' href="' + esc(published.url) + '" target="_blank" rel="noreferrer">' + esc(label) + '</a>';
  }
  return '<span' + attrs + '>' + esc(label) + '</span>';
}

export function renderHeatmap(model = {}) {
  const t = (key,vars) => translate(model.t,key,vars);
  const locale = model.lang === "zh" ? "zh-CN" : "en-US";
  const days = (Array.isArray(model.days) ? model.days : []).filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d.date));
  const todayKey = model.today || keyOf(new Date()), today = parse(todayKey);
  const year = Number(model.year) || null;
  const start = year ? new Date(Date.UTC(year,0,1)) : days.length ? parse(days[0].date) : new Date(today.getTime()-364*DAY);
  const end = year ? new Date(Date.UTC(year,11,31)) : today;
  const first = start.getTime()-start.getUTCDay()*DAY;
  const columns = Math.floor((end.getTime()-first)/(7*DAY))+1;
  const pool = days.filter(d => d.date>=keyOf(start) && d.date<=keyOf(end) && d.date<=todayKey);
  const byDate = new Map(pool.map(d => [d.date,d]));
  const peak = Math.max(0,...pool.map(d => Number(d.steps)||0));
  const total = pool.reduce((s,d) => ({days:s.days+(d.steps>0||d.seconds>0?1:0),steps:s.steps+(Number(d.steps)||0),seconds:s.seconds+(Number(d.seconds)||0)}),{days:0,steps:0,seconds:0});
  const years = [...new Set([today.getUTCFullYear(),...(year ? [year] : []),...(model.years||[]).map(Number)])]
    .filter(y => Number.isInteger(y)&&y>=1970&&y<=today.getUTCFullYear()).sort((a,b)=>b-a);
  const yearLink = (value,label,on) => '<button class="btn'+(on?' on':'')+'" type="button" data-act="year" data-id="'+value+'" aria-pressed="'+on+'">'+label+'</button>';
  const nav = '<nav class="hm-years" aria-label="'+t("studio.heatmap.year")+'">'
    +yearLink('',t("studio.heatmap.recent"),year===null)+years.map(y=>yearLink(y,y,y===year)).join('')+'</nav>';
  const monthFormat = new Intl.DateTimeFormat(locale,{month:'short',timeZone:'UTC'});
  const dayFormat = new Intl.DateTimeFormat(locale,{weekday:'short',timeZone:'UTC'});
  const monthStarts = [];
  let previous = -1;
  for(let col=0;col<columns;col++){
    const date = new Date(Math.max(first+col*7*DAY,start.getTime())), month=date.getUTCMonth();
    if(month!==previous) monthStarts.push({col,date});
    previous=month;
  }
  const months = monthStarts.filter((slot,index) => (monthStarts[index+1]?.col ?? columns)-slot.col >= 3)
    .map(slot => '<span class="hm-month" style="grid-column:'+(slot.col+1)+'">'+esc(monthFormat.format(slot.date))+'</span>');
  const rail = Array.from({length:7},(_,r)=>'<span class="hm-day">'+([1,3,5].includes(r)?esc(dayFormat.format(new Date(Date.UTC(2023,0,1+r)))):'')+'</span>').join('');
  const cells=[];
  for(let col=0;col<columns;col++)for(let row=0;row<7;row++){
    const stamp=first+(col*7+row)*DAY,key=keyOf(new Date(stamp)),d=byDate.get(key);
    const outside=stamp<start.getTime()||stamp>end.getTime(),future=key>todayKey,steps=Number(d?.steps)||0;
    const level=outside||future||!steps?0:Math.min(4,Math.ceil(steps/peak*4));
    const title=future?t("studio.heatmap.future",{date:key}):d?t("studio.heatmap.cell",{date:key,steps:count(steps),runs:count(d.runs),time:clock(d.seconds)}):t("studio.heatmap.cell.empty",{date:key});
    cells.push('<span class="hm-cell" data-date="'+key+'" data-level="'+level+'"'+(outside?' data-outside="1"':future?' data-empty="1"':'')+' title="'+title+'"></span>');
  }
  const legend=[0,1,2,3,4].map(v=>'<span class="hm-cell" data-level="'+v+'"></span>').join('');
  return '<div class="hm" style="--hm-columns:'+columns+'"><div class="hm-content"><div class="hm-head"><span>'
    +t("studio.heatmap.summary",{days:count(total.days),period:year||t("studio.heatmap.recent")})+'</span><span class="hm-total">'
    +t("studio.heatmap.work",{steps:count(total.steps),time:clock(total.seconds)})+'</span></div>'
    +'<div class="hm-scroll" tabindex="0" role="region" aria-label="'+t("studio.heatmap.label",{steps:count(total.steps),days:count(total.days)})+'"><div class="hm-calendar"><div class="hm-months" aria-hidden="true">'
    +months.join('')+'</div><div class="hm-body"><div class="hm-days" aria-hidden="true">'+rail+'</div><div class="hm-cells" role="img" aria-label="'+t("studio.heatmap.label",{steps:count(total.steps),days:count(total.days)})+'">'+cells.join('')+'</div></div></div></div>'
    +'<div class="hm-foot"><span class="hint">'+t("studio.heatmap.hint")+'</span><span class="hm-legend">'+t("studio.heatmap.less")+legend+t("studio.heatmap.more")+'</span></div></div>'+nav+'</div>';
}
export function groupHistory(rows,year=null){
  const groups=new Map();
  const list=(Array.isArray(rows)?rows:[]).filter(r=>r&&r.name)
    .filter(r=>!year||new Date(Number(r.started_at)*1000).getFullYear()===Number(year)).sort((a,b)=>Number(b.started_at)-Number(a.started_at));
  for(const row of list){
    const datasets=Array.isArray(row.datasets)&&row.datasets.length?row.datasets:[{path:'',source:'',name:''}];
    for(const dataset of datasets){
      const key=JSON.stringify([dataset.source,dataset.path]);
      if(!groups.has(key))groups.set(key,{dataset,runs:[]});
      const group=groups.get(key);
      if(!group.runs.some(r=>r.name===row.name))group.runs.push(row);
    }
  }
  return [...groups.values()];
}
export function renderHistory(rows,options={}){
  const t=(key,vars)=>translate(options.t,key,vars),groups=groupHistory(rows,options.year);
  if(!groups.length)return '<div class="empty"><b>'+t("studio.history.empty.title")+'</b><div>'+t("studio.history.empty.body")+'</div></div>';
  return '<div class="activity-tree">'+groups.map(({dataset,runs})=>'<section class="dataset-activity"><div class="dataset-heading"><h4>'
    +esc(dataset.name||t("studio.history.dataset.unknown"))+'</h4><span class="hint">'+t("studio.history.dataset.count",{count:count(runs.length)})+'</span></div>'
    +(dataset.path?'<div class="dataset-path">'+(dataset.source?esc(dataset.source)+' · ':'')+esc(dataset.path)+'</div>':'')+'<ol class="activity-branches">'
    +runs.map(row=>{
      const files=Array.isArray(row.exports)?row.exports:[],file=files.find(f=>/\.(safetensors|ckpt|pt|pth|bin)$/i.test(f))||'';
      return '<li class="activity-run"><div class="activity-run-head"><h5><a href="#/run/'+encodeURIComponent(row.name)+'">'+esc(row.name)+'</a></h5><time class="hint">'+esc(when(row.started_at))+'</time></div>'
        +'<div class="activity-meta"><span class="st" data-status="'+esc(row.status)+'">'+esc(String(row.status).toUpperCase())+'</span><span>'
        +t("studio.history.run.work",{steps:count(row.steps),time:clock(row.seconds)})+'</span>'+(row.datasets?.length>1?'<span class="hint">'+t("studio.history.multi")+'</span>':'')
        +(options.rowMeta?options.rowMeta(row):'')+'</div>'
        +'<div class="activity-export hint">'+(file?esc(file):t("studio.profile.pins.noexport"))+'</div>'
        +'</li>';
    }).join('')+'</ol></section>').join('')+'</div>';
}
