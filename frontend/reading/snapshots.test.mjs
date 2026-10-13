import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname,basename} from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {demoSnapshot} from './demo.mjs';
import {loadSnapshots,validateSnapshot,noteHash} from './snapshots.mjs';

function syntheticApproved(){const v=demoSnapshot();v.demo=false;v.note.content_origin='human';v.content_hash=noteHash(v.note);return v;}
test('unapproved previews, private fields and tampering fail closed',()=>{
  const value=syntheticApproved();assert.equal(validateSnapshot(value).note.title,value.note.title);
  assert.throws(()=>validateSnapshot({...value,mode:'preview',approved:false}),/未批准/);
  assert.throws(()=>validateSnapshot({...value,trace:'PRIVATE'}),/字段/);
  assert.throws(()=>validateSnapshot({...value,note:{...value.note,raw_source:'PRIVATE'}}),/字段/);
  assert.throws(()=>validateSnapshot({...value,note:{...value.note,note:'changed'}}),/哈希/);
});
test('demo stays in explicit demo builds; links and date meanings are checked',()=>{
  const value=demoSnapshot();assert.equal(validateSnapshot(value,{demo:true}).note.content_origin,'demo');
  assert.throws(()=>validateSnapshot(value),/混用/);assert.throws(()=>validateSnapshot(syntheticApproved(),{demo:true}),/混用/);
  const unsafe=syntheticApproved();unsafe.note.source_url='javascript:alert(1)';unsafe.content_hash=noteHash(unsafe.note);assert.throws(()=>validateSnapshot(unsafe),/HTTP/);
  const date=syntheticApproved();date.note.source_date='2026-10-10';date.content_hash=noteHash(date.note);assert.throws(()=>validateSnapshot(date),/日期/);
});
test('Python exporter hash and JavaScript reader hash agree, including Unicode',()=>{
  const v=syntheticApproved();v.note.note='中文\n引号 " 测试 😀';
  const python=fileURLToPath(new URL(process.platform==='win32'?'../../.venv/Scripts/python.exe':'../../.venv/bin/python',import.meta.url));
  const hash=execFileSync(python,['-X','utf8','-c','import sys,json,hashlib; v=json.load(sys.stdin); print(hashlib.sha256((json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+"\\n").encode()).hexdigest())'],{input:JSON.stringify(v.note),encoding:'utf8'}).trim();
  assert.equal(noteHash(v.note),hash);
});
test('empty is empty; duplicate identical exports deduplicate; conflicting revisions fail',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'reading-test-'));
  try{
    assert.equal((await loadSnapshots(dir)).notes.length,0);
    const v=syntheticApproved();await writeFile(join(dir,'a.json'),JSON.stringify(v));await writeFile(join(dir,'b.json'),JSON.stringify(v));
    assert.equal((await loadSnapshots(dir)).notes.length,1);
    v.note.note+=' New revision';v.content_hash=noteHash(v.note);await writeFile(join(dir,'b.json'),JSON.stringify(v));
    await assert.rejects(()=>loadSnapshots(dir),/多个版本/);
  }finally{
    assert.equal(dirname(resolve(dir)),resolve(tmpdir()));
    assert.ok(basename(dir).startsWith('reading-test-'));
    await rm(dir,{recursive:true,force:true});
  }
});
