/* ELT state and lineage come from WorkflowView. This page only displays them and
   submits an explicit action through the existing capture/insights Interface. */
import {currentRun} from './application-navigation.js';
type Operation = {id:string;phase:string;title:string;status:string;label:string;summary:string;executor:string;required:boolean;input:any;output:any;detail:any;action:{kind:string;stage?:string;label:string}|null};
type FlowNode = {id:string;title:string;status:string;label:string;summary:string;operation_id:string;operation_ids:string[];optional:boolean};
type Workflow = {run_id:string;title:string;author:string|null;published:string|null;url:string;version_id:number|null;provenance:any;steps:any[];elt:any;insights:any;pipeline_view:{nodes:FlowNode[]}};
const get = (id:string) => document.getElementById(id)!;
const node = (tag:string,text='',className='') => {const element=document.createElement(tag);element.textContent=text;element.className=className;return element;};
const params = new URLSearchParams(location.search);
const legacySteps:Record<string,string> = {source:'clean',screen:'core',translate:'translate',review:'core'};
const origins:Record<string,string> = {user_link:'手动链接',agent_supplied:'外部 Agent',rss:'RSS',web_search:'网页搜索',demo:'演示资料'};
let current:Workflow|null=null, selected=params.get('operation')||legacySteps[params.get('step')||'']||'';
let inspectorView=['output','input','records'].includes(params.get('view')||'')?params.get('view')!:'output';
let generation=0, busy=false, poll:number|undefined;
let pendingAction:{key:string;submission:string}|null=null;
let importSubmission=crypto.randomUUID();
async function request(path:string, body?:unknown) {
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));
  return value;
}
function error(message='') {get('flow-error').textContent=message;get('flow-error').hidden=!message;}
function disclosure(label:string, child:HTMLElement) {const box=node('details','','flow-disclosure');box.append(node('summary',label),child);return box;}
function renderRecord(value:any) {return (globalThis as any).savedRecordView.render(value);}
// Only completed, immutable attempts are memoized. Failed reads can be retried.
const attemptDetails=new Map<string,Promise<any>>();
function lazyDisclosure(label:string,url:string,field:string,cache:boolean) {
  const body=node('div'), box=disclosure(label+' · 按需读取',body);
  let loaded=false, loading=false;
  async function load() {
    if(loaded||loading)return;
    loading=true;body.replaceChildren(node('p','正在读取已保存记录…','flow-muted'));
    try {
      let pending=cache?attemptDetails.get(url):undefined;
      if(!pending){pending=request(url);if(cache)attemptDetails.set(url,pending);}
      const value=await pending;body.replaceChildren(renderRecord(value[field]));loaded=true;
    } catch(cause) {
      attemptDetails.delete(url);
      const retry=node('button','重试读取') as HTMLButtonElement;retry.type='button';retry.onclick=load;
      body.replaceChildren(node('p',(cause as Error).message,'flow-error-text'),retry);
    } finally {loading=false;}
  }
  box.addEventListener('toggle',()=>{if((box as HTMLDetailsElement).open)load();});return box;
}
function facts(items:[string,any][]) {const list=node('dl','','flow-facts');for(const [label,value] of items)list.append(node('dt',label),node('dd',value==null?'未记录':String(value)));return list;}
function link(label:string,href:string,className='') {const anchor=node('a',label,className) as HTMLAnchorElement;anchor.href=href;anchor.target='_blank';anchor.rel='noopener noreferrer';return anchor;}
function badge(value:{label:string;status:string}) {return node('span',value.label,'flow-state flow-state-'+value.status);}
function attemptStatus(status:string) {return ({completed:'已完成',failed:'失败',running:'执行中',queued:'等待执行',cancelled:'已取消',interrupted:'已中断'} as Record<string,string>)[status]||status;}
function operations():Operation[] {return current?.elt.operations||[];}
function selectedOperation() {return operations().find(operation=>operation.id===selected)!;}
function synchronizeURL() {if(current){history.replaceState(null,'','/pipeline?'+new URLSearchParams({run:current.run_id,operation:selected,...(inspectorView==='output'?{}:{view:inspectorView})}));document.dispatchEvent(new CustomEvent('workspace:document',{detail:{runId:current.run_id,title:current.title,readable:!!current.elt.result.url,operation:selected}}));}}
function usageFor(attempt:any) {const value=attempt?.response?.usage;return value?.total||value;}
function tokenCount(attempt:any) {if(attempt?.observation)return attempt.observation.tokens.total??'未记录';const usage=usageFor(attempt);return usage?(usage.totalTokens??usage.total_tokens??'未报告'):'未报告';}
function seconds(value:number|null) {return value==null?'未记录':value+' 秒';}
function screenTrace() {return current?.steps.find(step=>step.id==='screen')?.trace;}
function setBusy(value:boolean) {
  busy=value;
  (get('flow-material') as HTMLSelectElement).disabled=value;
  (get('model-save') as HTMLButtonElement).disabled=value;
  (get('flow-import-submit') as HTMLButtonElement).disabled=value;
  const action=document.getElementById('flow-run-action') as HTMLButtonElement|null;
  if(action)action.disabled=value;
}
async function loadModel() {
  const configuration=await request('/api/model-configuration');
  (get('model-provider') as HTMLSelectElement).value=configuration.provider;
  get('model-label').textContent=configuration.provider==='codex'?'Codex · 订阅':'DeepSeek · API';
  get('model-status').textContent=configuration.model+' · '+configuration.effort+' · '+configuration.reason;
}
get('model-save').onclick=async()=>{if(busy)return;setBusy(true);try{await request('/api/model-configuration',{provider:(get('model-provider') as HTMLSelectElement).value});await loadModel();await refresh();}catch(cause){get('model-status').textContent=(cause as Error).message;}finally{setBusy(false);}};
function display(value:Workflow) {
  current=value;
  if(!operations().some(operation=>operation.id===selected))selected=value.elt.result.core?'core':operations().find(operation=>operation.required&&operation.status!=='completed')?.id||'clean';
  get('flow-title').textContent=value.title;
  get('flow-meta').textContent=[value.author||'作者未知',value.published||'日期未知',value.version_id?'正文 v'+value.version_id:'正文尚未整理'].join(' · ');
  (get('flow-source') as HTMLAnchorElement).href=value.url;
  get('flow-source-note').textContent=(origins[value.provenance?.origin]||'发现渠道未记录')+' · '+(value.provenance?.source_note||'没有来源说明。');
  get('flow-result-symbol').textContent=value.elt.status==='completed'?'✓':value.elt.status==='failed'?'!':'○';
  get('flow-result-symbol').dataset.state=value.elt.status;
  get('flow-result-summary').textContent=value.elt.summary;
  const reader=get('flow-reader') as HTMLAnchorElement;reader.hidden=!value.elt.result.url;reader.href=value.elt.result.url||'/';
  const publication=get('flow-publication') as HTMLAnchorElement;publication.hidden=!value.elt.result.url;publication.href='/publication?run='+value.run_id;
  get('flow-capture-note').textContent=value.elt.capture_group_note;
  get('flow-document').hidden=false;get('flow-empty').hidden=true;
  (get('flow-material') as HTMLSelectElement).value=value.run_id;
  renderSteps();renderDetail();showInspector(inspectorView);synchronizeURL();schedule();
}
function renderSteps() {
  const container=get('flow-steps');container.replaceChildren();
  for(const [index,step]of current!.pipeline_view.nodes.entries()) {
    const button=node('button','','flow-node') as HTMLButtonElement;button.type='button';button.id='flow-stage-'+step.id;button.dataset.state=step.status;
    button.setAttribute('aria-pressed',String(step.operation_ids.includes(selected)));button.setAttribute('aria-controls','flow-inspector');
    const title=node('span','','flow-node-title');title.append(node('span',String(index+1).padStart(2,'0'),'flow-node-number'),node('strong',step.title),badge(step));
    button.append(title,node('span',step.summary,'flow-node-summary'),node('span',step.optional?'可选模型任务':'保存原文'===step.title?'采集与正文整理 · 无模型':'一次全文判断与提取','flow-node-executor'));
    button.onclick=()=>{selected=step.operation_id;renderSteps();renderDetail();showInspector(inspectorView);synchronizeURL();get('flow-stage-'+step.id).focus();if(matchMedia('(max-width:720px)').matches)get('flow-inspector').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});};container.append(button);
  }
  const technical=get('flow-technical-steps');technical.replaceChildren();
  for(const operation of operations().slice(0,3)){
    const button=node('button',operation.phase+' · '+operation.title+' · '+operation.label,'flow-technical-node') as HTMLButtonElement;button.type='button';button.setAttribute('aria-pressed',String(operation.id===selected));
    button.onclick=()=>{selected=operation.id;renderSteps();renderDetail();showInspector(inspectorView);synchronizeURL();};technical.append(button);
  }
}
function renderCore(body:HTMLElement, operation:Operation) {
  const output=current!.steps.find(step=>step.id==='screen')?.output;
  const trace=screenTrace();
  if(!operation.output||output?.kind!=='article_insights') {
    body.append(node('p',operation.summary,'flow-muted'));
    if(trace?.selected_attempt?.error)body.append(node('p',trace.selected_attempt.error,'flow-error-text'));
    return;
  }
  const judgment=output.judgment;
  body.append(node('p',judgment.reason,'flow-reason'),node('p','引文定位：'+(output.quote_alignment==='passed'?'通过':'未通过')+'；语义与当前适用性未独立验证。','flow-muted'));
  if(output.core_available) {
    const core=node('section','','flow-core');core.append(node('h4','文章核心 · AI 提取'),node('p',judgment.core));body.append(core);
    const types:Record<string,string>={author_judgment:'作者判断',factual_statement:'原文事实陈述，未外部查证',example:'示例'};
    for(const [index,point] of judgment.points.entries()) {
      const item=node('section','','flow-point');item.append(node('span',String(index+1).padStart(2,'0'),'flow-point-number'));
      const content=node('div');content.append(node('small',types[point.claim_type]),node('p',point.text));
      const quotes=node('div','','flow-quotes');
      for(const evidence of output.evidence_checks.filter((check:any)=>check.target==='point-'+(index+1))){quotes.append(node('blockquote',evidence.source_quote||evidence.quote),node('small','原文字符位置 '+evidence.start+'–'+evidence.end));}
      content.append(disclosure('原文依据',quotes));item.append(content);body.append(item);
    }
  } else body.append(node('p','未形成可用核心。判断原因、机器响应和原文继续保留。','flow-muted'));
  const limits=node('div');
  for(const [title,items] of [['原文条件',judgment.conditions],['版本依赖',judgment.time_dependencies],['未核实',judgment.unknowns]] as [string,string[]][]){limits.append(node('h4',title));const list=node('ul');for(const item of items.length?items:['没有记录条目'])list.append(node('li',item));limits.append(list);}
  body.append(disclosure('条件、限制与未知项',limits));
}
function renderChinese(body:HTMLElement, operation:Operation) {
  const plan=operation.detail?.plan, budget=operation.input?.budget;
  body.append(node('p',operation.summary,'flow-reason'));
  if(budget){body.append(facts([['固定原文','v'+budget.version_id+' · '+budget.source_characters.toLocaleString()+' 字符'],['执行配置',operation.input.provider+' · '+operation.input.model+' · '+operation.input.effort],['正文批数',budget.total_segments],['已保存并复用',budget.reused_segments+' 批'],['代码 / 空白直接保留',budget.preserved_segments+' 批'],['待执行应用调用',budget.remaining_model_calls+' 次'],['单次任务调用上限',budget.max_model_calls+' 次']]));
    body.append(node('p',budget.output_token_limit_enforced?'每批输出上限 '+budget.max_output_tokens_per_call+' tokens。':'Codex SDK 没有硬性输出 token 上限；按待执行批数控制范围，实际用量见记录。','flow-muted'));}
  if(plan){body.append(node('p',plan.reason,'flow-muted'));if(plan.latest?.error)body.append(node('p',plan.latest.error,'flow-error-text'));}
  if(operation.status==='running')body.append(node('p','关闭网页后后端继续处理；某批失败就停止，不自动重试。','flow-muted'));
  const html=operation.output?.translation_html;
  if(html){const reading=link('中英对照阅读 →','/read/'+current!.run_id+'?mode=parallel','flow-action');reading.removeAttribute('target');body.append(reading,node('p','机器译文与原文独立保存，结构检查通过不代表语义已核对。','flow-muted'));
    const article=node('article','','flow-original');article.innerHTML=html;body.append(disclosure('中文副本预览',article));}
}
function renderDetail() {
  const operation=selectedOperation(), panel=get('flow-output');panel.replaceChildren();
  const group=current!.pipeline_view.nodes.find(step=>step.operation_ids.includes(operation.id))!;
  const heading=node('header','','flow-detail-heading'), title=node('div');title.append(node('p',operation.executor+' · '+operation.title,'flow-eyebrow'),node('h3',group.title));heading.append(title,badge(operation));
  if(operation.action){const button=node('button',operation.action.label,'flow-action') as HTMLButtonElement;button.type='button';button.id='flow-run-action';button.disabled=busy;button.onclick=()=>execute(operation);heading.append(button);}panel.append(heading);
  const input=operation.id==='fetch'?current!.url:operation.id==='load'?'网页响应 #'+(operation.input.request_id??'未保存'):operation.id==='clean'?'已保存快照 #'+(operation.input.request_id??'未保存'):'正文 '+(operation.input.version_id?'v'+operation.input.version_id:'未生成');
  const artifacts:Record<string,string>={fetch:'来源响应',load:'原始快照与采集记录',clean:'整理正文与来源版本',core:'阅读建议、核心与引文',translate:'中文副本'};
  const envelope=node('div','','flow-envelope');envelope.append(node('span','输入：'+input),node('span','产物：'+artifacts[operation.id]));panel.append(envelope);
  const body=node('div','','flow-copy');panel.append(body);
  if(operation.id==='fetch') {
    body.append(facts([['HTTP 状态',operation.output?.status],['最终来源',operation.output?.final_url],['响应类型',operation.output?.content_type],['采集尝试',operation.detail.attempts]]));
    if(operation.output?.error)body.append(node('p',operation.output.error,'flow-error-text'));
  } else if(operation.id==='load') {
    const output=operation.output;
    if(output){body.append(facts([['保存位置',output.snapshot],['响应大小',(output.bytes/1024).toFixed(1)+' KiB'],['采集时间',output.captured_at],['SHA-256 快照标识',output.sha256],['完整响应',output.complete?'是':'否；错误或截断响应']]));body.append(link('查看原始响应 ↗',output.url));}
    else body.append(node('p',operation.summary,'flow-muted'));
  } else if(operation.id==='clean') {
    const output=operation.output;
    if(output){body.append(facts([['正文版本','v'+output.version_id],['正文长度',output.characters.toLocaleString()+' 字符'],['正文版本标识',output.content_hash]]));
      const article=node('article','','flow-original');article.innerHTML=current!.steps.find(step=>step.id==='source')?.output?.source_html||'';
      body.append(disclosure('整理后的原文',article),disclosure('整理检查与提示',renderRecord(output.extraction)),link('查看正文文本 ↗',output.url));}
    else body.append(node('p',operation.summary,'flow-muted'));
  } else if(operation.id==='core')renderCore(body,operation);
  else renderChinese(body,operation);
  renderInput(operation);renderTrace(operation);
}
function renderInput(operation:Operation) {
  const body=get('flow-input');body.replaceChildren(node('h3',operation.input.request_frozen===false?'待提交输入':'本阶段输入'),renderRecord(operation.input));
  if(['fetch','load','clean'].includes(operation.id))body.append(node('p','采集、保存与整理均不执行模型。完整原文保存在本机版本中。','flow-muted'));
  if(operation.id==='core'){
    const attempt=screenTrace()?.selected_attempt;
    if(attempt?.observation?.detail_url)body.append(lazyDisclosure('实际固定请求与全文',attempt.observation.detail_url,'input',!['queued','running'].includes(attempt.status)));
    body.append(node('p','已有结果保留当时的模型与档位；查看输入不会提交模型任务。','flow-muted'));
  }
}
function showInspector(view:string) {
  inspectorView=view;
  for(const name of ['output','input','records']){
    get('flow-'+name).hidden=name!==view;
    const tab=get('flow-tab-'+name);tab.setAttribute('aria-selected',String(name===view));tab.tabIndex=name===view?0:-1;
  }
  (get('flow-trace') as HTMLDetailsElement).open=view==='records';
}
const inspectorTabs=Array.from(document.querySelectorAll<HTMLButtonElement>('[data-flow-view]'));
for(const [index,tab]of inspectorTabs.entries()){
  tab.onclick=()=>{showInspector(tab.dataset.flowView!);synchronizeURL();};
  tab.onkeydown=event=>{const target=event.key==='ArrowRight'?(index+1)%3:event.key==='ArrowLeft'?(index+2)%3:event.key==='Home'?0:event.key==='End'?2:null;if(target!==null){event.preventDefault();inspectorTabs[target].click();inspectorTabs[target].focus();}};
}
function renderTrace(operation:Operation) {
  const container=get('flow-record');container.replaceChildren();
  if(operation.id==='core') {
    const read=current!.insights.read;
    if(read){container.append(node('p',(read.saved_result_id?'本次读取：复用已保存结果 #'+read.saved_result_id:'本次读取：只查询状态')+'；新模型任务 '+read.model_tasks_started+'。','flow-read-summary'));
      if(read.saved_result_id&&read.configuration_matches===false)container.append(node('p','新任务配置与历史记录不同；读取旧结果保留当时的配置。','flow-muted'));}
  }
  if(operation.id==='translate'){
    const read=operation.detail?.plan?.read;
    container.append(node('p',(read?.saved_result_id?'本次读取：复用中文副本 #'+read.saved_result_id:'本次读取：只查询中文处理状态')+'；新模型任务 0。','flow-read-summary'));
    const native=current!.steps.find(step=>step.id==='translate')?.trace?.native;
    const history=native?.attempts||[], rows=node('div','','flow-attempts');
    for(const attempt of [...history].reverse())rows.append(attemptRow(attempt,'批次 '+(attempt.segment+1)+' · '));
    if(history.length)container.append(disclosure('译文调用历史 · '+history.length+' 次应用尝试',rows));
    const jobs=operation.detail?.plan?.jobs;if(jobs?.length)container.append(disclosure('启动、重试与固定配置',renderRecord(jobs)));
  }
  container.append(disclosure(operation.input.request_frozen===false?'待提交输入':'输入与配置',renderRecord(operation.input)),disclosure('产物与校验结果',renderRecord(operation.output)));
  const selectedAttempt=screenTrace()?.selected_attempt;
  get('flow-usage').textContent=operation.id==='core'&&selectedAttempt?'#'+selectedAttempt.id+' · '+tokenCount(selectedAttempt)+' tokens':'';
  get('flow-trace-note').textContent='以下用量与耗时属于历史尝试。本地结果复用与供应商的输入缓存分别记录；查看不会执行模型。';
  if(operation.id==='core') {
    const history=screenTrace()?.history||[];
    const attempts=node('div','','flow-attempts');
    for(const attempt of [...history].reverse()) {
      attempts.append(attemptRow(attempt));
    }
    if(history.length)container.append(disclosure('实际调用历史 · '+history.length+' 次应用尝试',attempts));
  } else if(operation.detail?.selected_attempt)container.append(disclosure('执行记录',renderRecord(operation.detail.selected_attempt)));
  (get('flow-json') as HTMLAnchorElement).href='/api/workflows/'+current!.run_id+'?detail=full';
}
function attemptRow(attempt:any,prefix='') {
      const response=attempt.response||attempt.output||{}, observation=attempt.observation, row=node('details','','flow-attempt');
      const duration=observation?seconds(observation.timing.task_seconds):'耗时未记录';
      row.append(node('summary',prefix+'#'+attempt.id+' · '+attemptStatus(attempt.status)+' · '+duration+' · '+tokenCount(attempt)+' tokens'));
      if(observation) {
        const t=observation.tokens, time=observation.timing;
        row.append(facts([['执行框架',(globalThis as any).savedRecordView.framework(observation)],['历史配置',observation.provider+' · '+(observation.configured_model||'未记录')+' · '+(observation.effort||'未记录')],['实际执行模型',observation.execution_model],['任务 / 终态','#'+observation.application_attempt_id+' / '+attemptStatus(observation.terminal_status)],['底层模型请求数',observation.provider_request_count]]));
        const visible=observation.visible_input;
        if(visible){row.append(node('h4','可见请求 · 字符，非 tokens'),facts([['system 提示词',visible.system_characters],['user 消息',visible.user_characters],['消息字符合计',visible.message_characters],['SDK / runtime',(visible.sdk_version||'未记录')+' / '+(visible.runtime_version||'未记录')],['完整模型上下文','未观测']]));
          row.append(node('p',visible.scope,'flow-muted'),disclosure('固定消息标识',renderRecord({messages_sha256:visible.messages_sha256,instruction_sources:visible.instruction_sources})));}
        row.append(node('h4','历史 tokens'),facts([['输入',t.input],['缓存读取',t.cached_input],['缓存写入',t.cache_write_input],['输出',t.output],['其中推理输出',t.reasoning_output],['总量',t.total],['输入缓存比例',observation.cache_read_ratio==null?null:(observation.cache_read_ratio*100).toFixed(1)+'%']]));
        row.append(node('p',observation.usage_basis+'。推理输出已包含在输出中，不另加到总量。缓存比例不表示本地结果复用率。','flow-muted'));
        row.append(node('h4','历史耗时'),facts([['任务总耗时',seconds(time.task_seconds)],['worker 内准备',seconds(time.preparation_seconds)],['turn 执行',seconds(time.turn_seconds)]]));
        row.append(node('p',Object.values(observation.timing_basis).join('；')+'。','flow-muted'));
        row.append(disclosure('会话、事件与计量依据',renderRecord({thread_id:observation.thread_id,turn_id:observation.turn_id,event_count:observation.event_count,last_event:observation.last_event,reported_thread_total:observation.reported_thread_total,started:attempt.started,finished:attempt.finished})));
        const cache=!['queued','running'].includes(attempt.status);
        row.append(lazyDisclosure('发送前固定输入与提示词',observation.detail_url,'input',cache),lazyDisclosure('提供方完整响应',observation.detail_url,'response',cache));
        if(observation.log_url){const download=link('下载 SDK 事件与原始响应',observation.log_url);download.removeAttribute('target');download.setAttribute('download','model-record-'+attempt.id+'.json');row.append(disclosure('调试数据',download));}
      } else {
        row.append(facts([['提供方',response.provider||attempt.input.execution?.adapter],['配置模型',response.configured_model||attempt.input.request.model],['终态',response.terminal_status||attempt.status]]),disclosure('发送前固定输入与提示词',renderRecord(attempt.input)),disclosure('提供方响应',renderRecord(response)));
      }
      if(attempt.error)row.append(node('p',attempt.error,'flow-error-text'));
      return row;
}
function schedule() {
  clearTimeout(poll);
  if(busy||operations().some(operation=>operation.status==='running'))poll=window.setTimeout(refresh,2500);
}
async function refresh() {
  if(!current)return;
  const run=current.run_id, sequence=generation;
  try{const value=await request('/api/workflows/'+run+'?detail=summary');if(sequence===generation&&current?.run_id===run)display(value);}
  catch(cause){if(sequence===generation)error((cause as Error).message);schedule();}
}
async function execute(operation:Operation) {
  if(busy||!current||!operation.action)return;
  const run=current.run_id, key=run+':'+operation.id;
  if(pendingAction?.key!==key)pendingAction={key,submission:crypto.randomUUID()};
  setBusy(true);error();schedule();
  try {
    if(operation.action.kind==='insights')await request('/api/runs/'+run+'/insights',{submission_id:pendingAction.submission});
    else if(operation.action.kind==='translation')await request('/api/runs/'+run+'/translation/start',{submission_id:pendingAction.submission});
    else if(operation.action.kind==='stage')await request('/api/runs/'+run+'/stages/'+operation.action.stage,{});
    pendingAction=null;await refresh();
  }catch(cause){error((cause as Error).message);await refresh();}
  finally{setBusy(false);schedule();}
}
async function select(run:string) {
  clearTimeout(poll);const sequence=++generation;error();get('flow-loading').hidden=false;
  try{const value=await request('/api/workflows/'+encodeURIComponent(run)+'?detail=summary');if(sequence===generation)display(value);}
  catch(cause){if(sequence===generation)error((cause as Error).message);}
  finally{if(sequence===generation)get('flow-loading').hidden=true;}
}
async function loadList(run?:string) {
  const rows=await request('/api/workflows');const selectBox=get('flow-material') as HTMLSelectElement;
  selectBox.replaceChildren(...rows.map((row:any)=>new Option(row.title,row.run_id)));
  if(!rows.length){get('flow-document').hidden=true;get('flow-empty').hidden=false;get('flow-loading').hidden=true;return;}
  const remembered=rows.some((row:any)=>row.run_id===currentRun())?currentRun():null;
  const chosen=run||current?.run_id||params.get('run')||remembered||rows[0].run_id;await select(chosen);
}
get('flow-material').onchange=()=>{selected='';select((get('flow-material') as HTMLSelectElement).value);};
get('flow-refresh').onclick=()=>{if(!busy)loadList();};
get('flow-open-import').onclick=()=>{get('flow-open-import').closest('details')?.removeAttribute('open');get('flow-import').hidden=false;(get('flow-import') as HTMLDetailsElement).open=true;get('flow-import-url').focus();};
get('flow-import').addEventListener('toggle',()=>{if(!(get('flow-import') as HTMLDetailsElement).open)get('flow-import').hidden=true;});
get('flow-import-form').addEventListener('input',()=>importSubmission=crypto.randomUUID());
get('flow-import-form').onsubmit=async event=>{
  event.preventDefault();if(busy)return;setBusy(true);error();
  try{
    const response=await request('/api/candidates/process',{submission_id:importSubmission,url:(get('flow-import-url') as HTMLInputElement).value,goal:(get('flow-import-goal') as HTMLTextAreaElement).value,
      origin:'user_link',source_note:(get('flow-import-note') as HTMLInputElement).value||'在工作台手动添加链接。',as_of:new Intl.DateTimeFormat('sv-SE').format(new Date()),max_age_days:30});
    importSubmission=crypto.randomUUID();selected=response.processing.status==='completed'?'core':response.processing.stage==='extract'?'clean':'fetch';
    (get('flow-import') as HTMLDetailsElement).open=false;await loadList(response.pipeline_id);
  }catch(cause){error((cause as Error).message);}
  finally{setBusy(false);}
};
addEventListener('pagehide',()=>clearTimeout(poll));
Promise.all([loadModel(),loadList()]).catch(cause=>{error((cause as Error).message);get('flow-loading').hidden=true;});
