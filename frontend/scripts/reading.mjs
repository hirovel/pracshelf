/** Build into a fresh directory, validate artifacts, then update a local pointer.
 * Neither command publishes. No database or private service is read here.
 */
import {execFileSync} from 'node:child_process';
import {randomUUID,createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,readdir,lstat,rename} from 'node:fs/promises';
import {dirname,join,resolve,relative,extname,isAbsolute} from 'node:path';
import {fileURLToPath} from 'node:url';
import {loadSnapshots} from '../reading/snapshots.mjs';
import {demoSnapshot} from '../reading/demo.mjs';

const frontend=fileURLToPath(new URL('../',import.meta.url)),repo=dirname(frontend);
const args=process.argv.slice(2),command=args.shift(),demo=args.includes('--demo');
function option(name,fallback){const i=args.indexOf(name);if(i<0)return fallback;if(!args[i+1]||args[i+1].startsWith('--'))throw new Error(`${name} 缺少值。`);return args[i+1];}
const mode=demo?'demo':'real',root=resolve(option('--output-root',join(repo,'data','reading-builds',mode))),latest=join(root,'latest.json');
async function files(directory){
  const result=[];
  for(const name of await readdir(directory)){const p=join(directory,name),s=await lstat(p);if(s.isSymbolicLink())throw new Error('构建产物不可包含符号链接。');if(s.isDirectory())result.push(...await files(p));else result.push(p);}
  return result;
}
async function build(){
  const id=option('--build-id',randomUUID());
  if(!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(id))throw new Error('构建编号无效。');
  const buildRoot=join(root,id),site=join(buildRoot,'site');
  await mkdir(buildRoot,{recursive:true});
  const report={id,mode,status:'running',created:new Date().toISOString(),site,model_calls:0,deployed:false};
  try{
    let input=resolve(frontend,option('--input','reading/snapshots'));
    if(demo&&!args.includes('--input')){input=join(buildRoot,'synthetic-input');await mkdir(input);await writeFile(join(input,'demo.json'),JSON.stringify(demoSnapshot(),null,2));}
    const content=await loadSnapshots(input,{demo});
    const manifest=join(buildRoot,'input.json');await writeFile(manifest,JSON.stringify(content,null,2));
    Object.assign(report,{input_directory:input,notes:content.notes.length,inputs:content.inputs});
    execFileSync(process.execPath,[join(frontend,'node_modules','astro','bin','astro.mjs'),'build','--config','astro.config.mjs'],{
      cwd:join(frontend,'reading'),env:{...process.env,ASTRO_TELEMETRY_DISABLED:'1',READING_MANIFEST:manifest,READING_OUTPUT:site},stdio:'inherit',windowsHide:true,timeout:70000,
    });
    const notices=await Promise.all(['Inter.OFL.txt','FiraCode.OFL.txt'].map(name=>readFile(join(frontend,'licenses',name),'utf8')));
    await writeFile(join(site,'FONT_LICENSES.txt'),notices.join('\n\n'));
    const expected=new Set(['index.html','about/index.html','404.html','FONT_LICENSES.txt',...content.notes.map(({note})=>`notes/${note.note_id}/index.html`)]);
    const artifacts=[];
    for(const path of await files(site)){
      const name=relative(site,path).split('\\').join('/');let body=await readFile(path);
      const asset=name.startsWith('_astro/')&&['.css','.woff2'].includes(extname(name));
      if(!(expected.has(name)||asset))throw new Error(`出现非预期产物：${name}`);
      if(extname(name)==='.woff2'&&body.subarray(0,4).toString()!=='wOF2')throw new Error(`字体文件无效：${name}`);
      if(extname(name)==='.html'){
        const prefix=relative(dirname(path),site).split('\\').join('/')||'.';
        // Inline CSS must resolve its local assets within this build, including nested previews.
        const html=body.toString('utf8').replace(/url\((['"]?)\/(_astro\/[^)'"\s]+)\1\)/g,
          (_,quote,asset)=>`url(${quote}${prefix}/${asset}${quote})`);
        if(/<script\b|<[^>]*\son\w+\s*=/i.test(html))throw new Error(`阅读站不应包含客户端脚本：${name}`);
        body=Buffer.from(html);await writeFile(path,body);
        expected.delete(name);
      }
      if(name==='FONT_LICENSES.txt')expected.delete(name);
      artifacts.push({file:name,bytes:body.length,sha256:createHash('sha256').update(body).digest('hex')});
    }
    if(expected.size)throw new Error(`构建缺少页面：${[...expected].join(', ')}`);
    report.status='succeeded';report.artifacts=artifacts.sort((a,b)=>a.file.localeCompare(b.file));report.finished=new Date().toISOString();
    await writeFile(join(buildRoot,'report.json'),JSON.stringify(report,null,2));
    if(!args.includes('--no-activate')){const temp=join(root,`latest-${id}.tmp`);await writeFile(temp,JSON.stringify(report,null,2));await rename(temp,latest);}
    console.log(`\n本地 ${mode} 阅读站：${content.notes.length} 篇。未部署。\n产物：${site}\n记录：${join(buildRoot,'report.json')}`);
  }catch(error){report.status='failed';report.finished=new Date().toISOString();report.error=error.message;await writeFile(join(buildRoot,'report.json'),JSON.stringify(report,null,2));throw error;}
}
async function preview(){
  const report=JSON.parse(await readFile(latest,'utf8'));
  const port=option('--port',demo?'8783':'8782');
  if(!/^\d+$/.test(port)||Number(port)<1024||Number(port)>65535)throw new Error('端口需在 1024–65535。');
  if(report.status!=='succeeded')throw new Error('只有成功构建才能启动预览。');
  const rel=relative(root,resolve(report.site));if(rel.startsWith('..')||isAbsolute(rel)||resolve(report.site)===root)throw new Error('预览目录不在本模块构建范围。');
  const python=join(repo,'.venv',process.platform==='win32'?'Scripts':'bin',process.platform==='win32'?'python.exe':'python');
  console.log(`仅本机预览：http://127.0.0.1:${port}/\n只服务静态产物，不使用工作台。`);
  execFileSync(python,['-m','http.server',port,'--bind','127.0.0.1','--directory',report.site],{stdio:'inherit',windowsHide:true});
}
try{if(command==='build')await build();else if(command==='preview')await preview();else throw new Error('使用 reading:build 或 reading:preview，可选 --demo。');}
catch(error){console.error('阅读站操作失败：'+error.message);process.exitCode=1;}
