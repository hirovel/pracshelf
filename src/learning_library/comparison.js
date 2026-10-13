/* Comparison results stay separate from search and capture execution. */
(() => {
  const box = document.getElementById('comparison');
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const link = (text, href) => {
    const node = el('a', text); node.href = href;
    node.target = '_blank'; node.rel = 'noopener'; return node;
  };
  const verdicts = {shortlist:'阅读候选', reject:'本轮排除', hold:'暂缓', support:'配套材料'};
  const depths = {body_read:'正文已读', skim:'仅初筛', unavailable:'全文未取得'};
  const feedbackLabels = {useful:'值得读', not_useful:'不适合我', unread:'待读'};

  function sourceCard(s, report, compact = false) {
    const card = el('article', undefined, 'panel');
    card.style.marginTop = '12px';
    card.append(el('h3', s.title.replace(/¶$/, '')), el('span', verdicts[s.verdict] + ' · ' + depths[s.depth], 'badge ' + (s.verdict === 'shortlist' ? 'succeeded' : 'needs_review')));
    card.append(el('p', s.reason), el('p', s.caveat, 'muted'));
    const details = el('details');
    details.append(el('summary', '日期、原文依据与适用限制'));
    details.append(el('p', '核实日期：' + (s.checked_date || '未知') + ' · ' + s.date_basis, 'muted'));
    for (const quote of s.quotes) details.append(el('blockquote', quote));
    details.append(el('p', 'AI 阅读判断；引文匹配已由程序检查，语义审核由当前 Codex 完成，效果未独立复现。', 'muted'));
    card.append(details);
    const controls = el('div', undefined, 'controls');
    controls.append(link('来源网站', s.url), link('原始网页快照', '/api/runs/' + s.pipeline_id + '/artifacts/raw'));
    if (s.version_id) {
      controls.append(link('保存的正文 v' + s.version_id, '/api/runs/' + s.pipeline_id + '/artifacts/text'));
      const read = el('button', '在此展开正文');
      const reader = el('pre'); reader.hidden = true;
      read.onclick = async () => {
        try {
          if (!reader.textContent) {
            const response = await fetch('/api/runs/' + s.pipeline_id + '/artifacts/text');
            if (!response.ok) throw new Error('读取失败');
            reader.textContent = await response.text();
          }
          reader.hidden = !reader.hidden;
          read.textContent = reader.hidden ? '在此展开正文' : '收起正文';
        } catch (error) { reader.hidden = false; reader.textContent = error.message; }
      };
      controls.append(read); card.append(controls, reader);
    } else card.append(controls);
    if (!compact || s.verdict === 'shortlist') {
      const history = report.human_feedback.filter(f => f.source_id === s.id);
      const last = history.at(-1)?.output;
      const status = el('p', last ? '你的判断：' + feedbackLabels[last.verdict] : '你的判断：尚未填写（不影响查看其他结果）', 'muted');
      const note = el('textarea'); note.placeholder = '自己写一句：这篇对我有什么用？';
      note.value = last?.note || ''; note.maxLength = 2000;
      note.style.cssText = 'width:100%;min-height:65px;font:inherit;padding:10px;box-sizing:border-box';
      note.setAttribute('aria-label', s.title + '：人工笔记');
      const votes = el('div', undefined, 'controls');
      for (const [value, label] of Object.entries(feedbackLabels)) {
        const button = el('button', label);
        button.onclick = async () => {
          votes.querySelectorAll('button').forEach(b => b.disabled = true);
          try {
            const response = await fetch('/api/comparisons/' + report.id + '/feedback/' + s.id, {
              method:'POST', headers:{'Content-Type':'application/json'},
              body:JSON.stringify({verdict:value, note:note.value})
            });
            if (!response.ok) throw new Error(await response.text());
            status.textContent = '已保存你的判断：' + label + '。与 AI 审核分开留档。';
          } catch(error) {status.textContent = error.message;}
          finally {votes.querySelectorAll('button').forEach(b => b.disabled = false);}
        };
        votes.append(button);
      }
      card.append(status, note, votes);
    }
    return card;
  }

  async function load() {
    const response = await fetch('/api/comparisons/latest');
    if (!response.ok) throw new Error('无法读取比较结果');
    const report = await response.json();
    if (!report) {box.hidden = true;return;}
    box.replaceChildren();
    box.append(el('h2','本轮选择：' + report.methods.find(m => m.id === report.selected_method).label), el('p', report.reason));
    box.append(el('p','截至 ' + report.as_of + ' · 当前 Codex 的非盲审核 · 单一目标的暂定选择 · 人工反馈 ' + report.human_feedback.length + ' 条', 'muted'));
    const table = el('table'); table.style.cssText = 'width:100%;border-collapse:collapse;text-align:left';
    const head = el('tr');
    ['方式','状态','正文已读 / 审核对象','入选 / 新发现','结果档案'].forEach(t => head.append(el('th',t)));
    table.append(head);
    for (const m of report.methods) {
      const tr = el('tr'); tr.style.background = m.status === 'active' ? '#e4f3ea' : '#f5f7f6';
      const vals = [m.label, {active:'保留', archived:'归档', not_run:'未运行'}[m.status], m.status === 'not_run' ? '—' : m.body_read + ' / ' + m.reviewed_sources, m.status === 'not_run' ? '—' : m.shortlisted + ' / ' + m.new_shortlisted];
      vals.forEach(t => {const td = el('td', t);td.style.padding='12px 8px';tr.append(td);});
      const td = el('td');td.append(link('独立 JSON','/api/comparisons/' + report.id + '/methods/' + m.id));tr.append(td);table.append(tr);
    }
    const scroll = el('div');scroll.style.overflowX='auto';scroll.append(table);box.append(scroll);
    box.append(el('p','入选数是 AI 审核结果，不是人工认可数。配套仓库不重复计数；全文受阻不按内容低质计分。', 'muted'));
    box.append(el('h3','先读这两篇'));
    for (const s of report.sources.filter(s => s.method === report.selected_method && s.verdict === 'shortlist')) box.append(sourceCard(s,report));
    box.append(el('h3','什么时候需要你介入'));
    for (const h of report.human_intervention) box.append(el('p', h.when + '：' + h.action));
    const limits = el('details');limits.append(el('summary','比较局限与采集缺陷（必须连同结论阅读）'));
    for (const text of report.limitations) limits.append(el('p', text, 'muted'));
    box.append(limits);
    const archive = el('details');archive.append(el('summary','逐篇审核档案：入选、排除、暂缓及配套材料'));
    for (const m of report.methods) {
      const group = el('details'); group.append(el('summary',m.label + ' · ' + m.summary));
      for (const s of report.sources.filter(s => s.method === m.id)) group.append(sourceCard(s,report,true));
      archive.append(group);
    }
    box.append(archive,link('完整审核 JSON','/api/comparisons/' + report.id));
    const old = document.getElementById('experiment-archive');old.removeAttribute('open');
    old.dataset.archived = 'true';
    const disable = () => old.querySelectorAll('button:not(#experiment-refresh)').forEach(b => b.disabled = true);
    disable();new MutationObserver(disable).observe(old,{childList:true,subtree:true});
  }
  load().catch(error => {box.textContent = error.message;});
})();
