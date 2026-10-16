/* The selector shows latest work per URL, with an explicitly opened history item. */
export function workflowChoices(rows,selectedRun){
  const latest=new Map();
  for(const row of [...rows].sort((a,b)=>String(b.created||'').localeCompare(String(a.created||'')))){
    const key=row.url||row.run_id;
    if(!latest.has(key))latest.set(key,row);
  }
  const choices=[...latest.values()].map(row=>({...row,historical:false}));
  const selected=rows.find(row=>row.run_id===selectedRun);
  if(selected&&!choices.some(row=>row.run_id===selectedRun))choices.push({...selected,historical:true});
  return choices;
}
