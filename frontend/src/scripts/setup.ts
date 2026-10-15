// inspect reads session state only; SDK work starts only from explicit buttons.
const el=(id:string)=>document.getElementById(id)!;
declare const readingUI:{reduceMotion:()=>boolean;motionPreference:()=>string;setMotionPreference:(value:string)=>boolean};
const motion=el('motion-preference') as HTMLSelectElement;
motion.value=readingUI.motionPreference();
const motionStatus=()=>el('motion-status').textContent=readingUI.reduceMotion()?'当前减少动态效果。':'当前使用常规动态效果。';
motionStatus();
motion.onchange=()=>{const saved=readingUI.setMotionPreference(motion.value);motionStatus();if(!saved)el('motion-status').textContent+=' 浏览器未允许保存，本页设置已生效。';};
addEventListener('reading:motion',()=>{motion.value=readingUI.motionPreference();motionStatus();});
const activeStates=new Set(['starting','checking','waiting_login']);
let timer:ReturnType<typeof setTimeout>|undefined;
let submitting=false;
async function request(path:string,action?:string) {
  const response=await fetch(path,{cache:'no-store',...(action?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})}:{})});
  const value=await response.json();if(!response.ok)throw new Error(value.detail||'连接状态读取失败');return value;
}
function text(id:string,value:string){el(id).textContent=value;}
function render(value:any) {
  const environment=value.environment,operation=value.operation,result=operation.result;
  const active=activeStates.has(operation.status)||Boolean(operation.id&&!operation.finished_at);
  const configuration=value.configuration;
  const same=result&&result.model===configuration.model&&result.effort===configuration.effort;
  text('environment-state',environment.installed&&environment.python_supported?'依赖已安装':'依赖不完整');
  text('environment-detail',`Python ${environment.python}\nSDK ${environment.sdk||'未安装'} · runtime ${environment.runtime||'未安装'}`);
  const build=environment.reading_build;
  text('build-state',build?.ready?'博客构建依赖已安装':'博客构建条件未满足');
  text('build-detail',build?.ready?'Node.js 可用，源码与 Astro 依赖存在；构建不调用模型。':build?.error||'无法读取博客构建条件。');
  text('account-state',result?result.auth_type==='chatgpt'?'ChatGPT 登录':result.auth_type==='missing'?'尚未登录':result.auth_type==='apiKey'?'当前为 API 登录':'未能确认':'尚未检查');
  text('account-detail',result?.auth_type==='chatgpt'?'官方运行时返回 ChatGPT 登录类别；未执行模型推理。':result?.auth_type==='apiKey'?'本项目不使用当前 API 登录，请显式连接 ChatGPT。':'登录检查与 SDK 安装分开；已有本地结果仍可读取。');
  text('model-state',!result?'尚未检查':!same?'配置已变化':result.model_found===null?'尚未读取目录':!result.model_found?'目录中没有指定模型':!result.effort_found?'目录中没有指定档位':'指定模型与档位在目录中');
  text('model-detail',`${configuration.model} / ${configuration.effort}\n${result&&!same?'上次检查 '+result.model+' / '+result.effort+'，请重新检查。':'目录不证明任务能够推理或账户额度充足。'}`);
  const statuses:Record<string,string>={unchecked:'尚未执行 SDK 检查',starting:'正在启动官方 SDK 运行时…',checking:'正在检查登录与模型目录…',waiting_login:'等待你在官方页面完成登录',completed:'检查完成',cancelled:'本次等待已取消',failed:'连接检查失败'};
  text('connection-status',operation.error||statuses[operation.status]||operation.status);
  (el('connection-check') as HTMLButtonElement).disabled=active||submitting||!environment.installed;
  const login=el('connection-login') as HTMLButtonElement;login.disabled=active||submitting||!environment.installed;login.hidden=result?.auth_type==='chatgpt';
  const cancel=el('connection-cancel') as HTMLButtonElement;cancel.hidden=!active;cancel.disabled=Boolean(operation.cancel_requested);
  cancel.textContent=operation.cancel_requested?'正在取消…':'取消等待';
  const link=el('connection-login-link') as HTMLAnchorElement;link.hidden=!operation.login_url;if(operation.login_url)link.href=operation.login_url;else link.removeAttribute('href');
  const facts=el('connection-observation');facts.replaceChildren();
  const entries=[['资料目录',environment.data_directory],['本次操作',operation.action==='login'?'连接（已有登录复用）':operation.action==='check'?'只读检查':'尚未执行'],['配置',operation.configuration?`${operation.configuration.model} / ${operation.configuration.effort}`:'未检查'],['开始时间',operation.started_at||'未开始'],['完成时间',operation.finished_at||'尚未结束'],['SDK 检查耗时',result?result.elapsed_seconds+' 秒':'未记录'],['启动至结束',operation.elapsed_seconds==null?'未记录':operation.elapsed_seconds+' 秒'],['模型任务',result?String(result.model_tasks):'0'],['付费 API 回退','关闭']];
  for(const [label,value]of entries){const term=document.createElement('dt'),detail=document.createElement('dd');term.textContent=label;detail.textContent=value;facts.append(term,detail);}
  const events=el('connection-events');events.replaceChildren();
  for(const item of operation.events||[]){const li=document.createElement('li');li.textContent=item.at+' · '+(statuses[item.status]||item.status);events.append(li);}
  if(result){const li=document.createElement('li');li.textContent='SDK 请求：'+(result.calls.length?result.calls.join(' → '):'未发出');events.append(li);}
  const catalog=el('connection-models');catalog.replaceChildren();
  if(result?.models){const title=document.createElement('h3');title.textContent='本次返回的模型目录';const list=document.createElement('ul');for(const item of result.models){const row=document.createElement('li'),model=document.createElement('span'),efforts=document.createElement('span');model.textContent=item.model;efforts.textContent=item.efforts.join(' / ');row.append(model,efforts);list.append(row);}catalog.append(title,list);}
  const history=el('connection-history');history.replaceChildren();
  for(const item of value.history||[]){const row=document.createElement('li');row.textContent=[item.started_at,item.action==='login'?'连接':'检查',statuses[item.status]||item.status,item.error||'',item.elapsed_seconds==null?'':item.elapsed_seconds+' 秒'].filter(Boolean).join(' · ');history.append(row);}
  if(timer)clearTimeout(timer);if(active)timer=setTimeout(load,700);
}
function error(cause:unknown){const element=el('setup-error');element.hidden=false;element.textContent=cause instanceof Error?cause.message:'连接操作失败，可重新检查。';}
async function load(){try{render(await request('/api/connection'));}catch(cause){error(cause);(el('connection-check') as HTMLButtonElement).disabled=false;}}
async function begin(action:string){if(submitting)return;submitting=true;el('setup-error').hidden=true;try{render(await request('/api/connection',action));}catch(cause){error(cause);}finally{submitting=false;await load();}}
el('connection-check').onclick=()=>begin('check');el('connection-login').onclick=()=>begin('login');
el('connection-cancel').onclick=async()=>{try{const response=await fetch('/api/connection/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const value=await response.json();if(!response.ok)throw new Error(value.detail);render(value);}catch(cause){error(cause);}};
load();
