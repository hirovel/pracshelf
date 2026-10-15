import {test,mock} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {join,resolve,sep} from 'node:path';
import {publishCompiledPages} from './publish.mjs';

test('Git includes every compiled page while excluding private build artifacts',async t=>{
  const root=fileURLToPath(new URL('../../',import.meta.url));
  const checkout=spawnSync('git',['rev-parse','--show-toplevel'],{cwd:root,encoding:'utf8',windowsHide:true});
  if(checkout.status!==0||resolve(checkout.stdout.trim())!==resolve(root)){
    t.skip('Git ignore contract applies in a repository checkout.');return;
  }
  const compiled=(await fs.readdir(join(root,'src','learning_library','cactus'),{recursive:true}))
    .filter(name=>name.endsWith('.html')).map(name=>'src/learning_library/cactus/'+name.replaceAll('\\','/'));
  assert.ok(compiled.includes('src/learning_library/cactus/reading-builds/index.html'));
  const privatePaths=['data/reading-builds/demo/input.json','custom-library/reading-builds/job/input.json',
    'custom-library/model-runs/response.txt','src/learning_library/cactus/reading-builds/unexpected.json'];
  const result=spawnSync('git',['check-ignore','--no-index','--stdin'],{
    cwd:root,input:[...compiled,...privatePaths].join('\n')+'\n',encoding:'utf8',windowsHide:true,
  });
  assert.equal(result.status,0,result.stderr);
  assert.deepEqual(result.stdout.trim().split(/\r?\n/).sort(),privatePaths.sort());
});

async function fixture(t){
  const base=resolve('.test-runs');await fs.mkdir(base,{recursive:true});
  const root=await fs.mkdtemp(join(base,'publish-'));
  t.after(async()=>{assert.equal(root.startsWith(base+sep),true);await fs.rm(root,{recursive:true,force:true});});
  const parent=join(root,'src','learning_library'),target=join(parent,'cactus');
  await fs.mkdir(join(root,'frontend','dist','_astro'),{recursive:true});
  await fs.mkdir(join(root,'frontend','licenses'));await fs.mkdir(target,{recursive:true});
  await fs.writeFile(join(root,'frontend','dist','index.html'),'NEW_PAGE');
  await fs.writeFile(join(root,'frontend','dist','_astro','new.js'),'NEW_SCRIPT');
  await fs.writeFile(join(root,'frontend','LICENSE'),'TEST_LICENSE');
  await fs.writeFile(join(root,'frontend','UPSTREAM.md'),'TEST_SOURCE');
  await fs.writeFile(join(root,'frontend','licenses','font.txt'),'TEST_FONT_NOTICE');
  await fs.writeFile(join(target,'old.js'),'OLD_SENTINEL');
  return {root,parent,target};
}

test('compiled publish replaces stale files and preserves notices',async t=>{
  const {root,target,parent}=await fixture(t);
  await publishCompiledPages(root);
  assert.equal(await fs.readFile(join(target,'index.html'),'utf8'),'NEW_PAGE');
  await assert.rejects(fs.stat(join(target,'old.js')),{code:'ENOENT'});
  assert.equal(await fs.readFile(join(target,'licenses','font.txt'),'utf8'),'TEST_FONT_NOTICE');
  assert.deepEqual(await fs.readdir(parent),['cactus']);
});

test('failed replacement restores the previous compiled pages',async t=>{
  const {root,target,parent}=await fixture(t),rename=fs.rename;
  mock.method(fs,'rename',async(from,to)=>{
    if(from.includes('.cactus-next-')&&to===target)throw new Error('INSTALL_FAILED');
    return rename(from,to);
  });
  t.after(()=>mock.restoreAll());
  await assert.rejects(publishCompiledPages(root),/INSTALL_FAILED/);
  assert.equal(await fs.readFile(join(target,'old.js'),'utf8'),'OLD_SENTINEL');
  assert.deepEqual(await fs.readdir(parent),['cactus']);
});

test('missing input and an existing publish lock leave the old tree untouched',async t=>{
  const {root,target,parent}=await fixture(t);
  await fs.unlink(join(root,'frontend','dist','index.html'));
  await assert.rejects(publishCompiledPages(root));
  assert.equal(await fs.readFile(join(target,'old.js'),'utf8'),'OLD_SENTINEL');
  const lock=join(parent,'.cactus-publish.lock');await fs.writeFile(lock,'EXISTING');
  await assert.rejects(publishCompiledPages(root),/前端发布锁已存在/);
  assert.equal(await fs.readFile(lock,'utf8'),'EXISTING');
});

test('a linked destination is rejected without changing the linked files',async t=>{
  const {root,target,parent}=await fixture(t),outside=join(root,'unrelated');
  await fs.mkdir(outside);await fs.writeFile(join(outside,'keep.txt'),'KEEP_SENTINEL');
  await fs.rename(target,join(parent,'original'));
  await fs.symlink(outside,target,process.platform==='win32'?'junction':'dir');
  await assert.rejects(publishCompiledPages(root),/普通目录/);
  assert.equal(await fs.readFile(join(outside,'keep.txt'),'utf8'),'KEEP_SENTINEL');
  assert.equal(await fs.readFile(join(parent,'original','old.js'),'utf8'),'OLD_SENTINEL');
});
