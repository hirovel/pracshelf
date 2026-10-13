/* Read-only catalogue projection. Filtering never changes saved decisions or notes. */
export const categories={practice:'实践经验',technical:'技术原理',updates:'产品动态',unclassified:'未分类'};
export const filters={all:'全部',kept:'已保留',notes:'笔记',archived:'归档'};

function publicationDate(value){
  const match=typeof value==='string'&&value.match(/^(\d{4})-(\d{2})-(\d{2})(?:$|T|\s)/);
  if(!match)return null;
  const date=match[0].slice(0,10),parsed=new Date(date+'T00:00:00Z');
  return Number.isFinite(parsed.getTime())&&parsed.toISOString().slice(0,10)===date?date:null;
}

export function libraryView(rows,{filter='all',query='',category=''}={}){
  if(!Object.hasOwn(filters,filter))filter='all';
  const search=query.trim().toLocaleLowerCase();
  return rows.map(row=>{
    const payload=row.curation?.latest?.payload||{};
    const kind=row.classification?.kind;
    return {...row,decision:payload.decision||'inbox',category:Object.hasOwn(categories,kind)?kind:'unclassified',publicationDate:publicationDate(row.published)};
  }).filter(row=>{
    if(filter==='archived'?row.decision!=='archived':row.decision==='archived')return false;
    if(filter==='kept'&&row.decision!=='kept')return false;
    if(filter==='notes'&&!row.note?.trim())return false;
    if(category&&row.category!==category)return false;
    const payload=row.curation?.latest?.payload||{};
    const text=[row.title,row.author,row.domain,row.url,row.note,categories[row.category],row.classification?.reason,payload.purpose,payload.applicability,payload.trial_note].join(' ').toLocaleLowerCase();
    return !search||text.includes(search);
  }).sort((left,right)=>(right.publicationDate||'').localeCompare(left.publicationDate||'')||String(right.saved||'').localeCompare(String(left.saved||'')));
}
