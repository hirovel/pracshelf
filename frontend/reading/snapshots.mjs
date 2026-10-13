/** A small build-time interface: loadSnapshots(directory, {demo}).
 * Owns the export contract, approval gate, hash, unsafe links and duplicate IDs.
 * This is an offline artifact check, not authentication of who approved a file.
 */
import {createHash} from 'node:crypto';
import {readdir, readFile, lstat} from 'node:fs/promises';
import {join} from 'node:path';

const envelopeKeys=['schema','mode','approved','deployed','demo','content_hash','note'];
const noteKeys=['note_id','title','source_url','source_date','source_date_status','category','note','purpose','quote','applicability','freshness','checked_on','trial','content_origin','note_updated_at'];
export function noteHash(note) {
  // Matches publication.encode: UTF-8, sorted ASCII field names, 2 spaces, final LF.
  const ordered=Object.fromEntries(Object.keys(note).sort().map(k=>[k,note[k]]));
  return createHash('sha256').update(JSON.stringify(ordered,null,2)+'\n','utf8').digest('hex');
}
function exactKeys(value, keys, label) {
  if(!value || typeof value!=='object' || Array.isArray(value) ||
     Object.keys(value).sort().join('|')!==[...keys].sort().join('|'))
    throw new Error(`${label} 字段不符合公开快照契约（缺项或多余字段）。`);
}
function date(value) {
  return typeof value==='string' && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10)===value;
}
export function validateSnapshot(value,{demo=false}={}) {
  exactKeys(value,envelopeKeys,'快照');
  if(value.schema!=='public-note-snapshot/v1' || value.mode!=='approved' || value.approved!==true || value.deployed!==false)
    throw new Error('仅接收尚未部署的已确认导出；未批准预览不能用于阅读站。');
  if(value.demo!==demo) throw new Error('真实与演示快照不能混用。');
  const n=value.note;exactKeys(n,noteKeys,'公开笔记');
  for(const k of noteKeys.filter(k=>!['source_date','checked_on','note_updated_at'].includes(k)))
    if(typeof n[k]!=='string' || n[k].length>16000) throw new Error(`字段 ${k} 类型或长度无效。`);
  if(!/^note-[a-f0-9]{20}$/.test(n.note_id) || !n.title.trim() || !n.note.trim())
    throw new Error('需要有效笔记标识、标题与非空笔记。');
  if(n.content_origin!==(demo?'demo':'human')) throw new Error('笔记来源与当前构建模式不一致。');
  let url;try{url=new URL(n.source_url);}catch{throw new Error('来源链接无效。');}
  if(!['https:','http:'].includes(url.protocol)||url.username||url.password) throw new Error('来源必须为不带凭据的 HTTP(S) 链接。');
  if(!['confirmed','unknown'].includes(n.source_date_status)||
     (n.source_date_status==='confirmed'?!date(n.source_date):n.source_date!==null)||
     (n.checked_on!==null&&!date(n.checked_on))||
     typeof n.note_updated_at!=='string'||!/^\d{4}-\d{2}-\d{2}T/.test(n.note_updated_at)||Number.isNaN(Date.parse(n.note_updated_at)))
    throw new Error('日期必须有明确含义；未知原文日期应为 null。');
  if(!['未分类','Skills','提示词','工作流','知识'].includes(n.category)||
     !['适用性待核实','在记录条件下适用','已不适用'].includes(n.freshness)||
     !['未试用','试用有帮助','部分有帮助','试用无帮助','阅读材料，无需试用'].includes(n.trial))
    throw new Error('分类或适用状态不符合当前契约。');
  if(n.freshness!=='适用性待核实'&&(!n.applicability.trim()||!n.checked_on)) throw new Error('明确适用判断必须保留条件和核查日期。');
  if(value.content_hash!==noteHash(n)) throw new Error('内容哈希不匹配；请重新导出，不要直接修改快照。');
  return {note:n,hash:value.content_hash};
}
export async function loadSnapshots(directory,{demo=false}={}) {
  const names=(await readdir(directory)).filter(n=>n.endsWith('.json')).sort();
  const notes=new Map(),inputs=[];
  for(const name of names){
    const path=join(directory,name),stat=await lstat(path);
    if(!stat.isFile()||stat.isSymbolicLink()||stat.size>1024*1024) throw new Error(`${name}: 仅接收 1 MiB 内的普通 JSON 文件。`);
    let parsed;try{parsed=JSON.parse(await readFile(path,'utf8'));}catch{throw new Error(`${name}: 无法读取 JSON。`);}
    let checked;try{checked=validateSnapshot(parsed,{demo});}catch(e){throw new Error(`${name}: ${e.message}`);}
    const previous=notes.get(checked.note.note_id);
    if(previous&&previous.hash!==checked.hash) throw new Error(`${name}: 同一笔记有多个版本，请只保留本次选定版本。`);
    notes.set(checked.note.note_id,checked);inputs.push({file:name,note_id:checked.note.note_id,hash:checked.hash});
  }
  return {schema:'reading-build-input/v1',demo,notes:[...notes.values()].sort((a,b)=>b.note.note_updated_at.localeCompare(a.note.note_updated_at)||a.note.note_id.localeCompare(b.note.note_id)),inputs};
}
