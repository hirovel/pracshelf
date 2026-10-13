// FeedInbox owns persistence and capture state. Feed HTML is displayed as plain text.
const el = (id: string): any => document.getElementById(id);
const params = new URLSearchParams(location.search);
for (const [key,id] of [['state','inbox-state'],['feed','inbox-feed'],['freshness','inbox-date'],['q','inbox-query']]) {
  if(key !== 'feed' && params.has(key))el(id).value=params.get(key);
}
let chosenFeed=params.get('feed')||'', busy=false, generation=0;
const submissions=new Map<number,string>();
function node(tag: string, text='', className='') {const n=document.createElement(tag);n.textContent=text;n.className=className;return n;}
function link(label: string, href: string) {const n=node('a',label) as HTMLAnchorElement;n.href=href;return n;}
async function request(path: string, payload?: unknown) {
  const r=await fetch(path,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const value=await r.json();if(!r.ok)throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));return value;
}
function action(label: string, run:()=>Promise<void>, primary=false) {
  const b=node('button',label,primary?'primary':'') as HTMLButtonElement;b.disabled=busy;b.onclick=run;return b;
}
function lock(value: boolean) {busy=value;document.querySelectorAll<HTMLButtonElement>('.inbox-ui button').forEach(b=>b.disabled=value);}
async function execute(label: string, work:()=>Promise<string>) {
  if(busy)return;lock(true);el('inbox-error').textContent='';el('inbox-message').textContent=label;
  try{el('inbox-message').textContent=await work();await load();}
  catch(error){el('inbox-error').textContent=(error as Error).message;el('inbox-message').textContent='操作未确认完成，可以刷新列表查看已保存状态。';}
  finally{lock(false);}
}
async function decide(item: any, actionName: string) {
  await execute(actionName==='save'?'正在保存全文；成功阶段会复用…':'正在保存选择…',async()=>{
    if(!submissions.has(item.id))submissions.set(item.id,crypto.randomUUID());
    const out=await request(`/api/inbox/${item.id}/decision`,{action:actionName,submission_id:submissions.get(item.id)});
    submissions.delete(item.id);
    if(actionName==='save' && out.capture?.version_id){el('inbox-open-saved').href='/read/'+out.capture.pipeline_id;el('inbox-open-saved').hidden=false;}
    return actionName!=='save'?(actionName==='ignore'?'已忽略，可在“已忽略”恢复。':'已恢复到原来的选择状态。'):out.capture?.version_id?'全文已保存。在“已选择保存”打开阅读页。':'全文尚未保存，请在“已选择保存”查看失败原因并重试。';
  });
}
function card(item: any) {
  const c=node('article','','feed-card');
  const dateLabel=item.date_kind==='updated'?'更新日期':'发布日期';
  const date=item.date_iso||'日期未知';
  c.append(node('div',`${item.author||'作者未提供'} · ${new URL(item.url).hostname} · ${dateLabel} ${date}`,'feed-meta'));
  c.append(node('h3',item.title));
  const sourceSummary=node('div');
  sourceSummary.append(node('p',item.summary||'来源未提供简介，请打开原站判断。','feed-summary'));
  sourceSummary.append(node('p',item.summary_truncated?'来源简介 · 展示前 600 字符，完整来源记录保留':'来源简介 · 非 AI 摘要','feed-meta'));
  c.append(sourceSummary);
  if(item.date_check.status==='future_date')c.append(node('p','来源日期在未来，需核查。','capture-error'));
  if(item.date_check.status==='outside_window')c.append(node('p','来源日期已超过 90 天；适用性需要结合版本判断。','feed-meta'));
  const status=item.capture?.version_id?'全文已保存':item.capture?.status==='failed'?'采集失败':item.capture?.status==='running'?'正在采集':item.decision==='ignored'?'已忽略':item.decision==='selected'?'已选择，等待采集':'待选择';
  c.append(node('p',status,'feed-status'));
  if(item.capture?.error)c.append(node('p',item.capture.error,'capture-error'));
  const controls=node('div','','feed-actions');
  if(item.decision==='pending'){controls.append(action('保存全文',()=>decide(item,'save'),true),action('忽略',()=>decide(item,'ignore')));}
  else if(item.decision==='ignored')controls.append(action(item.candidate_id?'恢复到已选':'恢复到待选择',()=>decide(item,'restore')));
  else if(item.capture?.version_id)controls.append(link('阅读原文 →','/read/'+item.capture.pipeline_id),action('忽略',()=>decide(item,'ignore')));
  else if(item.capture?.status!=='running')controls.append(action(item.capture?.status==='failed'?'重试保存全文':'保存全文',()=>decide(item,'save'),true));
  const source=link('原站 ↗',item.url);source.target='_blank';source.rel='noopener noreferrer';controls.append(source);
  if(item.candidate_id)controls.append(link('查看流水线',item.capture?.pipeline_id?'/pipeline?run='+item.capture.pipeline_id:'/candidates?id='+item.candidate_id));
  c.append(controls);
  const details=node('details');details.append(node('summary','来源与操作记录'));
  details.append(node('pre',JSON.stringify({feed:item.feed_url,date_raw:item.date_raw,date_kind:item.date_kind,feed_version:item.version_id,first_seen:item.created,last_seen:item.seen,decisions:item.decisions,capture:item.capture},null,2)));
  c.append(details);return c;
}
async function load() {
  const current=++generation;el('inbox-items').setAttribute('aria-busy','true');
  const query=new URLSearchParams({state:el('inbox-state').value,q:el('inbox-query').value,freshness:el('inbox-date').value,feed:chosenFeed});
  try {
    const data=await request('/api/inbox?'+query);if(current!==generation)return;
    history.replaceState(null,'','/inbox?'+query);
    el('inbox-items').replaceChildren(...data.items.map(card));el('inbox-count').textContent=`${data.items.length} 篇`;el('inbox-empty').hidden=!!data.items.length;
    const select=el('inbox-feed');select.replaceChildren(new Option('全部订阅',''));
    for(const feed of data.feeds)select.add(new Option(new URL(feed.url).hostname,feed.url));select.value=chosenFeed;
    const feeds=el('feed-runs');feeds.replaceChildren();
    for(const feed of data.feeds){
      const block=node('section','','feed-run');block.append(node('strong',feed.url));
      const last=feed.attempts[0];block.append(node('p',last?`${last.started} · ${last.result?.status||'读取中'} · ${last.result?.saved??0} 条`:'尚无读取记录'));
      if(last?.result?.error)block.append(node('p',last.result.error));
      block.append(action('重新读取此订阅',()=>refreshFeed(feed.url)));
      const d=node('details');d.append(node('summary','读取输入与结果'),node('pre',JSON.stringify(feed.attempts,null,2)));block.append(d);feeds.append(block);
    }
  } finally {if(current===generation)el('inbox-items').setAttribute('aria-busy','false');}
}
async function refreshFeed(url: string) {
  await execute('正在读取 RSS；不抓全文，不调用模型…',async()=>{
    const result=await request('/api/inbox/refresh',{url});
    return `${result.status==='ok'?'读取完成':'读取未全部成功'}：${result.saved} 条。${result.error||''}`;
  });
}
el('feed-form').onsubmit=(event: Event)=>{event.preventDefault();refreshFeed(el('feed-url').value);};
const reload=()=>load().catch(e=>el('inbox-error').textContent=e.message);
el('inbox-reload').onclick=reload;
for(const id of ['inbox-state','inbox-date','inbox-feed'])el(id).onchange=()=>{chosenFeed=el('inbox-feed').value;reload();};
let timer: ReturnType<typeof setTimeout>;
el('inbox-query').oninput=()=>{clearTimeout(timer);timer=setTimeout(reload,200);};
reload();
