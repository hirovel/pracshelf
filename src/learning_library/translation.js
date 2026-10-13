// A reading branch: no writes to source text or human acceptance records.
const chineseTranslation = (() => {
  const el = id => document.getElementById(id);
  const names = {blocked:'等待正文提取',not_started:'尚未翻译',partial:'部分完成',running:'运行中',failed:'本批失败，可重试',succeeded:'全文翻译完成 · 待人工核对'};
  let state = null, stopped = false;
  function show(value, id, busy) {
    state = value;
    el('translation-status').textContent = names[value.status];
    el('translation-meta').textContent = `${value.origin==='demo'?'演示结果（模拟响应）':'DeepSeek API'} · ${value.model} · 原文版本 ${value.version_id??'未生成'} · ${value.completed}/${value.total} 批完成` + (value.created?' · '+new Date(value.created).toLocaleString():'');
    let inputTokens=0,outputTokens=0,reported=0;
    for(const attempt of value.attempts) {
      try {const usage=JSON.parse(attempt.output?.body??'null')?.usage;if(usage){inputTokens+=usage.prompt_tokens??0;outputTokens+=usage.completion_tokens??0;reported++;}}catch{}
    }
    el('translation-usage').textContent=`本运行 ${value.attempts.length} 次调用 · ${value.attempts.filter(a=>a.status==='failed').length} 次失败`+(reported?` · 已报告用量：输入 ${inputTokens.toLocaleString()} / 输出 ${outputTokens.toLocaleString()} tokens`:' · 尚无实际用量报告');
    el('translation-config').hidden = value.configured;
    el('translate').disabled = busy || !value.available || !value.configured || value.status==='succeeded';
    el('translate').textContent = value.status==='failed'||value.status==='partial'?'继续翻译（保留已完成批次）':'翻译全文为中文';
    el('translation-download').hidden = value.status!=='succeeded';
    el('translation-download').href = `/api/runs/${id}/translation/text`;
    el('translation-json').href = `/api/runs/${id}/translation`;
    el('translation-trace').textContent = JSON.stringify(value.attempts,null,2);
    el('translation-runs').replaceChildren();
    for(const run of value.history??[]) {
      const link=document.createElement('a'), row=document.createElement('p');
      link.href=`/api/runs/${id}/translation?translation_id=${run.id}`;link.target='_blank';link.rel='noopener';
      link.textContent=`运行 #${run.id} · ${run.prompt_version} · ${run.model} · ${new Date(run.created).toLocaleString()}`;
      row.append(link);el('translation-runs').append(row);
    }
    const error = value.attempts.at(-1)?.error;
    el('translation-error').textContent = error || '';
    el('translation-audit').hidden=!value.audit;
    el('translation-findings').replaceChildren();
    if(value.audit) {
      el('translation-audit-summary').textContent=value.audit.summary;
      for(const finding of value.audit.findings) {
        const item=document.createElement('article'), h=document.createElement('h3'), text=document.createElement('pre'), link=document.createElement('a');
        h.textContent=`${finding.id} · ${finding.severity} · ${finding.title}`;
        text.textContent=`原文：${finding.source}\n\n译文：${finding.translation}\n\n判断：${finding.reason}\n\n建议：${finding.suggestion}`;
        link.href=`#translation-part-${finding.batch}`;link.textContent=`定位第 ${finding.batch} 批对照`;
        link.onclick=()=>{el('translation-pairs-details').open=true;};
        item.append(h,text,link);el('translation-findings').append(item);
      }
    }
    el('translation-pairs').replaceChildren();
    for(const part of value.segments) {
      const article = document.createElement('article'); article.className='translation-pair';article.id=`translation-part-${part.index+1}`;
      const h = document.createElement('h3'); h.textContent=`第 ${part.index+1} 批 · `+({succeeded:'译文待核对',preserved:'代码／空白原样保留',pending:'尚未翻译',running:'翻译中',failed:'失败'}[part.status]);
      const columns = document.createElement('div'); columns.className='translation-columns';
      for(const [label,text] of [['原文',part.source],['中文译文',part.translation??'此批尚无有效译文。']]) {
        const column=document.createElement('div'), title=document.createElement('strong'), content=document.createElement('pre');
        title.textContent=label;content.textContent=text;column.append(title,content);columns.append(column);
      }
      article.append(h,columns);el('translation-pairs').append(article);
    }
  }
  async function run(id, api) {
    stopped=false;el('translation-stop').hidden=false;el('translation-pairs-details').open=true;
    try {
      while(!stopped) {
        el('translation-status').textContent='翻译中 · 等待当前批次返回';
        const value=await api(`/api/runs/${id}/translation/next`,{method:'POST'});
        show(value,id,true);
        if(['succeeded','failed'].includes(value.status))break;
      }
    } finally {el('translation-stop').hidden=true;}
  }
  el('translation-stop').onclick=()=>{stopped=true;el('translation-error').textContent='将在当前批次返回后停止，已完成译文会保留。'};
  return {show,run,lock(busy){if(state)el('translate').disabled=busy||!state.available||!state.configured||state.status==='succeeded';}};
})();
