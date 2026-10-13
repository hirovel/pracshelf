// Search execution, provenance and choices belong to SourceSearch. This page renders its output.
const el = (id: string): any => document.getElementById(id);
let current: any = null, busy = false, submission = crypto.randomUUID();
const captureSubmissions = new Map<number, string>();
function node(tag: string, text = '', className = '') {
  const element = document.createElement(tag); element.textContent = text; element.className = className; return element;
}
function link(text: string, href: string) {
  const element = node('a', text) as HTMLAnchorElement; element.href = href; return element;
}
async function request(path: string, body?: unknown) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
  return value;
}
function lock(value: boolean) {
  busy = value; document.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('.inbox-ui button, .inbox-ui input, .inbox-ui textarea, .inbox-ui select').forEach(element => element.disabled = value);
  el('search-items').setAttribute('aria-busy', String(value));
}
async function action(message: string, work: () => Promise<void>) {
  if (busy) return; lock(true); el('search-error').textContent = ''; el('search-message').textContent = message;
  try { await work(); }
  catch (error) { el('search-error').textContent = (error as Error).message; el('search-message').textContent = '操作未确认完成。刷新记录可查看已保存状态，不会自动再次搜索。'; }
  finally { lock(false); }
}
function button(label: string, run: () => Promise<void>) {
  const element = node('button', label) as HTMLButtonElement; element.type = 'button'; element.disabled = busy; element.onclick = run; return element;
}
async function decide(item: any, choice: string) {
  await action(choice === 'save' ? '正在保存原文，成功阶段会复用…' : '正在保存选择…', async () => {
    if (!captureSubmissions.has(item.position)) captureSubmissions.set(item.position, crypto.randomUUID());
    current = await request(`/api/searches/${current.id}/items/${item.position}/decision`, {action:choice, submission_id:captureSubmissions.get(item.position)});
    captureSubmissions.delete(item.position); render();
    el('search-message').textContent = choice === 'save' ? '采集状态已更新，可在“已选择保存”查看原文或失败原因。' : '选择已保存。';
  });
}
function render() {
  el('search-items').replaceChildren(); el('search-trace').hidden = !current;
  if (!current) return;
  const statuses: Record<string,string> = {running:'搜索进行中', succeeded:'搜索已完成', failed:'搜索失败'};
  const state = el('result-state').value;
  const visible = current.items.filter((item: any) => state === 'all' || item.decision === state);
  const charge = current.output?.reported_cost_usd == null ? '费用未报告' : `提供方报告费用 $${current.output.reported_cost_usd}`;
  const configuration = current.execution.model ? ` · ${current.execution.model} / ${current.execution.effort}` : '';
  el('search-summary').textContent = `${statuses[current.status]} · 返回 ${current.items.length} 条 · 当前显示 ${visible.length} 条 · 本记录原始模型任务 ${current.execution.model_calls} 次${configuration} · ${charge}`;
  if (current.error) el('search-items').append(node('p', current.error, 'error'));
  if (current.status === 'running') el('search-items').append(node('p', '正在等待搜索服务；刷新记录只读取进度。', 'subtle'));
  else if (!visible.length && !current.error) el('search-items').append(node('p', current.items.length ? '当前状态下没有结果，可切换状态查看。' : '搜索服务没有返回结果；这不证明不存在相关资料。', 'subtle'));
  for (const item of visible) {
    const card = node('article', '', 'feed-card');
    const date = item.published?.match(/^\d{4}-\d{2}-\d{2}/)?.[0] || item.published || '日期未知';
    card.append(node('p', `${item.author || '作者未提供'} · ${new URL(item.url).hostname} · ${date}`, 'feed-meta'), node('h3', item.title));
    const excerpt = (item.snippet || '').replace(/^(?:Published|Author):[^\n]*\n/gm, '').replace(/^(?:Highlights|Text):\s*/, '').trim();
    card.append(node('p', excerpt.slice(0, 450) + (excerpt.length > 450 ? '…' : '') || '搜索服务未提供摘要。', 'feed-summary'), node('p', '搜索服务摘要 / 摘录 · 非完整原文 · 作者与日期未核实', 'feed-meta'));
    if (excerpt.length > 450) { const more = node('details'); more.append(node('summary', '展开搜索服务摘录'), node('p', excerpt, 'feed-summary')); card.append(more); }
    const capture = item.capture;
    if (capture) card.append(node('p', capture.version_id ? `${item.already_saved ? '资料库已有原文' : '原文已保存'} · v${capture.version_id}` : capture.status === 'running' ? '采集进行中' : capture.error || '尚未完成采集', capture.error ? 'capture-error' : 'feed-status'));
    const actions = node('div', '', 'feed-actions');
    if (item.decision === 'ignored') actions.append(button('恢复', () => decide(item, 'restore')));
    else {
      if (capture?.version_id) actions.append(link('阅读原文 →', '/read/' + capture.pipeline_id));
      else if (capture?.status !== 'running') actions.append(button(capture?.status === 'failed' ? '重试保存全文' : '保存全文', () => decide(item, 'save')));
      actions.append(button('忽略', () => decide(item, 'ignore')));
    }
    const source = link('原始来源 ↗', item.url); source.target = '_blank'; source.rel = 'noopener noreferrer'; actions.append(source);
    if (item.candidate_id) actions.append(link('查看流水线', capture?.pipeline_id ? '/pipeline?run=' + capture.pipeline_id : '/candidates?id=' + item.candidate_id));
    card.append(actions); el('search-items').append(card);
  }
  el('search-record').textContent = JSON.stringify({input:current.input, execution:current.execution, created:current.created, finished:current.finished, elapsed_seconds:current.elapsed_seconds, status:current.status, error:current.error, output:current.output}, null, 2);
  el('search-json').href = '/api/searches/' + current.id;
  history.replaceState(null, '', '/search?id=' + current.id);
}
async function loadHistory(selected?: string) {
  const rows = await request('/api/searches'); el('search-history').replaceChildren();
  if (!rows.length) el('search-history').add(new Option('尚无搜索记录', ''));
  for (const row of rows) el('search-history').add(new Option(`${new Date(row.created).toLocaleString()} · ${row.input.query}`, row.id));
  const id = selected || current?.id || rows[0]?.id;
  if (id) { current = await request('/api/searches/' + encodeURIComponent(id)); el('search-history').value = id; render(); }
}
el('search-form').addEventListener('input', () => submission = crypto.randomUUID());
el('search-form').onsubmit = (event: Event) => {
  event.preventDefault(); void action('正在执行搜索，失败不换提供方…', async () => {
    const requestedSubmission = submission;
    current = await request('/api/searches', {submission_id:submission, provider:el('search-provider').value, query:el('query').value, objective:el('objective').value,refresh:el('search-refresh').checked});
    const reused = current.input.submission_id !== requestedSubmission;
    submission = crypto.randomUUID(); el('result-state').value = 'pending'; await loadHistory(current.id);
    el('search-message').textContent = reused ? (current.status === 'running' ? '复用已有运行中的搜索，本次未启动新任务。' : '已复用当天结果，本次新增模型任务 0。来源时间与原始调用记录保留。') : current.status === 'succeeded' ? '结果已保存。选择保存全文后才进入原文采集。' : current.status === 'running' ? '已有任务仍在运行，本次没有重复搜索。' : '本次失败记录已保存。新提交可重试，失败不会自动重跑。';
  });
};
el('result-state').onchange = render;
el('search-history').onchange = () => action('读取已有记录…', async () => { captureSubmissions.clear(); await loadHistory(el('search-history').value); el('search-message').textContent = '已载入历史结果，没有重新搜索。'; });
el('search-reload').onclick = () => action('刷新已有记录…', async () => { await loadHistory(); el('search-message').textContent = '记录已刷新，没有重新搜索。'; });
loadHistory(new URLSearchParams(location.search).get('id') || undefined).catch(error => el('search-error').textContent = error.message);
request('/api/model-configuration').then(configuration=>{el('search-policy').textContent=`Codex 搜索：${configuration.codex.model} / ${configuration.codex.effort}。当天相同查询与配置优先复用；打开页面不搜索。`}).catch(()=>{});
