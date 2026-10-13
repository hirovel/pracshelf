// UI adapter: Python Candidates owns versions, budgets, checks and persistence.
const get = (id: string): any => document.getElementById(id);
let current: any = null, busy = false;
let importId = crypto.randomUUID(), processId = crypto.randomUUID(), screenId = crypto.randomUUID();
let filterState = 'all', listGeneration = 0;
let searchTimer: ReturnType<typeof setTimeout> | undefined;
const initialParams = new URLSearchParams(location.search);
get('candidate-search').value = initialParams.get('q') || '';
get('filter-date').value = initialParams.get('freshness') || 'all';
get('filter-origin').value = initialParams.get('origin') || 'all';
filterState = initialParams.get('state') || 'all';
get('advanced-filters').open = get('filter-date').value!=='all'||get('filter-origin').value!=='all';
const compactLayout = matchMedia('(max-width:960px)');
compactLayout.addEventListener('change',()=>{get('queue').open=!compactLayout.matches||!current;});
function syncUrl() {
  const params = new URLSearchParams();
  if (current) params.set('id',current.id);
  if (get('candidate-search').value.trim()) params.set('q',get('candidate-search').value.trim());
  if (filterState !== 'all') params.set('state',filterState);
  for (const [key,id] of [['freshness','filter-date'],['origin','filter-origin']]) if(get(id).value!=='all')params.set(key,get(id).value);
  history.replaceState(null,'',location.pathname+(params.size?'?'+params.toString():''));
}
function markSelected() {
  document.querySelectorAll<HTMLButtonElement>('[data-candidate]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.candidate===current?.id)));
}
let pollTimer: ReturnType<typeof setTimeout> | undefined, watchedSubmission: string | null = null;
const dates: Record<string,string> = {within_window:'在日期窗口内',outside_window:'已超出日期窗口',unknown_date:'发布日期未知',future_date:'日期在未来，需核查'};
const recommendations: Record<string,string> = {read:'建议进一步阅读',focus:'建议只看重点',skip:'建议跳过',uncertain:'暂不能判断'};
const states: Record<string,string> = {ready:'原文已保存，等待审核',awaiting_capture:'等待保存原文',running:'正在审核',completed:'审核执行完成；本人决定请在阅读页记录',failed:'审核失败，可查看记录后重试',interrupted:'上次运行中断'};
get('as-of').value = new Date().toISOString().slice(0,10);
async function request(path: string, options?: RequestInit) {
  const response = await fetch(path,options), value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
  return value;
}
const post = (path: string,payload?: unknown) => request(path,{method:'POST',headers:{'Content-Type':'application/json'},...(payload ? {body:JSON.stringify(payload)} : {})});
function selectView(view: string, focus = false) {
  document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button => {
    const selected = button.dataset.view === view;
    button.setAttribute('aria-selected',String(selected));button.tabIndex = selected ? 0 : -1;
    get('view-'+button.dataset.view).hidden = !selected;
    if (selected && focus) button.focus();
  });
}
const views = ['overview','evidence','trace'];
document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button => {
  button.onclick = () => selectView(button.dataset.view!);
  button.onkeydown = event => {
    const index = views.indexOf(button.dataset.view!);
    const next = event.key === 'ArrowRight' ? (index+1)%3 : event.key === 'ArrowLeft' ? (index+2)%3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : -1;
    if (next >= 0) {event.preventDefault();selectView(views[next],true);}
  };
});
function lock(value: boolean) {
  busy = value;
  for (const id of ['import-button','process','refresh']) get(id).disabled = value;
  document.querySelectorAll<HTMLButtonElement>('[data-candidate]').forEach(b=>b.disabled=value);
  get('screen-button').disabled=value||!current?.screening_action;
  get('screening').setAttribute('aria-busy',String(value));
  get('import-button').textContent=value?'处理中…':'保存原文';
  get('candidate-form').querySelectorAll('input,textarea,select').forEach((e: any) => e.disabled = value);
  if (!value && current) get('process').disabled = !current.processing.action;
}
function bullets(id: string, items: string[], empty: string) {
  get(id).replaceChildren();
  for (const text of items.length ? items : [empty]) {const li = document.createElement('li');li.textContent = text;get(id).append(li);}
}
function render(state: any) {
  const selectionChanged=current?.id!==state.id;
  current = state;get('screening').hidden = false;get('candidate-empty').hidden = true;
  if(selectionChanged && compactLayout.matches)get('queue').open=false;
  get('source-link').textContent=new URL(state.input.url).hostname.replace(/^www\./,'');
  get('source-link').href=state.input.url;
  get('article-date').textContent=state.document?.published||'日期待核实';
  get('candidate-title').textContent = state.document?.title || state.input.url;
  document.dispatchEvent(new CustomEvent('workspace:document',{detail:{runId:state.pipeline_id,candidateId:state.id,title:get('candidate-title').textContent}}));
  const flow=state.processing;
  const stageNames:Record<string,string>={fetch:'获取网页',extract:'提取正文',screen:'价值与时效初筛'};
  get('stage-status').textContent=flow.status==='completed'?'原文已保存，可以开始阅读。':flow.status==='failed'?`${stageNames[flow.stage]||'处理'}失败；成功阶段已保留。`:flow.status==='running'?`正在处理：${stageNames[flow.stage]||'保存结果'}。`:'尚未完成处理。';
  get('process').textContent=flow.action==='retry'?'重试失败阶段':flow.status==='completed'?'原文已保存':flow.status==='running'?'正在处理…':'开始处理';
  get('process').hidden=flow.status==='completed';
  for(const stage of flow.stages){
    const item=get('step-'+stage.id);
    item.textContent=`${stage.label} · ${stage.status==='succeeded'?'成功':stage.status==='running'?'运行中':stage.status==='failed'?'失败':stage.status==='blocked'?'等待前一步':'待运行'} · ${stage.attempts} 次`;
    item.dataset.state=stage.status==='succeeded'?'done':stage.status==='failed'?'failed':stage.id===flow.stage&&flow.status==='running'?'active':'waiting';
  }
  for (const id of ['read','curate']) get(id).hidden = !state.version_id;
  get('read').href = '/read/'+state.pipeline_id;get('work').href = '/workbench?run='+state.pipeline_id;
  get('curate').href = '/read/'+state.pipeline_id+'#curation-panel';get('json').href = '/api/candidates/'+state.id;
  const preview=state.preview;
  get('budget').textContent = preview?`检查 ${preview.checked_characters} / ${preview.source_characters} 字符 · ${preview.source_scope==='full_saved_text'?'已保存全文':'首、中、尾取样'}。${preview.max_output_tokens?'输出上限 '+preview.max_output_tokens+' tokens。':'Codex 内部请求数与输出上限未知。'}相同输入复用已有结果。`:'保存原文后可生成中文预览。';
  get('preview-scope').textContent=preview?`${preview.source_scope==='full_saved_text'?'基于已保存全文':'基于原文节选'} · ${preview.checked_characters.toLocaleString()} 字符`:'';
  get('screen-button').textContent=preview?.latest?.status==='running'?'生成中…':state.screening_action==='retry'?'重试阅读建议':!state.screening_action&&preview?.latest?'已生成':'生成阅读建议';
  get('source-facts').textContent=state.version_id?`正文 ${state.document.characters.toLocaleString()} 字符 · 版本 ${state.version_id} · 初筛尝试 ${state.attempts.length} 次`:'等待原文保存';
  const check=state.date_check;
  get('current-date').textContent=`${dates[check.status]} · 来源日期：${check.published||'未知'} · 按 ${check.as_of} 检查 ${check.max_age_days} 天窗口。来源日期未独立核实。`;
  get('error').textContent = flow.error || state.latest?.error || '';
  renderOutput(state.preview?.latest?.current_output || state.preview?.latest?.output);renderTrace(state);lock(busy);syncUrl();markSelected();
}
function renderOutput(output: any) {
  get('result').hidden = !output;get('pending-result').hidden = !!output;get('evidence').replaceChildren();
  if (!output) {get('evidence').textContent = '尚无审核依据。';return;}
  const judgment = output.judgment;
  get('recommendation').textContent = 'AI '+recommendations[output.effective_recommendation||judgment.recommendation]+(output.disposition === 'needs_review' ? ' · 待核实' : ' · 尚未确认');
  get('value').textContent = judgment.learning_value;get('reason').textContent = judgment.reason;
  const date = output.date_check;
  get('date-result').textContent = dates[date.status]+'。来源声明：'+(date.published || '未知')+'；优先窗口 '+date.max_age_days+' 天。';
  get('check-result').textContent = '引文定位'+(output.quote_alignment === 'passed' ? '通过' : '未通过')+'；语义忠实度与当前适用性未验证。';
  bullets('conditions',judgment.conditions,'原文未给出明确条件，仍需结合使用场景判断。');
  bullets('unknowns',[...judgment.time_dependencies.map((v: string) => '时间依赖：'+v),...judgment.unknowns],'没有额外条目；不代表已经验证。');
  output.evidence_checks.forEach((e: any,index: number) => {
    const article = document.createElement('article');article.className = 'evidence-item';
    const label = document.createElement('small');label.textContent = `证据 ${index+1} / ${e.matched ? '逐字匹配 · 字符位置 '+e.start : '未在原文找到'}`;
    const quote = document.createElement('blockquote');quote.textContent = e.quote;
    const support = document.createElement('p');support.textContent = '模型解释：'+e.supports;
    article.append(label,quote,support);get('evidence').append(article);
  });
}
function renderTrace(state: any) {
  const input = {...state.input,version_id:state.version_id,characters:state.document?.characters};
  get('candidate-input').textContent = JSON.stringify(input,null,2);get('input-facts').replaceChildren();
  get('processing-history').textContent=JSON.stringify(state.processing,null,2);
  const origins: Record<string,string> = {user_link:'本人提供',agent_supplied:'Agent 提供',rss:'RSS 订阅',web_search:'网页搜索',demo:'演示材料'};
  for (const [label,value] of [['学习目标',input.goal],['原文链接',input.url],['来源',origins[input.origin]],['来源说明',input.source_note],['原文版本',input.version_id ?? '尚未保存'],['正文长度',input.characters ? input.characters+' 字符' : '未知'],['审核日期',input.as_of+' (UTC)']]) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');dt.textContent = label;dd.textContent = String(value);get('input-facts').append(dt,dd);
  }
  get('trace-count').textContent = state.attempts.length;
  get('trace-tokens').textContent = (state.preview?.latest?.output?.usage?.total_tokens ?? state.preview?.latest?.response?.usage?.total_tokens)?.toLocaleString() ?? '未知';
  const active=state.preview?.latest;
  get('trace-execution').textContent = active?.input?.execution
    ? `${active.input.execution.engine} / ${active.input.execution.adapter} · JSON 结构解析 → 原文引文校验 · ${active.response?.provider==='codex'?'Codex 任务 1 次，内部请求数未知':'HTTP 请求尝试 '+(active.response?.request_count??'未知')+' 次'}`
    : active ? '历史调用使用直接 HTTP；实际请求次数未单独统计。' : '尚无当前配置的模型运行记录。';
  get('trace-duration').textContent = active?.finished ? ((Date.parse(active.finished)-Date.parse(active.started))/1000).toFixed(1)+' 秒' : '—';
  get('attempts').replaceChildren();if (!state.attempts.length) get('attempts').textContent = '尚无模型调用。';
  for (const attempt of [...state.attempts].reverse()) {
    const detail = document.createElement('details');detail.className = 'trace-entry';
    detail.open=attempt.id===active?.id;
    const summary = document.createElement('summary');summary.textContent = `运行 ${attempt.id} / ${attempt.input?.execution?.engine||'直接 HTTP（历史）'} / ${states[attempt.status] || attempt.status} / ${attempt.output?.usage?.total_tokens ?? attempt.response?.usage?.total?.totalTokens ?? '未知'} tokens`;detail.append(summary);
    const {langchain_message,...wire}=attempt.response||{};
    for (const [label,value] of [['时间、状态与错误',{started:attempt.started,finished:attempt.finished,status:attempt.status,error:attempt.error}],['原文范围、提示词与调用配置（发送前）',attempt.input],['实际调用与提供方响应',wire],['LangChain 解析后的消息',langchain_message],['运行时结构化输出与引文检查',attempt.output],['当前规则复核（不调用模型）',attempt.current_output]]) {
      if(value===undefined)continue;
      const part=document.createElement('details'), labelNode=document.createElement('summary'), pre=document.createElement('pre');labelNode.textContent=String(label);pre.textContent=JSON.stringify(value,null,2);part.append(labelNode,pre);detail.append(part);
    }
    get('attempts').append(detail);
  }
}
async function list() {
  const token=++listGeneration;
  const focused=(document.activeElement as HTMLElement)?.dataset.candidate;
  const params=new URLSearchParams({q:get('candidate-search').value.trim(),state:filterState,freshness:get('filter-date').value,origin:get('filter-origin').value});
  get('candidate-list').setAttribute('aria-busy','true');get('queue-error').textContent='';
  document.querySelectorAll<HTMLButtonElement>('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.filter===filterState)));
  try {
    const rows=await request('/api/candidates?'+params);
    if(token!==listGeneration)return;
    get('candidate-list').replaceChildren();get('queue-count').textContent=rows.length+' 条匹配';get('queue-empty').hidden=rows.length!==0;
    for(const row of rows) {
      const button=document.createElement('button');button.type='button';button.className='candidate-card';button.dataset.candidate=row.id;button.disabled=busy;
      const title=document.createElement('strong');title.textContent=row.document?.title||row.input.url;
      const domain=document.createElement('span');domain.textContent=new URL(row.input.url).hostname;domain.className='card-domain';
      const meta=document.createElement('small');meta.textContent=({completed:'原文已保存',failed:'采集失败',running:'采集中',ready:'待采集'}[row.processing.status]||row.processing.status)+' · '+dates[row.date_check.status];
      button.append(domain,title,meta);button.onclick=()=>action(async()=>{
        processId=crypto.randomUUID();screenId=crypto.randomUUID();
        render(await request('/api/candidates/'+row.id));get('message').textContent='';selectView('overview');
        if(compactLayout.matches){get('queue').open=false;get('candidate-title').focus({preventScroll:true});}
        if(current.processing.status==='running'||current.status==='running')scheduleProgress();
      });
      get('candidate-list').append(button);
    }
    markSelected();
    get('selection-note').hidden=!current||rows.some((r:any)=>r.id===current.id);
    if(focused)document.querySelectorAll<HTMLButtonElement>('[data-candidate]').forEach(b=>{if(b.dataset.candidate===focused)b.focus();});
    return rows;
  } catch(error:any) {if(token===listGeneration){get('queue-error').textContent='筛选失败：'+error.message;get('queue-count').textContent='结果未更新';}}
  finally {if(token===listGeneration)get('candidate-list').setAttribute('aria-busy','false');}
}
function updateFilters(){syncUrl();void list();}
get('candidate-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(updateFilters,200);};
for(const id of ['filter-date','filter-origin'])get(id).onchange=updateFilters;
document.querySelectorAll<HTMLButtonElement>('[data-filter]').forEach(b=>b.onclick=()=>{filterState=b.dataset.filter!;updateFilters();});
get('reset-filters').onclick=()=>{clearTimeout(searchTimer);filterState='all';get('candidate-search').value='';get('filter-date').value='all';get('filter-origin').value='all';updateFilters();};
async function action(fn: () => Promise<void>) {
  if (busy) return;lock(true);get('error').textContent = '';
  try {await fn();await list();} catch (error: any) {get('error').textContent = error.message;get('message').textContent = '操作未完成；已有记录可刷新查看。';} finally {lock(false);}
}
function scheduleProgress(){
  clearTimeout(pollTimer);
  pollTimer=setTimeout(async()=>{
    try{
      let id=current?.id;
      if(watchedSubmission){
        const rows=await request('/api/candidates');
        id=rows.find((row:any)=>row.input.submission_id===watchedSubmission)?.id;
      }
      if(id)render(await request('/api/candidates/'+encodeURIComponent(id)));
    }catch{get('message').textContent='暂时无法更新进度；处理记录保留，可刷新查看。';}
    if(busy||(current?.processing.status==='running'||current?.status==='running'))scheduleProgress();
  },1500);
}
get('candidate-form').addEventListener('input',() => importId = crypto.randomUUID());
get('candidate-form').onsubmit = (event: Event) => {
  event.preventDefault();action(async () => {
    get('message').textContent = '正在保存原文；不调用模型，页面自动更新进度。';
    watchedSubmission=importId;scheduleProgress();
    try{
      render(await post('/api/candidates/process',{submission_id:importId,url:get('candidate-url').value,goal:get('goal').value,origin:get('origin').value,source_note:get('source-note').value||'网页导入；作者背景尚未核实。',as_of:get('as-of').value,max_age_days:Number(get('days').value)}));
      get('message').textContent = current.processing.status==='completed'?'原文已保存，未触发 中文价值预览。':'处理未完成；失败阶段和原因如下。';
      get('intake').open = false;selectView('overview');
    }finally{watchedSubmission=null;}
  });
};
get('process').onclick = () => action(async () => {
  get('message').textContent = '正在处理未完成阶段；成功结果会复用。';scheduleProgress();
  render(await post('/api/candidates/'+current.id+'/process',{submission_id:processId}));
  if(current.processing.status!=='running')processId=crypto.randomUUID();
  get('message').textContent=current.processing.status==='completed'?'原文已保存，未触发 中文价值预览。':'失败原因和已有结果保留在下方。';selectView('overview');
});
get('refresh').onclick = () => action(async () => render(await request('/api/candidates/'+current.id)));
get('screen-button').onclick=()=>action(async()=>{
  get('message').textContent='正在调用所选模型做中文价值预览；原文已保存，可以继续阅读。';scheduleProgress();
  render(await post('/api/candidates/'+current.id+'/review',{submission_id:screenId}));
  if(current.status!=='running')screenId=crypto.randomUUID();
  get('message').textContent=current.preview?.latest?.status==='completed'?'中文价值预览已保存，尚未经本人核实。':'初筛未完成；原文仍可阅读，重试需主动点击。';selectView('overview');
});
(async()=>{
  const id=initialParams.get('id');
  if(id){render(await request('/api/candidates/'+encodeURIComponent(id)));get('intake').open=false;}
  const rows=await list();
  // Opening a detail is read-only. It never generates a preview or records a decision.
  if(!id && rows?.length)render(await request('/api/candidates/'+encodeURIComponent(rows[0].id)));
  if(current?.processing.status==='running'||current?.status==='running')scheduleProgress();
})().catch(error=>get('error').textContent=error.message);
