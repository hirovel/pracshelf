// Presentation context only. URL identity wins; no workflow, note or model state.
const modes=new Set(['source','translation','parallel']);
const operations=new Set(['fetch','load','clean','core','translate']);
function clean(value={}) {
  if(!value||typeof value!=='object')return {};
  const runId=typeof value.runId==='string'&&/^[a-f0-9]{32}$/.test(value.runId)?value.runId:null;
  if(!runId)return {};
  const resultId=/^[1-9][0-9]*$/.test(String(value.reader?.resultId||''))?String(value.reader.resultId):null;
  return {runId,title:typeof value.title==='string'?value.title.slice(0,500):'',readable:value.readable===true,
    operation:operations.has(value.operation)?value.operation:null,
    reader:{mode:modes.has(value.reader?.mode)?value.reader.mode:null,resultId}};
}
export function mergeContext(previous,next) {
  const old=clean(previous),newValue=clean(next);
  if(!newValue.runId)return {};
  if(old.runId!==newValue.runId)return newValue;
  return clean({...old,...next,reader:{...old.reader,...next.reader}});
}
export function resolveContext(pathname,search,saved) {
  const params=new URLSearchParams(search), prior=clean(saved);
  const explicit=pathname.startsWith('/read/')?pathname.split('/').filter(Boolean).pop():params.get('run');
  let current=explicit?mergeContext(prior,{runId:explicit}):prior;
  if(pathname.startsWith('/read/'))current=mergeContext(current,{runId:explicit,reader:{mode:params.get('mode'),resultId:params.get('result_id')}});
  if(pathname==='/pipeline'&&params.get('operation'))current=mergeContext(current,{runId:current.runId,operation:params.get('operation')});
  return current;
}
export function navigation(context) {
  const value=clean(context),run=value.runId;
  const work=new URLSearchParams();if(run)work.set('run',run);if(value.operation)work.set('operation',value.operation);
  const read=new URLSearchParams();if(value.reader?.mode)read.set('mode',value.reader.mode);if(value.reader?.resultId)read.set('result_id',value.reader.resultId);
  return {library:'/',collect:'/inbox',pipeline:'/pipeline'+(work.size?'?'+work:''),
    reading:run&&value.readable?'/read/'+run+(read.size?'?'+read:''):null,
    publication:'/publication'+(run?'?run='+run:''),builds:'/reading-builds',setup:'/setup'};
}
