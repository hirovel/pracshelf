// The page controls the task; Python owns acceptance rules and persistence.
const processingResults = (() => {
  const el = id => document.getElementById(id);
  let state=null, runId=null, selected=null, part=0, generation=0, saving=false;
  const origins={model_api:'真实 API 结果',external_ai:'外部 AI 结果（提交者声明）',human_edit:'人工修改（提交者声明）',demo:'演示结果'};
  const verdicts={accepted:'人工通过',needs_fix:'需修复',pending:'暂缓'};
  async function request(path, body) {
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const value=await response.json();
    if(!response.ok) throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));
    return value;
  }
  const base=()=>`/api/runs/${runId}/processing`;
  const result=()=>state?.results.find(r=>r.id===selected);
  const draftKey=()=>`processing-review:${runId}:${selected}`;
  let draft=null;
  function readDraft(){
    const r=result(); if(!r)return;
    try{draft=JSON.parse(sessionStorage.getItem(draftKey())||'null');}catch{draft=null;}
    if(!draft) draft={submission_id:crypto.randomUUID(),previous_review_id:r.reviews.at(-1)?.id??null,verdict:'',meaning:'unreviewed',conditions:'unreviewed',note:''};
    for(const name of ['verdict','meaning','conditions','note']) el('processing-'+name).value=draft[name];
  }
  function keepDraft(){
    if(!draft)return;
    draft={...draft,submission_id:crypto.randomUUID()};
    for(const name of ['verdict','meaning','conditions','note']) draft[name]=el('processing-'+name).value;
    sessionStorage.setItem(draftKey(),JSON.stringify(draft));
    el('processing-message').textContent='草稿保存在当前浏览器标签页；尚未保存为验收记录。';
  }
  function showPart(){
    const r=result(); if(!r)return;
    const source=state.task.segments[part];
    const matches=r.payload.segments.filter(p=>p.index===part);
    el('processing-position').textContent=`${part+1} / ${state.task.segments.length}`;
    el('processing-part').value=String(part);
    el('processing-source').textContent=source.source;
    el('processing-target').textContent=matches.length?matches.map(p=>p.text).join('\n\n【重复提交的另一份结果】\n\n'):'此批缺少结果。';
    el('processing-prev').disabled=part===0;
    el('processing-next').disabled=part===state.task.segments.length-1;
    const messages=r.checks.issues.filter(p=>p.index===part).map(p=>p.message);
    for(const f of r.execution.ai_review?.findings??[])if(f.batch===part+1)messages.push(`AI 审阅（待你核对）：${f.title}\n${f.reason}\n建议：${f.suggestion}`);
    el('processing-part-issues').textContent=messages.join('\n\n')||'此批没有已记录的检查问题；仍需阅读核对含义。';
  }
  function show(){
    el('processing-select').replaceChildren();
    for(const r of state.results){
      const option=document.createElement('option'); option.value=r.id;
      option.textContent=`#${r.id} · ${r.payload.executor} · ${origins[r.payload.origin]??r.payload.origin}`;
      el('processing-select').append(option);
    }
    if(!state.results.some(r=>r.id===selected))selected=state.results[0]?.id??null;
    el('processing-select').value=String(selected);
    el('processing-empty').hidden=selected!==null;
    el('processing-content').hidden=selected===null;
    if(selected===null)el('processing-verdict-state').textContent='尚无结果';
    el('processing-task').href=base()+'/task';
    el('processing-json').href=base();
    el('processing-version').textContent=`原文 v${state.task.version_id} · ${state.task.segments.length} 批 · 接收已有结果不调用模型`;
    const r=result(); if(!r)return;
    el('processing-verdict-state').textContent=r.reviews.length?verdicts[r.reviews.at(-1).payload.verdict]:'等待你的人工验收';
    el('processing-check-state').textContent=r.checks.status==='passed'?'结构检查通过':'结构检查发现问题';
    el('processing-check-state').className='badge '+(r.checks.status==='passed'?'succeeded':'needs_review');
    el('processing-checks').textContent=`收到 ${r.checks.received}/${r.checks.expected} 批。${r.checks.scope}`;
    el('processing-issues').replaceChildren();
    for(const issue of r.checks.issues){
      const row=document.createElement('li'),button=document.createElement('button');
      button.textContent=(issue.index===null?'整体':`第 ${issue.index+1} 批`)+'：'+issue.message;
      button.onclick=()=>{if(issue.index!==null&&state.task.segments[issue.index]){part=issue.index;showPart();el('processing-reader').scrollIntoView({block:'start'});}};
      row.append(button);el('processing-issues').append(row);
    }
    el('processing-ai').textContent=r.execution.ai_review
      ? `已有 Codex AI 审阅：${r.execution.ai_review.findings?.length??0} 项发现；不是人工验收。${r.execution.ai_review.summary??''}`
      : '尚无独立语义审阅记录。';
    el('processing-evidence').textContent=r.execution.kind==='observed'
      ? `本地观察到 ${r.execution.call_count} 次调用；接收操作没有新增调用。`
      : r.execution.kind==='demo'?'演示响应，不计入真实模型效果。':'外部提交：内部调用次数、成本与过程未知。工具及模型名称来自提交者声明。';
    el('processing-trace-link').hidden=!r.execution.trace_url;
    if(r.execution.trace_url)el('processing-trace-link').href=r.execution.trace_url;
    el('processing-record').textContent=JSON.stringify({created:r.created,task_key:r.task_key,payload:r.payload,execution:r.execution},null,2);
    el('processing-review-history').replaceChildren();
    for(const review of [...r.reviews].reverse()){
      const p=document.createElement('p');
      p.textContent=`${new Date(review.created).toLocaleString()} · ${review.origin==='demo'?'演示':'本地人工入口'} · ${verdicts[review.payload.verdict]}\n${review.payload.note||'无附注'}`;
      el('processing-review-history').append(p);
    }
    el('processing-part').replaceChildren();
    for(const s of state.task.segments){const o=document.createElement('option');o.value=s.index;o.textContent=`第 ${s.index+1} 批${s.kind==='code'?' · 代码保留':''}`;el('processing-part').append(o);}
    part=Math.min(part,state.task.segments.length-1);
    el('processing-review-fields').disabled=saving||!r.current;
    readDraft();showPart();
    document.dispatchEvent(new CustomEvent('processing-updated',{detail:{state,result:r}}));
  }
  async function load(id, translation, available){
    const token=++generation;
    el('processing-panel').hidden=!available;
    if(!available){runId=id;state=null;return;}
    if(id!==runId){selected=null;part=0;el('processing-message').textContent='';el('processing-error').textContent='';}
    runId=id;
    el('processing-native').replaceChildren();
    for(const item of [...translation.history].reverse()){
      const o=document.createElement('option');o.value=item.id;o.textContent=`翻译运行 #${item.id} · ${item.prompt_version}`;el('processing-native').append(o);
    }
    el('processing-receive-native').disabled=!translation.history.length;
    const value=await request(base());
    if(token!==generation)return;
    state=value;show();
  }
  async function change(path, body, after){
    if(saving)return;
    const active=runId;saving=true;
    el('processing-error').textContent='';
    el('processing-panel').setAttribute('aria-busy','true');
    el('processing-actions').disabled=true;
    el('processing-review-fields').disabled=true;
    try{
      const value=await request(path,body);
      if(active!==runId)return;
      if(after)after(value);
      state=value;show();el('processing-message').textContent='已保存。原结果和历史记录保持原样。';
    }catch(error){if(active===runId)el('processing-error').textContent=error.message;}
    finally{saving=false;el('processing-actions').disabled=false;el('processing-panel').setAttribute('aria-busy','false');if(result())el('processing-review-fields').disabled=!result().current;}
  }
  el('processing-select').onchange=()=>{selected=Number(el('processing-select').value);part=0;show();};
  el('processing-part').onchange=()=>{part=Number(el('processing-part').value);showPart();};
  el('processing-prev').onclick=()=>{part--;showPart();};
  el('processing-next').onclick=()=>{part++;showPart();};
  el('processing-receive-native').onclick=()=>change(base()+'/translations/'+el('processing-native').value,{},value=>{selected=value.results.find(r=>r.execution.translation_id===Number(el('processing-native').value))?.id??null;});
  el('processing-file').onchange=async()=>{
    const file=el('processing-file').files[0];if(!file)return;
    const active=runId,path=base()+'/results';
    try{if(file.size>4_000_000)throw new Error('请选择小于 4 MB 的结果 JSON 文件。');const body=JSON.parse(await file.text());if(active!==runId)return;await change(path,body,()=>{selected=null;});}
    catch(error){el('processing-error').textContent=error.message;}
    finally{el('processing-file').value='';}
  };
  el('processing-review-form').oninput=keepDraft;
  el('processing-review-form').onsubmit=event=>{event.preventDefault();const key=draftKey();change(base()+`/results/${selected}/reviews`,draft,()=>{sessionStorage.removeItem(key);});};
  return {load};
})();
