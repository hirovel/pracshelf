/* One editor for the reader and workbench. Drafts never count as saved records. */
const curationEditor = (() => {
  const root = document.getElementById('curation-panel');
  if (!root) return {load: async () => {}};
  root.classList.add('curation');
  root.innerHTML = `
    <h2>阅读笔记</h2>
    <div class="curation-status"><p id="curation-state" role="status">正在读取…</p><span class="curation-private">私有</span></div>
    <p id="curation-version" class="curation-hint"></p>
    <form id="curation-form"><fieldset id="curation-fields" disabled>
      <label class="curation-note-label">笔记正文<span id="curation-count" class="curation-hint">Markdown · 0 / 16,000</span><textarea name="note" maxlength="16000" rows="10" placeholder="记录你的理解、实际尝试或待解决的问题。支持 Markdown。"></textarea></label>
      <div class="curation-actions"><button type="submit" class="curation-save">保存笔记</button><a id="curation-export" aria-disabled="true" title="保存笔记正文后可导出">导出 Markdown ↓</a><button type="button" id="curation-reset">恢复已保存内容</button></div>
      <p class="curation-hint curation-shortcut">Ctrl / ⌘ S 保存 · 导出读取已保存正文</p>
      <details class="curation-metadata"><summary>分类、用途与其他记录（可选）</summary><div class="curation-grid">
        <label>类别<select name="category"><option value="unclassified">未分类</option><option value="skill">Skills</option><option value="prompt">提示词</option><option value="workflow">工作流</option><option value="knowledge">知识</option></select></label>
        <label>整理决定<select name="decision"><option value="inbox">留在收集箱</option><option value="kept">保留到资料库</option><option value="archived">归档（仍可恢复）</option></select></label>
      </div>
      <label>用途或取舍原因<textarea name="purpose" maxlength="1000" rows="2" placeholder="选择保留或归档时填写。只保存笔记无需填写。"></textarea></label>
      <details><summary>适用条件与时效</summary>
        <div class="curation-grid">
          <label>适用性<select name="freshness"><option value="unknown">待核实</option><option value="current">在记录条件下适用</option><option value="outdated">已不适用</option></select></label>
          <label>核查日期<input name="checked_on" type="date"></label>
        </div>
        <label>版本、条件与判断依据<textarea name="applicability" maxlength="2000" rows="3" placeholder="例如模型或工具版本、任务范围，以及为什么认为现在仍适用。不能只看发布日期。"></textarea></label>
      </details>
      <details><summary>试用反馈</summary>
        <label>试用情况<select name="trial"><option value="not_tried">未试用</option><option value="useful">试用有帮助</option><option value="mixed">部分有帮助</option><option value="not_useful">试用无帮助</option><option value="not_applicable">阅读材料，无需试用</option></select></label>
        <label>实际任务与结果<textarea name="trial_note" maxlength="2000" rows="3" placeholder="写明在哪个任务、什么环境下用了它，观察到什么。作者或 AI 的判断不作为我的试用。"></textarea></label>
      </details>
      <details><summary>原文依据（可选）</summary>
        <label>原文逐字摘录<textarea name="quote" maxlength="4000" rows="4" placeholder="从保存的原文复制一段关键内容；保存时校验能否逐字对应。"></textarea></label>
        <p class="curation-hint">引文匹配只能检查出处，不能证明你的理解或原文观点正确。</p>
      </details>
      </details>
    </fieldset></form>
    <p id="curation-message" class="curation-hint" role="status"></p>
    <p id="curation-error" role="alert"></p>
    <div id="curation-conflict" hidden><p>新记录已显示在下面。草稿仍保留；请对照后合并，再保存。</p><button id="curation-merge" type="button">已对照，以当前草稿继续编辑</button></div>
    <details><summary id="curation-history-label">笔记历史</summary><div id="curation-history"></div></details>
    <details class="curation-debug"><summary>调试数据</summary><a id="curation-json" download>下载笔记记录 JSON</a></details>`;
  const el = id => document.getElementById(id);
  const form = el('curation-form');
  const defaults = {category:'unclassified',decision:'inbox',purpose:'',note:'',freshness:'unknown',checked_on:null,applicability:'',trial:'not_tried',trial_note:'',quote:''};
  const labels = {inbox:'待整理',kept:'已保留',archived:'已归档'};
  const trialLabels = {not_tried:'未试用',useful:'试用有帮助',mixed:'部分有帮助',not_useful:'试用无帮助',not_applicable:'无需试用'};
  let runId = null, state = null, baseId = null, submissionId = null, token = 0, saving = false;
  const key = () => 'curation-draft-v1:' + location.host + ':' + state.version_id;
  function values() {
    const value = Object.fromEntries(Object.keys(defaults).map(name => [name, form.elements[name].value]));
    value.checked_on = value.checked_on || null;
    return value;
  }
  function populate(value) {
    for (const name of Object.keys(defaults)) form.elements[name].value = value[name] ?? defaults[name] ?? '';
    el('curation-count').textContent = 'Markdown · ' + form.elements.note.value.length.toLocaleString() + ' / 16,000';
  }
  function persist() {
    if (!state?.available) return;
    try { sessionStorage.setItem(key(), JSON.stringify({values:values(),baseId,submissionId})); }
    catch { el('curation-message').textContent = '浏览器无法保存草稿，请勿在提交前关闭页面。'; }
  }
  function changed() {
    submissionId = null;
    el('curation-count').textContent = 'Markdown · ' + form.elements.note.value.length.toLocaleString() + ' / 16,000';
    el('curation-message').textContent = '草稿未保存。刷新可恢复；导出仍为已保存版本。';
    persist();
  }
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
  async function request(path, options) {
    const response = await fetch(path, options);
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(Array.isArray(body.detail) ? body.detail.map(item => item.msg).join('\n') : body.detail || '请求失败');
      error.status = response.status;
      throw error;
    }
    return body;
  }
  function showHistory() {
    const latest = state.latest;
    el('curation-state').textContent = (state.origin === 'demo' ? '演示 · ' : '') + (latest?.payload.note ? '已保存 · ' + new Date(latest.created).toLocaleString() : '尚无笔记正文');
    el('curation-version').textContent = '原文 v' + state.version_id + (state.older_version ? ' · 已有新版，这里的笔记保留在旧版。' : '') + (!latest && state.history.length ? ' · 旧版笔记在历史中。' : '');
    el('curation-history-label').textContent = '笔记历史（' + state.history.length + '）';
    el('curation-history').replaceChildren();
    for (const row of [...state.history].reverse()) {
      const item = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = new Date(row.created).toLocaleString() + ' · ' + labels[row.payload.decision] + ' · v' + row.version_id + (row.origin === 'demo' ? ' · 演示' : ' · 本人填写');
      const body = document.createElement('p');
      body.className = 'curation-saved-note';
      body.textContent = row.payload.note || '没有笔记正文。';
      item.append(summary, body);
      if (globalThis.savedRecordView) {
        const metadata = document.createElement('details');
        const heading = document.createElement('summary'); heading.textContent = '其他记录';
        const {note, ...other} = row.payload;
        metadata.append(heading, savedRecordView.render(other)); item.append(metadata);
      }
      el('curation-history').append(item);
    }
    el('curation-export').setAttribute('aria-disabled', String(!latest?.payload.note));
    if (latest?.payload.note) el('curation-export').href = '/api/library/' + runId + '/note';
    else el('curation-export').removeAttribute('href');
    el('curation-export').title = latest?.payload.note ? '下载已保存笔记；不包含全文或译文' : '保存笔记正文后可导出';
    el('curation-json').href = '/api/runs/' + runId + '/curation';
    document.dispatchEvent(new CustomEvent('curation-updated', {detail:{runId,state}}));
  }
  function reset() {
    populate(state.latest?.payload || defaults);
    baseId = state.latest?.id ?? null;
    submissionId = null;
    try { sessionStorage.removeItem(key()); } catch {}
    el('curation-message').textContent = '';
    el('curation-error').textContent = '';
    el('curation-conflict').hidden = true;
  }
  el('curation-reset').onclick = reset;
  el('curation-export').onclick = event => { if (el('curation-export').getAttribute('aria-disabled') === 'true') event.preventDefault(); };
  form.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault(); if (!saving && !el('curation-fields').disabled) form.requestSubmit();
    }
  });
  el('curation-merge').onclick = () => {
    baseId = state.latest?.id ?? null;
    submissionId = null;
    persist();
    el('curation-conflict').hidden = true;
    el('curation-error').textContent = '';
    el('curation-message').textContent = '已更新编辑基准。请检查草稿，再点击保存。';
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (saving || !state?.available) return;
    saving = true;
    const savingRun = runId;
    el('curation-fields').disabled = true;
    el('curation-error').textContent = '';
    submissionId ||= crypto.randomUUID();
    const payload = {...values(),version_id:state.version_id,previous_id:baseId,submission_id:submissionId};
    persist();
    try {
      const saved = await request('/api/runs/' + savingRun + '/curation', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if (savingRun !== runId) return;
      state = saved;
      reset();
      showHistory();
      el('curation-message').textContent = state.latest?.payload.note ? '已保存，可以导出 Markdown。' : '其他记录已保存。笔记正文为空。';
    } catch (error) {
      if (savingRun !== runId) return;
      el('curation-error').textContent = error.message;
      if (error.status === 409) {
        try {
          const updated = await request('/api/runs/' + savingRun + '/curation');
          if (savingRun !== runId) return;
          state = updated;
          showHistory();
          el('curation-conflict').hidden = false;
        } catch { el('curation-error').textContent += '\n最新记录读取失败，请稍后刷新，草稿会保留。'; }
      }
    } finally {
      saving = false;
      if (savingRun === runId) el('curation-fields').disabled = false;
    }
  };
  return {async load(id) {
    const sequence = ++token;
    runId = id;
    state = null;
    el('curation-fields').disabled = true;
    el('curation-state').textContent = '正在读取…';
    el('curation-version').textContent = '';
    el('curation-history').replaceChildren();
    el('curation-export').removeAttribute('href');el('curation-export').setAttribute('aria-disabled', 'true');
    el('curation-json').href = '/api/runs/' + id + '/curation';
    el('curation-conflict').hidden = true;
    el('curation-message').textContent = '';
    el('curation-error').textContent = '';
    try {
      const loaded = await request('/api/runs/' + id + '/curation');
      if (sequence !== token) return;
      state = loaded;
      if (!state.available) {
        populate(defaults);
        el('curation-state').textContent = '成功保存正文后可写笔记。';
        el('curation-version').textContent = '';
        el('curation-history').replaceChildren();
        el('curation-export').removeAttribute('href');el('curation-export').setAttribute('aria-disabled', 'true');
        return;
      }
      populate(state.latest?.payload || defaults);
      baseId = state.latest?.id ?? null;
      submissionId = null;
      el('curation-message').textContent = '';
      el('curation-conflict').hidden = true;
      try {
        const draft = JSON.parse(sessionStorage.getItem(key()) || 'null');
        if (draft) {
          populate(draft.values);
          baseId = draft.baseId;
          submissionId = draft.submissionId;
          el('curation-message').textContent = '已恢复未保存草稿。';
          if (baseId !== (state.latest?.id ?? null)) el('curation-conflict').hidden = false;
        }
      } catch { el('curation-message').textContent = '草稿无法恢复，已显示已保存记录。'; }
      showHistory();
      el('curation-fields').disabled = false;
    } catch (error) {
      if (sequence === token) el('curation-error').textContent = error.message;
    }
  }};
})();
