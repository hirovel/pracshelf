/* Read-only article navigation and code presentation. */
import Prism from 'prismjs';
import 'prismjs/components/prism-python.js';
import {renderNote} from '../../reading/markdown.mjs';
curationEditor.renderWith(renderNote);
let frame,headings=[],positions=[];
function measure(){
  const mode=document.body.dataset.mode;
  const selector=mode==='source'?'#source-body':mode==='translation'?'#translation-body':'#parallel-body .pair-source';
  headings=[...document.querySelectorAll(selector+' h2,'+selector+' h3')];
  positions=headings.map(heading=>heading.getBoundingClientRect().top+scrollY);
  requestUpdate();
}
function update(){
  frame=null;let index=-1;
  for(let i=0;i<positions.length;i++){if(positions[i]<=scrollY+115)index=i;else break;}
  document.querySelectorAll('#outline-links a').forEach(link=>{
    if(index>=0&&link.hash==='#'+headings[index].id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');
  });
}
function requestUpdate(){if(!frame)frame=requestAnimationFrame(update);}
addEventListener('scroll',requestUpdate,{passive:true});addEventListener('resize',measure,{passive:true});
const resize=new ResizeObserver(measure);resize.observe(document.querySelector('#article'));

const codeObserver=new IntersectionObserver(entries=>{
  entries.forEach(entry=>{
    if(!entry.isIntersecting)return;
    const code=entry.target,raw=code.textContent;
    const declared=[...code.classList].find(name=>name.startsWith('language-'))?.slice(9);
    // Unlabelled fences keep their source unchanged; JS lexical coloring is presentation only.
    const language=declared||(/\b(?:const|let|await|async|function)\b/.test(raw)?'javascript':'plain');
    if(Prism.languages[language])code.innerHTML=Prism.highlight(raw,Prism.languages[language],language);
    codeObserver.unobserve(code);
  });
},{rootMargin:'600px'});
function decorateCode(){
  document.querySelectorAll('.prose pre > code').forEach(code=>{
    if(code.closest('.code-frame'))return;
    const pre=code.parentElement,raw=code.textContent,wrapper=document.createElement('div');wrapper.className='code-frame';
    pre.before(wrapper);wrapper.append(pre);
    const lines=document.createElement('span');lines.className='code-lines';lines.setAttribute('aria-hidden','true');
    const count=raw.replace(/\n$/,'').split('\n').length;lines.textContent=Array.from({length:count},(_,i)=>i+1).join('\n');pre.append(lines);
    const copy=document.createElement('button');copy.type='button';copy.className='code-copy';copy.setAttribute('aria-label','复制代码');copy.title='复制代码';
    const ns='http://www.w3.org/2000/svg',icon=document.createElementNS(ns,'svg'),path=document.createElementNS(ns,'path');
    icon.setAttribute('viewBox','0 0 16 16');icon.setAttribute('aria-hidden','true');path.setAttribute('d','M6 5h7v9H6z M3 11V2h7');icon.append(path);copy.append(icon);
    const status=document.createElement('span');copy.append(status);
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(raw);status.textContent='已复制';setTimeout(()=>{status.textContent='';},1500);}catch{status.textContent='复制失败';}};
    wrapper.append(copy);codeObserver.observe(code);
  });
}
function ready(){
  if(document.querySelector('#article').hidden)return;
  decorateCode();measure();
}
document.addEventListener('reader:ready',ready);
document.addEventListener('reader:note-preview',decorateCode);
document.addEventListener('reader:mode',()=>requestAnimationFrame(ready));
document.querySelector('#core-fold').addEventListener('toggle',measure);
ready();
