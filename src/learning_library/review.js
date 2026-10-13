/* User judgments are edited here; no program check preselects an acceptance. */
const originalReview = (() => {
  const byId = id => document.getElementById(id);
  const verdicts = {accepted:'通过原文验收',needs_fix:'需修复',pending:'暂缓'};
  const bodies = {unreviewed:'尚未核对',matched:'已对照，未发现遗漏或混入',issues:'发现遗漏或混入'};
  const structures = {unreviewed:'尚未核对',matched:'关键结构保留',not_applicable:'原文无这些结构',issues:'发现丢失或变形'};
  const fields = {body:'review-body',structure:'review-structure',date_status:'review-date-status',confirmed_date:'review-date',date_evidence:'review-date-evidence',verdict:'review-verdict',note:'review-note'};
  const drafts = new Map();
  let view = null, shownId = null, base = null, savedId = null;
  const label = state => (state?.origin==='demo'?'演示：':'人工：') + (state?.latest?verdicts[state.latest.payload.verdict]:'尚未验收');
  function describe(row) {
    const p = row.payload;
    return (row.origin==='demo'?'演示记录':'人工提交')+' #'+row.id+' · '+new Date(row.created).toLocaleString()+'\n'
      +verdicts[p.verdict]+' · 文档版本 '+row.version_id+'\n'
      +'正文：'+bodies[p.body]+'；结构：'+structures[p.structure]+'\n'
      +'日期：'+(p.date_status==='confirmed'?p.confirmed_date+'（人工核实，依据：'+p.date_evidence+'）':p.date_status==='unknown'?'已查看，仍未知':'尚未核对')
      +(p.note?'\n备注：'+p.note:'');
  }
  function dateFields() {
    const confirmed = byId('review-date-status').value==='confirmed';
    byId('review-date-fields').hidden=!confirmed;
    byId('review-date').required=confirmed;
    byId('review-date-evidence').required=confirmed;
  }
  function values() {
    const result={};for(const [key,id] of Object.entries(fields))result[key]=byId(id).value;
    if(result.date_status!=='confirmed'){result.confirmed_date=null;result.date_evidence=''}
    else result.confirmed_date=result.confirmed_date||null;
    return result;
  }
  function changed() {
    if(!view)return;
    const previous=drafts.get(view.id);
    drafts.set(view.id,{base:previous?.base||base,values:values(),submission_id:null});
    byId('review-message').textContent='草稿未保存。切换任务会保留本页草稿；刷新页面前请保存。';
    dateFields();
  }
  document.getElementById('review-form').addEventListener('input',changed);
  document.getElementById('review-form').addEventListener('change',changed);
  return {
    label,
    show(run,visible,busy) {
      view=run;
      const state=run.review;
      byId('demo-notice').hidden=state?.origin!=='demo';
      byId('original-review').hidden=!visible;
      if(!state)return;
      byId('review-target').textContent=state.available?'本次验收绑定文档版本 '+state.version_id+' / 提取尝试 '+state.extract_attempt_id+'。重新提取的任务单独验收。':'成功提取正文后才能提交验收。';
      byId('review-result').textContent=state.latest?describe(state.latest):'尚无验收记录。程序没有代你填写结论。';
      byId('review-fields').disabled=busy||!state.available;
      byId('review-json').href='/api/runs/'+run.id+'/reviews';
      byId('review-history-label').textContent='查看修改历史（'+state.history.length+'）';
      const history=byId('review-history');history.replaceChildren();
      for(const row of [...state.history].reverse()){const a=document.createElement('article');a.textContent=describe(row);history.append(a)}
      const draft=drafts.get(run.id);
      base=draft?.base||{version_id:state.version_id,extract_attempt_id:state.extract_attempt_id,previous_review_id:state.latest?.id??null};
      const value=draft?.values||state.latest?.payload||{body:'unreviewed',structure:'unreviewed',date_status:'unreviewed',verdict:'',confirmed_date:null,date_evidence:'',note:''};
      for(const [key,id] of Object.entries(fields))byId(id).value=value[key]??'';
      if(shownId!==run.id)byId('review-editor').open=!state.latest;
      shownId=run.id;
      byId('review-message').textContent=draft?'草稿未保存。刷新页面前请保存。':savedId===run.id?'已保存，旧记录保留在修改历史中。':'';
      dateFields();
    },
    payload() {
      let draft=drafts.get(view.id);
      if(!draft){draft={base,values:values(),submission_id:null};drafts.set(view.id,draft)}
      draft.submission_id ||= crypto.randomUUID();
      return {...draft.base,...draft.values,submission_id:draft.submission_id};
    },
    saved(id) {drafts.delete(id);savedId=id;byId('review-editor').open=false},
  };
})();
