import fs from 'node:fs/promises';
import {resolve,join,relative,isAbsolute} from 'node:path';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';

async function regularTree(path){
  const entry=await fs.lstat(path);
  if(entry.isSymbolicLink())throw new Error('编译产物和许可不能包含符号链接。');
  if(entry.isDirectory())for(const name of await fs.readdir(path))await regularTree(join(path,name));
  else if(!entry.isFile())throw new Error('编译产物必须是普通文件。');
}

/** Replace the fixed package directory. A failed install restores the old tree. */
export async function publishCompiledPages(projectRoot){
  const root=await fs.realpath(resolve(projectRoot));
  const frontend=join(root,'frontend'),parent=join(root,'src','learning_library');
  const source=join(frontend,'dist'),target=join(parent,'cactus');
  for(const path of [frontend,join(root,'src'),parent]){
    if((await fs.lstat(path)).isSymbolicLink()||await fs.realpath(path)!==path)
      throw new Error('发布目录不能通过符号链接跳出项目。');
  }
  const suffix=randomUUID(),stage=join(parent,'.cactus-next-'+suffix),backup=join(parent,'.cactus-previous-'+suffix);
  const lockPath=join(parent,'.cactus-publish.lock');
  const lock=await fs.open(lockPath,'wx').catch(error=>{
    if(error.code==='EEXIST')throw new Error('前端发布锁已存在；请检查构建进程。');
    throw error;
  });
  async function removeOwned(path){
    const inside=relative(parent,path);
    if(isAbsolute(inside)||inside.startsWith('..')||!['.cactus-next-'+suffix,'.cactus-previous-'+suffix].includes(inside))
      throw new Error('临时目录不在本次发布范围。');
    await fs.rm(path,{recursive:true,force:true});
  }
  let moved=false;
  try{
    await regularTree(source);
    for(const name of ['LICENSE','UPSTREAM.md','licenses'])await regularTree(join(frontend,name));
    if(!(await fs.stat(join(source,'index.html'))).isFile())throw new Error('缺少编译首页。');
    const old=await fs.lstat(target).catch(error=>{if(error.code!=='ENOENT')throw error;return null;});
    if(old&&(old.isSymbolicLink()||!old.isDirectory()))throw new Error('现有页面目标必须是普通目录。');
    await fs.mkdir(stage);
    await fs.cp(source,stage,{recursive:true});
    for(const name of ['LICENSE','UPSTREAM.md','licenses'])await fs.cp(join(frontend,name),join(stage,name),{recursive:true});
    await regularTree(stage);
    if(old){await fs.rename(target,backup);moved=true;}
    try{await fs.rename(stage,target);}
    catch(error){if(moved){await fs.rename(backup,target);moved=false;}throw error;}
    if(moved)await removeOwned(backup);
    return {status:'published',target};
  }finally{
    try{await removeOwned(stage);}
    finally{try{await lock.close();}finally{await fs.unlink(lockPath);}}
  }
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  await publishCompiledPages(fileURLToPath(new URL('../../',import.meta.url)));
  console.log('Published compiled pages and notices.');
}
