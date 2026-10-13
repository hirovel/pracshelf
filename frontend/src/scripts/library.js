import {categories,filters,libraryView} from './library-view.js';

const el=id=>document.getElementById(id);
const origins={agent:'助手归类',model_api:'模型分类',human:'本人分类',demo:'演示分类'};
let rows=[],filter='all',loaded=false;
function node(tag,text,className){const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(className)element.className=className;return element;}
function fromURL(){
  const params=new URLSearchParams(location.search);
  filter=Object.hasOwn(filters,params.get('view'))?params.get('view'):'all';
  el('search').value=params.get('q')||'';
  el('category').value=Object.hasOwn(categories,params.get('category'))?params.get('category'):'';
}
function render(updateURL=false){
  const query=el('search').value,category=el('category').value;
  const visible=libraryView(rows,{filter,query,category});
  el('clear-search').hidden=!query;
  document.querySelectorAll('[data-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.filter===filter)));
  const list=el('articles');list.replaceChildren();
  el('count').textContent=loaded?visible.length+' 篇':'';
  el('empty').hidden=!loaded||visible.length>0;
  el('empty').textContent=query.trim()||category?'没有符合条件的资料。请更换搜索词或分类。':{all:'尚无资料。可从收件箱选择并保存原文。',kept:'尚无已保留的资料。',notes:'尚无个人笔记。可在文章页的“笔记”中自行记录。',archived:'尚无归档资料。'}[filter];
  visible.forEach(row=>{
    const entry=node('article',undefined,'catalogue-entry'),date=node('p','发布','entry-date');
    if(row.publicationDate){const time=node('time',row.publicationDate);time.dateTime=row.publicationDate;date.append(time);}else date.append(node('span','日期未知'));
    const heading=node('h2'),link=node('a',row.title);link.href='/read/'+encodeURIComponent(row.run_id);heading.append(link);
    const headingRow=node('div',undefined,'entry-heading'),kind=node('a',categories[row.category],'entry-kind');
    kind.href='/?category='+encodeURIComponent(row.category);kind.setAttribute('aria-label','筛选'+categories[row.category]);
    kind.title=row.classification?.reason||'尚无分类依据';headingRow.append(heading,kind);
    const meta=node('div',undefined,'entry-meta');
    meta.append(node('span',row.author||'作者未记录','entry-author'),node('span',row.domain),node('span','原文 v'+row.version_id),node('span',row.has_translation?'中英对照 · '+row.status:'原文'));
    if(row.status==='演示资料'&&!row.has_translation)meta.append(node('span','演示资料'));
    entry.append(date,headingRow,meta);
    if(row.classification?.reason)entry.append(node('p','分类依据：'+row.classification.reason+' · '+(origins[row.classification.origin]||'来源未记录'),'entry-reason'));
    if(row.note?.trim()){const note=node('p',undefined,'entry-note');note.append(node('span','个人笔记'),document.createTextNode(row.note));entry.append(note);}
    list.append(entry);
  });
  if(updateURL){
    const params=new URLSearchParams();if(filter!=='all')params.set('view',filter);if(query)params.set('q',query);if(category)params.set('category',category);
    history.replaceState(null,'',location.pathname+(params.size?'?'+params.toString():''));
  }
}
function focusSearch(){el('search').focus();}
document.querySelectorAll('[data-filter]').forEach(button=>button.onclick=()=>{filter=button.dataset.filter;render(true);});
el('find-form').onsubmit=event=>{event.preventDefault();render(true);};
el('search').oninput=()=>render(true);el('category').onchange=()=>render(true);
el('clear-search').onclick=()=>{el('search').value='';render(true);focusSearch();};
el('library-search').onclick=focusSearch;
addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();focusSearch();}});
addEventListener('popstate',()=>{fromURL();render();});
async function load(){
  el('error').hidden=true;el('loading').hidden=false;el('reload-library').disabled=true;
  try{
    const response=await fetch('/api/library');
    if(!response.ok)throw new Error('资料读取失败（HTTP '+response.status+'）。');
    const value=await response.json();if(!Array.isArray(value))throw new Error('资料响应格式错误。');
    rows=value;loaded=true;render();
  }catch(error){el('error-text').textContent=error.message;el('error').hidden=false;}
  finally{el('loading').hidden=true;el('reload-library').disabled=false;}
}
el('reload-library').onclick=load;
fromURL();render();load();
