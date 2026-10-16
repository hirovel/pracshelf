/* Catalogue and outline are read-only projections of the existing library. */
import {libraryView} from './library-view.js';

const list=document.querySelector('#source-list'),search=document.querySelector('#source-search');
let rows=[],currentId=location.pathname.split('/').at(-1);
function render(){
  const visible=libraryView(rows,{query:search.value});list.replaceChildren();
  document.querySelector('#source-count').textContent=String(rows.length);
  for(const row of visible){
    const link=document.createElement('a'),title=document.createElement('span'),meta=document.createElement('small');
    link.href='/read/'+encodeURIComponent(row.run_id)+'?mode=source';
    title.textContent=row.title;meta.textContent=[row.author,row.published].filter(Boolean).join(' · ');
    if(row.run_id===currentId)link.setAttribute('aria-current','page');
    link.append(title,meta);list.append(link);
  }
  if(!visible.length){const empty=document.createElement('p');empty.textContent=search.value?'没有匹配的资料。':'暂无已保存资料。';list.append(empty);}
}
function outline(){
  const target=document.querySelector('#source-outline');target.replaceChildren();
  for(const original of document.querySelectorAll('#outline-links a')){
    const link=original.cloneNode(true);
    link.onclick=event=>{event.preventDefault();original.click();closeSources();};target.append(link);
  }
}
const toggle=document.querySelector('#reading-sources'),sources=document.querySelector('#reader-sources');
const narrow=matchMedia('(max-width:900px)');
function syncSources(){
  const open=document.body.classList.contains('sources-open');
  sources.inert=narrow.matches&&!open;
  sources.setAttribute('aria-hidden',String(sources.inert));
  toggle.setAttribute('aria-expanded',String(open));
}
function closeSources(){document.body.classList.remove('sources-open');syncSources();}
toggle.onclick=()=>{document.body.classList.toggle('sources-open');syncSources();};
narrow.addEventListener('change',syncSources);syncSources();
addEventListener('keydown',event=>{if(event.key==='Escape')closeSources();});
document.addEventListener('reader:ready',()=>{outline();});
document.addEventListener('reader:mode',outline);
document.addEventListener('curation-updated',event=>{
  const row=rows.find(row=>row.run_id===event.detail.runId);
  if(row){row.curation=event.detail.state;row.note=event.detail.state.latest?.payload.note||'';render();}
});
search.addEventListener('input',render);
fetch('/api/library').then(async response=>{
  if(!response.ok)throw new Error('资料列表读取失败');rows=await response.json();render();
}).catch(error=>{list.replaceChildren();const message=document.createElement('p'),retry=document.createElement('button');message.textContent=error.message;retry.textContent='刷新页面重试';retry.onclick=()=>location.reload();list.append(message,retry);});
// Deferred scripts can execute after the initial reader request has completed.
outline();
