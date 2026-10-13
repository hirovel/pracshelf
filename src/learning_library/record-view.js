/* Read-only presentation shared by reader and pipeline. No fetch or writes here. */
globalThis.savedRecordView = (() => {
  const labels = {
    version_id:'原文版本', content_hash:'正文指纹', source_url:'原始来源', url:'来源链接',
    model:'配置模型', provider:'提供方', effort:'推理档位', engine:'执行框架', adapter:'执行 Adapter',
    execution:'执行配置', packages:'依赖版本', revision:'实现版本', request:'请求', messages:'输入消息',
    budget:'处理范围', schema:'输出结构', response_format:'响应格式', prompt_version:'提示词版本',
    goal:'学习目标', origin:'内容性质', source_scope:'原文范围', cache_key:'结果复用标识',
    checked_characters:'已覆盖字符', full_source_characters:'全文字符', result_id:'阅读副本编号',
    selected_attempt:'所选尝试', request_id:'采集记录编号', quality:'正文检查', request_frozen:'输入已固定',
    extraction:'整理结果', attempts:'尝试次数', final_url:'实际来源地址', content_type:'响应类型',
    started:'开始时间', finished:'结束时间', created:'保存时间', status:'状态', error:'失败原因',
    judgment:'阅读判断与核心', core:'核心内容', recommendation:'阅读建议', reason:'理由',
    effective_recommendation:'有效阅读建议', points:'关键内容', evidence:'原文依据', quote:'引文',
    supports:'支持的内容', claim_type:'论断类型', conditions:'适用条件', unknowns:'未核实事项',
    time_dependencies:'版本依赖', reading_kind:'阅读分类', reading_reason:'分类理由',
    reading_evidence:'分类依据', evidence_checks:'引文检查', matched:'定位结果', target:'对象',
    source_quote:'定位原文', start:'起始位置', end:'结束位置', semantic_fidelity:'语义核对',
    core_available:'核心可展示', text:'正文', input:'输入', output:'输出', response:'提供方响应',
    thread_id:'线程编号', turn_id:'Turn 编号', event_count:'事件数量', last_event:'最后事件',
    terminal_status:'完成终态', request_count:'底层请求数', api_fallback:'付费 API 兜底',
    system_characters:'系统消息字符', user_characters:'用户消息字符', message_characters:'消息字符合计',
    messages_sha256:'消息指纹', instruction_sources:'额外指令来源', reported_thread_total:'线程累计用量',
    usage:'供应商用量', inputTokens:'输入 tokens', cachedInputTokens:'缓存读取 tokens',
    outputTokens:'输出 tokens', reasoningOutputTokens:'其中推理 tokens', totalTokens:'合计 tokens',
    total:'累计用量', last:'最近用量', events:'原始事件', segments:'正文批次', checks:'检查',
    source_characters:'原文字符', translation_id:'中文副本编号', submission_id:'提交编号',
    web_search:'联网搜索', max_retries:'自动重试次数', timeout_seconds:'超时秒数',
    note:'笔记正文', purpose:'用途', category:'分类', decision:'整理状态',
    freshness:'适用性', checked_on:'核查日期', applicability:'适用条件', trial:'试用状态', trial_note:'试用记录',
  };
  const states = {completed:'已完成', succeeded:'成功', failed:'失败', running:'运行中', queued:'排队中',
    read:'推荐阅读', focus:'建议精读', skip:'暂不推荐', uncertain:'信息不足', unverified:'未核对',
    practice:'实践经验', technical:'技术原理', updates:'产品动态', unclassified:'未分类',
    author_judgment:'作者判断', factual_statement:'原文事实陈述', example:'示例',
    inbox:'待整理', kept:'已保留', archived:'已归档', unknown:'未知',
    current:'在记录条件下适用', outdated:'已不适用', not_tried:'未试用', useful:'试用有帮助',
    mixed:'部分有帮助', not_useful:'试用无帮助', not_applicable:'无需试用',
    skill:'Skills', prompt:'提示词', workflow:'工作流', knowledge:'知识',
    full_saved_text:'固定全文', model_api:'模型结果', human:'本人填写', demo:'演示记录',
    external_agent:'外部 Agent'};
  const enums = new Set(['status','terminal_status','recommendation','effective_recommendation',
    'semantic_fidelity','reading_kind','claim_type','decision','freshness','trial','category','source_scope','origin']);
  function node(tag, text, className='') {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;el.className=className;return el;}
  function text(value,key) {if(value==null)return '未记录';if(typeof value==='boolean')return value?'是':'否';return enums.has(key)?states[value]||String(value):String(value);}
  function render(value, key='') {
    const root=node('div',undefined,'record-view');
    if(value==null||typeof value!=='object') {
      const content=text(value,key);root.append(node(content.includes('\n')||content.length>180?'pre':'p',content,'record-text'));return root;
    }
    if(Array.isArray(value)) {
      if(!value.length){root.append(node('p','没有记录条目','record-muted'));return root;}
      for(const [index,item] of value.slice(0,20).entries()) {
        const box=node('details',undefined,'record-item');
        const role={system:'系统约束',user:'原文与任务',assistant:'模型输出',tool:'工具结果'}[item?.role];
        box.append(node('summary',role?`${role} · ${String(item.content||'').length.toLocaleString()} 字符`:`${labels[key]||'记录'} ${index+1}`));
        box.append(render(role?item.content:item,role?'content':key));root.append(box);
      }
      if(value.length>20)root.append(node('p',`显示前 20 项，共 ${value.length} 项；完整内容可下载技术数据。`,'record-muted'));return root;
    }
    const facts=node('dl',undefined,'record-facts');
    for(const [name,item] of Object.entries(value)) {
      if(item!==null&&typeof item==='object') {
        const box=node('details',undefined,'record-item');box.append(node('summary',labels[name]||name),render(item,name));root.append(box);
      } else {
        const row=node('div');row.append(node('dt',labels[name]||name),node('dd',text(item,name)));facts.append(row);
      }
    }
    if(facts.childNodes.length)root.prepend(facts);
    return root;
  }
  function framework(observation) {
    const value=observation?.execution;
    const name={codex_sdk:'Codex SDK',langchain:'LangChain',langchain_tool:'LangChain · StructuredTool'}[value?.engine];
    return name?name+(value.adapter?' / '+value.adapter:''):'执行框架未记录';
  }
  return {render, framework};
})();
