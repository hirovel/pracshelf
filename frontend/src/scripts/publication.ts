import {renderNote} from '../../reading/markdown.mjs';

const element = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById('pub-'+id) as T;
const check = (id: string) => element<HTMLInputElement>(id).checked;
let state: any = null, run = '', busy = false, dirty = false;
const base = () => '/api/runs/'+encodeURIComponent(run)+'/publication';
const labels: Record<string,string> = {note_id:'稳定笔记标识',title:'标题',source_url:'原始出处',source_date:'原文日期',source_date_status:'日期依据状态',category:'类别',note:'本人短笔记',purpose:'用途 / 保留理由',quote:'所选原文摘录',applicability:'适用条件与依据',freshness:'适用性',checked_on:'核查日期',trial:'试用状态',content_origin:'笔记来源',note_updated_at:'笔记保存时间'};
async function request(url: string, body?: unknown) {
  const response = await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if (!response.ok) {const value=await response.json();throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));}
  return response;
}
function buttons() {
  const p=state?.latest;
  element<HTMLButtonElement>('prepare').disabled=busy||!state;
  element<HTMLButtonElement>('refresh').disabled=busy||!run;
  element<HTMLSelectElement>('source').disabled=busy;
  for(const id of ['note','purpose','quote']) element<HTMLInputElement>(id).disabled=busy;
  element<HTMLInputElement>('post-title').disabled=busy;
  element<HTMLButtonElement>('approve').disabled=busy||dirty||!p?.current||!!p?.approval||!!p?.blockers.length||!check('confirm')|| (!!p?.public_note.quote&&!check('rights'));
  element<HTMLButtonElement>('export-preview').disabled=busy||dirty||!p?.current;
  element<HTMLButtonElement>('export-approved').disabled=busy||dirty||!p?.current||!p?.approval;
}
function render() {
  const p=state.latest;
  document.dispatchEvent(new CustomEvent('workspace:document',{detail:{runId:run,title:state.title,readable:!!state.input?.version_id}}));
  element('private').replaceChildren(...state.private_fields.map((v:string)=>{const li=document.createElement('li');li.textContent=v;return li;}));
  for(const id of ['note','purpose','quote']) element(id+'-hint').textContent=state.available_fields[id]?'已保存':'尚未填写';
  element<HTMLAnchorElement>('read').href='/read/'+encodeURIComponent(run)+'#curation-panel';
  element<HTMLAnchorElement>('work').href='/pipeline?run='+encodeURIComponent(run);
  element('empty').hidden=!!p;element('preview').hidden=!p;
  element('audit').replaceChildren((globalThis as any).savedRecordView.render(state));
  element('history').replaceChildren(...state.history.map((entry:any)=>{
    const d=document.createElement('details'),s=document.createElement('summary');
    s.textContent=`预览 #${entry.id} · ${new Date(entry.created).toLocaleString()} · ${entry.current?'当前':'历史 / 已过期'} · ${entry.approval?'已确认':'未确认'} · 导出 ${entry.exports.length} 种快照`;
    d.append(s,(globalThis as any).savedRecordView.render(entry));return d;
  }));
  if(p){
    element('state').textContent=p.current?(p.approval?'本地已确认 · 未部署':'未批准预览 · 未部署'):'历史预览 · 需更新';
    element('title').textContent=p.public_note.title;
    element('body').innerHTML=renderNote(p.public_note.note);
    element('source-detail').textContent='参考来源：'+p.public_note.source_url;
    element('fields').replaceChildren(...Object.keys(labels).filter(key=>key!=='note').map(key=>[key,p.public_note[key]]).flatMap(([key,value])=>{
      const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=labels[key]||key;
      dd.textContent=value==null||value===''?'未记录 / 未选择':key==='content_origin'?({human:'本人入口保存（非身份认证）',demo:'演示记录',no_personal_note:'无人工笔记'}[String(value)]||String(value)):key==='source_date_status'?(value==='confirmed'?'人工确认':'未知，未推断'):String(value);return[dt,dd];
    }));
    element('json').textContent=JSON.stringify(p.public_note,null,2);
    element('hash').textContent='冻结内容 SHA-256：'+p.content_hash;
    element('stale').textContent=!p.current?'原文、整理记录或验收已改变。这一版不能再确认或导出。':dirty?'字段选择已变化；请先更新预览。':'';
    element('blockers').replaceChildren(...(p.blockers.length?p.blockers:['版本与必填项检查通过；内容含义、公开范围仍需你核对。']).map((v:string)=>{const li=document.createElement('li');li.textContent=v;return li;}));
    element('rights-label').hidden=!p.public_note.quote;
  } else element('state').textContent='尚未生成预览';
  element('step-2').textContent=p?'02 预览已生成':'02 等待生成预览';
  element('step-3').textContent=p?.approval?'03 本地已确认':'03 等待本人确认';
  element('step-4').textContent=p?.exports.length?'04 已导出 · 未部署':'04 等待导出';
  buttons();
}
async function action(work:()=>Promise<void>) {
  busy=true;buttons();element('error').textContent='';element('message').textContent='';
  try {await work();}catch(e){element('error').textContent=(e as Error).message;}finally{busy=false;buttons();}
}
async function load(){state=null;buttons();element('preview').hidden=true;state=await(await request(base())).json();dirty=false;element<HTMLInputElement>('confirm').checked=false;element<HTMLInputElement>('rights').checked=false;
  for(const id of ['note','purpose','quote']) element<HTMLInputElement>(id).checked=state.latest?state.latest.options['include_'+id]:id==='note';
  element<HTMLInputElement>('post-title').value=state.latest?.options.title||'';render();}
element('source').addEventListener('change',()=>action(async()=>{run=element<HTMLSelectElement>('source').value;history.replaceState(null,'','/publication?run='+encodeURIComponent(run));await load();}));
element('refresh').addEventListener('click',()=>action(load));
for(const id of ['note','purpose','quote'])element(id).addEventListener('change',()=>{dirty=true;element<HTMLInputElement>('confirm').checked=false;if(state)render();});
element('post-title').addEventListener('input',()=>{dirty=true;element<HTMLInputElement>('confirm').checked=false;if(state)render();});
for(const id of ['confirm','rights'])element(id).addEventListener('change',buttons);
element('prepare').addEventListener('click',()=>action(async()=>{
  state=await(await request(base()+'/previews',{version_id:state.input.version_id,curation_id:state.input.curation_id,previous_preview_id:state.latest?.id??null,title:element<HTMLInputElement>('post-title').value,include_note:check('note'),include_purpose:check('purpose'),include_quote:check('quote')})).json();
  dirty=false;element<HTMLInputElement>('confirm').checked=false;element<HTMLInputElement>('rights').checked=false;render();element('message').textContent='预览已冻结。仅写入本地记录，没有公开内容。';
}));
element('approve').addEventListener('click',()=>action(async()=>{
  state=await(await request(base()+`/previews/${state.latest.id}/approve`,{content_hash:state.latest.content_hash,content_reviewed:check('confirm'),quote_rights_confirmed:check('rights')})).json();render();element('message').textContent='这一版已在本地确认，尚未部署。';
}));
for(const mode of ['preview','approved'])element('export-'+mode).addEventListener('click',()=>action(async()=>{
  const id=state.latest.id,response=await request(base()+`/previews/${id}/export?mode=${mode}`,{}),blob=await response.blob();
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`note-${id}-${mode}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  await load();element('message').textContent=mode==='preview'?'已下载未批准预览；禁止将它当作发布包。':'已下载已确认快照；没有上传或部署。';
}));
action(async()=>{
  const rows=await(await request('/api/library')).json(),select=element<HTMLSelectElement>('source');
  select.replaceChildren(...rows.map((r:any)=>{const o=document.createElement('option');o.value=r.run_id;o.textContent=r.title;return o;}));
  const requested=new URLSearchParams(location.search).get('run');run=rows.find((r:any)=>r.run_id===requested)?.run_id||rows[0]?.run_id||'';
  if(!run){element('empty').textContent='还没有已保存原文的资料，请先到收集与审核导入。';return;}select.value=run;await load();
});
