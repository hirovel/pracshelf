/* Reading presentation. One initial GET; no analysis or translation execution.
   The optional personal-note editor owns its separate, explicit save action. */
(() => {
  const el=id=>document.getElementById(id);
  const params=new URLSearchParams(location.search);
  const runId=location.pathname.startsWith('/read/')?location.pathname.split('/').filter(Boolean).pop():params.get('run');
  const origins={agent_supplied:'外部 Agent 发现',rss:'RSS',user_link:'手动链接',web_search:'网页搜索',demo:'演示'};
  const types={author_judgment:'作者判断',factual_statement:'原文事实陈述',example:'示例'};
  const verdicts={read:'推荐阅读',focus:'建议精读',skip:'暂不推荐',uncertain:'信息不足'};
  const classificationOrigins={agent:'助手归类',model_api:'模型分类',human:'本人分类',demo:'演示分类'};
  let article=null, mode='parallel', modeAnimation=null, recordLoaded=false, recordLoading=false;
  const reduced=readingUI.reduceMotion;
  function node(tag,text,className){const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(className)element.className=className;return element;}
  const format=value=>Number.isFinite(value)?value.toLocaleString('zh-CN'):'未知';
  function disclosure(label){const details=node('details');details.append(node('summary',label));return details;}

  function renderCore(){
    const box=el('core');box.replaceChildren();
    const output=article.insights?.output, judgment=output?.judgment;
    if(!output){el('core-status').textContent='尚无核心结果';el('core-reason').textContent='原文可阅读';box.append(node('p','这篇资料尚未生成核心内容。','muted'));return;}
    el('core-status').textContent='AI 核心 · '+(verdicts[output.effective_recommendation]||'已有结果')+' · 语义未核对';el('core-reason').textContent=judgment.reason;
    const badge=node('div',undefined,'section-title');badge.append(node('h2','原文核心'),node('span','AI 提取 · 语义未核对'));box.append(badge);
    if(!output.core_available){box.append(node('p','当前判断没有可展示的核心内容。原始响应和校验结果保留在运行记录中。','muted'));return;}
    box.append(node('p',judgment.core,'core-text'));
    const points=disclosure(`核心论断与依据 · ${judgment.points.length} 条`);points.className='points';
    judgment.points.forEach((point,index)=>{
      const item=node('div',undefined,'point');item.append(node('small',String(index+1).padStart(2,'0')+' · '+types[point.claim_type]),node('p',point.text));
      const evidence=disclosure('原文依据');point.evidence.forEach(quote=>{const block=node('blockquote');block.append(node('p',quote.quote),node('cite',quote.supports));evidence.append(block);});item.append(evidence);points.append(item);
    });box.append(points);
    const conditions=[...judgment.conditions,...judgment.unknowns];
    if(conditions.length){const details=disclosure('条件与未确定事项');details.className='limitations';const list=node('ul');conditions.forEach(text=>list.append(node('li',text)));details.append(list);box.append(details);}
    box.append(node('p','引文匹配只验证文本来源，不能证明中文结论保留了原文的对象、否定和条件。','quality-note'));
  }
  function copyLabel(){return article.translation_origin==='human_edit'?'人工修改副本':article.translation_origin==='demo'?'演示副本':'机器译文';}
  function renderBody(){
    // HTML comes only from Library's non-executing Markdown renderer.
    el('source-body').innerHTML=article.source_html;el('translation-body').innerHTML=article.translation_html||'';
    const pairs=el('parallel-body');pairs.replaceChildren();
    if(article.has_translation){const labels=node('div',undefined,'parallel-labels');labels.append(node('span','原文'),node('span',copyLabel()));pairs.append(labels);
      article.pairs.forEach(pair=>{const row=node('div',undefined,'pair-row');row.dataset.pair=pair.index;const source=node('div',undefined,'prose pair-source'),translated=node('div',undefined,'prose pair-translation');source.lang='en';translated.lang='zh-CN';translated.dataset.origin=article.translation_origin;source.innerHTML=pair.source;translated.innerHTML=pair.translation;row.append(source,translated);pairs.append(row);});
    }
    document.querySelectorAll('button[data-mode]').forEach(button=>{button.disabled=button.dataset.mode!=='source'&&!article.has_translation;});
  }
  function buildOutline(){
    const pane=mode==='source'?el('source-body'):mode==='translation'?el('translation-body'):el('parallel-body');
    const headings=pane.querySelectorAll(mode==='parallel'?'.pair-source h2,.pair-source h3':'h2,h3');const links=el('outline-links');links.replaceChildren();
    headings.forEach((heading,index)=>{heading.id=`section-${mode}-${index}`;const link=node('a',heading.textContent.replace(/\s*¶\s*$/,''));link.href='#'+heading.id;if(heading.tagName==='H3')link.className='subheading';link.onclick=event=>{event.preventDefault();el('outline-dialog').close();heading.scrollIntoView({block:'start',behavior:reduced()?'instant':'smooth'});const url=new URL(location.href);url.hash=heading.id;history.replaceState(null,'',url);};links.append(link);});
    if(!headings.length)links.append(node('p','当前视图没有分节标题。','muted'));
  }
  function selectMode(value){
    mode=article.has_translation&&['source','translation','parallel'].includes(value)?value:'source';document.body.dataset.mode=mode;
    document.querySelectorAll('button[data-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mode===mode)));
    el('copy-status').textContent=mode==='source'?'原文 v'+article.version_id+' · 已保存'+(article.has_translation?'':' · 尚无中文副本'):copyLabel()+' · '+(article.status==='已验收副本'?'已确认':article.translation_origin==='human_edit'?'修改单独保存':'未人工核对');
    const url=new URL(location.href);url.searchParams.set('mode',mode);if(url.hash.startsWith('#section-')&&!url.hash.startsWith('#section-'+mode+'-'))url.hash='';history.replaceState(null,'',url);buildOutline();
    document.dispatchEvent(new CustomEvent('reader:mode'));
    document.dispatchEvent(new CustomEvent('workspace:document',{detail:{runId:article.run_id,title:article.title,readable:true,reader:{mode,resultId:params.get('result_id')}}}));
  }
  function renderRecords(){
    const result=article.insights, output=result?.output;
    const metric=result?.observation,tokens=metric?.tokens,timing=metric?.timing;
    const seconds=value=>Number.isFinite(value)?value+' 秒':'未知';
    const stack=el('record-stack');stack.replaceChildren(node('span',savedRecordView.framework(metric)),node('span',metric?.provider==='codex'?'ChatGPT 订阅':'提供方 '+(metric?.provider||'未记录')),node('span',result?'已保存分析':'尚无分析'));
    const metrics=el('record-metrics');metrics.replaceChildren();
    for(const [label,value] of [['输入 tokens',format(tokens?.input)],['输出 tokens',format(tokens?.output)],['缓存读取 tokens',format(tokens?.cached_input)],['历史耗时',seconds(timing?.task_seconds)],['结果复用',result?'#'+result.id:'无'],['本次新任务','0']]) {
      const card=node('div',undefined,'record-metric');card.append(node('small',label),node('strong',value));metrics.append(card);
    }
    const rows=[['原文版本',`v${article.version_id}`],['全文分析',result?'#'+result.id:'尚无结果'],['本次读取','新模型任务 0'],['历史配置',metric?[metric.provider,metric.configured_model,metric.effort].filter(Boolean).join(' / '):'未知'],['实际执行模型',metric?.execution_model||'未知'],['底层模型请求数',format(metric?.provider_request_count)],['输入 / 输出 tokens',format(tokens?.input)+' / '+format(tokens?.output)],['缓存读取 / 写入 tokens',format(tokens?.cached_input)+' / '+format(tokens?.cache_write_input)],['其中推理输出 tokens',format(tokens?.reasoning_output)],['分析 token 合计',format(tokens?.total)],['历史任务耗时',seconds(timing?.task_seconds)],['worker 内准备 / turn 执行',seconds(timing?.preparation_seconds)+' / '+seconds(timing?.turn_seconds)],['引文定位',output?`${output.evidence_checks.filter(item=>item.matched).length} / ${output.evidence_checks.length} 处匹配`:'尚无结果'],['语义检查',output?.semantic_fidelity==='unverified'?'未核对':'尚无语义核对记录'],['中文副本',article.has_translation?`${copyLabel()} #${article.selected_result_id}`:'尚无副本'],['发现渠道',origins[article.discovery?.origin]||'未记录']];
    const classification=article.classification;
    rows.splice(1,0,['资料分类',classification?.label||'未分类'],['分类来源',classificationOrigins[classification?.origin]||'尚无分类记录'],['分类依据',classification?.reason||'未记录']);
    el('classification-note').hidden=!classification?.evidence;el('classification-note').textContent=classification?.evidence?'分类原文依据（v'+classification.version_id+'）：'+classification.evidence.quote:'';
    const list=el('record-table');list.replaceChildren();rows.forEach(([title,value])=>{const row=node('div');row.append(node('dt',title),node('dd',value));list.append(row);});
    el('discovery-note').hidden=!article.discovery?.source_note;el('discovery-note').textContent=article.discovery?.source_note||'';el('record-link').href=metric?.detail_url||'/api/runs/'+article.run_id+'/insights?detail=full';el('translation-record-link').hidden=!article.has_translation;el('translation-record-link').href='/pipeline?run='+article.run_id+'&operation=translate';
    el('record-link').download='analysis-'+(result?.id||'record')+'.json';
    el('record-detail').hidden=!result;
  }
  async function loadRecord(){
    if(recordLoaded||recordLoading||!article?.insights)return;
    recordLoading=true;const box=el('record-content');box.replaceChildren(node('p','正在读取已保存输入与输出…','record-muted'));
    try {
      const response=await fetch(article.insights.observation?.detail_url||'/api/runs/'+article.run_id+'/insights/'+article.insights.id);
      const record=await response.json();if(!response.ok)throw new Error(typeof record.detail==='string'?record.detail:'记录读取失败');
      box.replaceChildren();
      for(const [label,value] of [['发送前固定输入',record.input],['分析结果与校验',record.output]]){const section=disclosure(label);section.className='record-item';section.append(savedRecordView.render(value));box.append(section);}
      if(record.error)box.append(node('p',record.error,'read-error'));recordLoaded=true;
    } catch(error) {
      const retry=node('button','重试读取');retry.type='button';retry.onclick=loadRecord;box.replaceChildren(node('p',error.message,'record-muted'),retry);
    } finally {recordLoading=false;}
  }
  el('record-detail').addEventListener('toggle',()=>{if(el('record-detail').open)loadRecord();});
  async function openNotes(){if(!article)return;el('notes-dialog').showModal();await curationEditor.load(article.run_id);}
  el('reading-outline').onclick=()=>{if(!article)return;el('outline-search').value='';el('outline-links').querySelectorAll('a').forEach(link=>link.hidden=false);el('outline-dialog').showModal();el('outline-search').focus();};
  el('reading-records').onclick=()=>{if(article)el('record-dialog').showModal();};el('reading-notes').onclick=openNotes;
  document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>button.closest('dialog').close());
  document.querySelectorAll('dialog').forEach(dialog=>dialog.onclick=event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
  document.querySelectorAll('button[data-mode]').forEach(button=>button.onclick=()=>{
    if(mode===button.dataset.mode)return;
    modeAnimation?.cancel();selectMode(button.dataset.mode);
    const pane=el(mode==='source'?'source-body':mode==='translation'?'translation-body':'parallel-body');
    if(!reduced())modeAnimation=pane.animate([{opacity:0},{opacity:1}],{duration:180,easing:'ease-out'});
  });
  el('outline-search').oninput=()=>{const query=el('outline-search').value.toLowerCase();el('outline-links').querySelectorAll('a').forEach(link=>link.hidden=!link.textContent.toLowerCase().includes(query));};
  addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();el('reading-outline').click();}});
  addEventListener('hashchange',()=>{if(location.hash==='#curation-panel')openNotes();else try{document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({block:'start'});}catch{}});
  if(!runId){el('loading').hidden=true;el('error').hidden=false;el('error').textContent='需要选择一篇已保存的资料。';return;}
  const result=params.get('result_id');
  fetch('/api/library/'+encodeURIComponent(runId)+(result?'?result_id='+encodeURIComponent(result):'')).then(async response=>{
    const value=await response.json();if(!response.ok)throw new Error(value.detail||'文章读取失败');article=value;
    document.title=value.title+' · PracShelf';el('title').textContent=value.title;el('domain').textContent=value.domain;el('version').textContent='原文 v'+value.version_id;el('author').textContent=value.author||'作者未记录';el('classification-title').textContent=value.classification?.label||'未分类';
    const date=value.published&&/^\d{4}-\d{2}-\d{2}$/.test(value.published)?new Date(value.published+'T12:00:00Z'):null;
    el('published').textContent=date&&!Number.isNaN(date.getTime())?date.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'}):value.published||'日期未知';if(value.published)el('published').dateTime=value.published;
    el('source').href=value.url;el('work').href='/pipeline?run='+value.run_id;el('processing-link').href=el('work').href;
    el('discovery').textContent=(origins[value.discovery?.origin]||'发现渠道未记录')+' · 原文 v'+value.version_id;el('reader-status').textContent=value.status+' · 保存于 '+new Date(value.saved).toLocaleDateString('zh-CN',{timeZone:'America/New_York'});
    renderCore();renderBody();renderRecords();selectMode(params.get('mode')||'parallel');el('loading').hidden=true;el('article').hidden=false;
    for(const id of ['reading-outline','reading-records','reading-notes'])el(id).disabled=false;
    document.dispatchEvent(new CustomEvent('reader:ready'));
    if(location.hash==='#curation-panel')openNotes();else try{document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({block:'start'});}catch{}
  }).catch(error=>{el('loading').hidden=true;el('error').hidden=false;el('error').textContent=error.message;});
})();
