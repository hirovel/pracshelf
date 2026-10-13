type Candidate = {preview_id:number;run_id:string;title:string;content_hash:string;eligible:boolean;reasons:string[]};
type Job = {request:{preview_ids:number[];allow_empty:boolean};id:string;submission_id:string;created:string;finished:string|null;status:string;stage:string;error:string|null;inputs:Candidate[];events:unknown[];log:string;report:any;preview_url:string|null};
const el = <T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById('rb-'+id) as T;
const node = (tag:string,text:string,className='')=>{const n=document.createElement(tag);n.textContent=text;n.className=className;return n;};
const stageNames:Record<string,string>={validate:'校验确认版本',export:'导出快照',build:'Astro 构建',verify:'复核版本',complete:'完成'};
let busy=false, remoteBusy=false, buildReady=false, timer:ReturnType<typeof setTimeout>|undefined;
let pending:{submission_id:string;preview_ids:number[];allow_empty:boolean}|null=null;
const selected=()=>Array.from(document.querySelectorAll<HTMLInputElement>('[data-preview]:checked')).map(n=>Number(n.dataset.preview));
function buttons(){
  const ids=selected(),empty=el<HTMLInputElement>('empty');
  el<HTMLButtonElement>('build').disabled=busy||remoteBusy||!buildReady||(!ids.length&&!empty.checked);
  empty.disabled=busy||remoteBusy||ids.length>0;
  document.querySelectorAll<HTMLInputElement>('[data-preview]').forEach(n=>{n.disabled=busy||remoteBusy||empty.checked;});
}
async function request(body?:unknown){
  const response=await fetch('/api/reading-builds',body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));
  return result;
}
function details(label:string,value:unknown){const d=document.createElement('details');d.append(node('summary',label),node('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));return d;}
function renderJobs(jobs:Job[]){
  const latest=jobs[0], stages=['validate','export','build','verify'];
  document.querySelectorAll<HTMLElement>('[data-build-stage]').forEach((n,i)=>{
    const current=stages.indexOf(latest?.stage);
    n.dataset.state=!latest?'':latest.status==='succeeded'||i<current?'done':i===current?(latest.status==='failed'?'failed':'active'):'';
  });
  // Keep open log sections open across polling updates.
  const open=new Set(Array.from(el('jobs').querySelectorAll<HTMLDetailsElement>('details[open]')).map(n=>n.dataset.key));
  el('jobs').replaceChildren(...jobs.map(job=>{
    const box=node('article','','job'),success=job.status==='succeeded',failed=job.status==='failed';
    box.append(node('h3',`${success?'构建成功':failed?'构建失败':'正在构建'} · ${stageNames[job.stage]||job.stage}`,success?'success':failed?'failure':''));
    box.append(node('p',`${new Date(job.created).toLocaleString()} · ${job.inputs.length} 篇 · 未部署`,'subtle'),node('code',job.id));
    if(job.error)box.append(node('p',job.error,'failure'));
    if(job.preview_url){const a=node('a','打开本次阅读站','preview-link') as HTMLAnchorElement;a.href=job.preview_url;a.target='_blank';a.rel='noopener';box.append(a);}
    if(job.report)box.append(node('p',`产物 ${job.report.artifacts.length} 个文件 · 模型调用 0 次`,'subtle'));
    const sections=[details('输入版本与内容标识',{selection:job.request,snapshots:job.inputs}),details('阶段记录',job.events),details('构建进程日志',job.log||'此阶段尚未产生构建进程日志。')];
    if(job.report)sections.push(details('产物清单与 SHA-256',job.report.artifacts));
    sections.forEach((d,i)=>{d.dataset.key=job.id+':'+i;d.open=open.has(d.dataset.key);box.append(d);});
    return box;
  }));
  if(!jobs.length)el('jobs').append(node('p','当前没有网页构建记录。','subtle'));
}
async function refresh(){
  clearTimeout(timer);
  const state=await request(),chosen=new Set(selected());
  buildReady=state.environment?.ready===true;
  el('environment').textContent=buildReady?'博客构建依赖已安装 · 本地编译，不调用模型。':state.environment?.error||'无法读取博客构建条件。';
  const mode=el('mode');mode.hidden=state.mode!=='demo';
  mode.textContent='隔离演示 · 测试笔记与测试确认；不会加入正式博客。';
  remoteBusy=state.jobs.some((j:Job)=>j.status==='running');
  const eligible=state.candidates.filter((c:Candidate)=>c.eligible).length;
  el('candidates').replaceChildren(...state.candidates.map((c:Candidate)=>{
    const row=node('div','','candidate-row');
    if(c.eligible){const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.dataset.preview=String(c.preview_id);input.checked=chosen.has(c.preview_id);input.addEventListener('change',()=>{pending=null;buttons();});label.append(input,node('span',c.title));row.append(label);}
    else row.append(node('h3',c.title));
    const a=node('a',`版本 #${c.preview_id} · ${c.eligible?'已确认':'未满足构建条件'}`) as HTMLAnchorElement;a.href='/publication?run='+encodeURIComponent(c.run_id);row.append(a);
    row.append(node('p',c.eligible?'SHA-256 '+c.content_hash:c.reasons.join(' ')));return row;
  }));
  if(!state.candidates.length)el('candidates').append(node('p','当前没有公开快照。','subtle'));
  el('message').textContent=remoteBusy?'构建正在运行。页面每两秒更新状态。':`可选的已确认快照：${eligible} 个。`;
  renderJobs(state.jobs);buttons();
  if(remoteBusy)timer=setTimeout(()=>refresh().catch(showError),2000);
}
function showError(error:unknown){el('error').textContent=error instanceof Error?error.message:String(error);}
el('refresh').addEventListener('click',()=>{el('error').textContent='';refresh().catch(showError);});
el('empty').addEventListener('change',()=>{pending=null;buttons();});
el('build').addEventListener('click',async()=>{
  pending??={submission_id:crypto.randomUUID(),preview_ids:selected(),allow_empty:el<HTMLInputElement>('empty').checked};
  busy=true;buttons();el('error').textContent='';
  timer=setTimeout(()=>refresh().catch(showError),500);
  try{await request(pending);pending=null;}catch(error){showError(error);}finally{busy=false;await refresh().catch(showError);buttons();}
});
refresh().catch(showError);
