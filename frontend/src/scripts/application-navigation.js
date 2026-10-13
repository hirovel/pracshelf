import {mergeContext,navigation,resolveContext} from './navigation-context.js';

let saved={};try{saved=JSON.parse(sessionStorage.getItem('workspace-document')||'{}');}catch{}
let context=resolveContext(location.pathname,location.search,saved);
export function currentRun(){return context.runId||null;}
const path=location.pathname,active=path==='/'?'library':path.startsWith('/read/')?'reading'
  :['/inbox','/search'].includes(path)?'collect':path==='/pipeline'?'pipeline':path.startsWith('/setup')?'setup':null;
function render() {
  const links=navigation(context);
  document.querySelectorAll('[data-app-route]').forEach(anchor=>{
    const route=anchor.dataset.appRoute,url=links[route];anchor.hidden=!url;if(url)anchor.href=url;
    if(route===active)anchor.setAttribute('aria-current','page');else anchor.removeAttribute('aria-current');
    if(route==='reading')anchor.title=context.title||'当前文章';
  });
  document.querySelectorAll('[data-context-reading]').forEach(anchor=>{if(links.reading)anchor.href=links.reading;});
  document.querySelectorAll('[data-context-pipeline]').forEach(anchor=>{anchor.href=links.pipeline;});
  document.querySelectorAll('[data-context-publication]').forEach(anchor=>{anchor.href=links.publication;});
  const title=document.querySelector('#app-document');if(title){title.textContent=context.title||'';title.title=context.title||'';}
  const stamp=document.querySelector('#workspace-context');if(stamp)stamp.textContent=context.runId?'当前资料 '+context.runId.slice(0,8):'本地资料';
  try{sessionStorage.setItem('workspace-document',JSON.stringify(context));}catch{}
}
document.addEventListener('workspace:document',event=>{context=mergeContext(context,event.detail);render();});
render();
