/* Presentation only: no fetches, processing commands, or persisted user data.
   Dock/typography follow the pinned Maxime source. Native animations avoid a React runtime. */
import Prism from 'prismjs';

const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
const island=document.querySelector('.reading-island');
let frame,headings=[],positions=[],range=1,lastProgress=-1;
function expandIsland(open){
  island.dataset.expanded=String(open);document.querySelector('#island-outline').setAttribute('aria-expanded',open);
  document.querySelector('#island-links').inert=!open;document.querySelector('#island-top').inert=!open;
}
document.querySelector('#island-outline').onclick=()=>expandIsland(island.dataset.expanded!=='true');
document.querySelector('#island-top').onclick=()=>{expandIsland(false);document.querySelector('#reading-top').click();};
document.addEventListener('pointerdown',event=>{if(!island.contains(event.target))expandIsland(false);});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&island.dataset.expanded==='true'){expandIsland(false);document.querySelector('#island-outline').focus();}});

function measure(){
  const mode=document.body.dataset.mode;
  const selector=mode==='source'?'#source-body':mode==='translation'?'#translation-body':'#parallel-body .pair-source';
  headings=[...document.querySelectorAll(selector+' h2,'+selector+' h3')];
  positions=headings.map(h=>h.getBoundingClientRect().top+scrollY);
  const links=document.querySelector('#island-links');links.replaceChildren();
  headings.forEach(heading=>{
    const link=document.createElement('a');link.href='#'+heading.id;link.textContent=heading.textContent.replace(/\s*¶\s*$/,'');if(heading.tagName==='H3')link.className='subheading';
    link.onclick=event=>{event.preventDefault();expandIsland(false);heading.scrollIntoView({block:'start',behavior:reduced()?'instant':'smooth'});const url=new URL(location.href);url.hash=heading.id;history.replaceState(null,'',url);};links.append(link);
  });
  range=Math.max(1,document.documentElement.scrollHeight-innerHeight);
  requestUpdate();
}
function update(){
  frame=null;
  const progress=Math.min(1,Math.max(0,scrollY/range)),percent=Math.round(progress*100);
  const scrolled=scrollY>250&&scrollY<range-100;
  if(document.body.dataset.scrolled!==String(scrolled)){
    document.body.dataset.scrolled=String(scrolled);
    document.querySelector('#main-header').inert=scrolled;
    island.inert=!scrolled;if(!scrolled)expandIsland(false);
  }
  if(lastProgress!==percent){
    lastProgress=percent;document.querySelector('.reading-progress').style.setProperty('--reading-progress',progress);
    document.querySelector('.reading-progress').setAttribute('aria-valuenow',percent);
  }
  const headingOffset=headings.length?parseFloat(getComputedStyle(headings[0]).scrollMarginTop)||120:120;
  let index=-1;for(let i=0;i<positions.length;i++){if(positions[i]<=scrollY+headingOffset+1)index=i;else break;}
  const title=document.querySelector('#title').textContent;
  if(document.querySelector('#island-title').textContent!==title)document.querySelector('#island-title').textContent=title;
  document.querySelectorAll('#outline-links a,#island-links a').forEach(link=>{
    if(index>=0&&link.hash==='#'+headings[index].id)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');
  });
}
function requestUpdate(){if(!frame)frame=requestAnimationFrame(update);}
addEventListener('scroll',requestUpdate,{passive:true});
addEventListener('resize',measure,{passive:true});
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
    const copy=document.createElement('button');copy.className='code-copy';copy.setAttribute('aria-label','复制代码');copy.title='复制代码';
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
document.addEventListener('reader:mode',()=>requestAnimationFrame(ready));
document.querySelector('#core-fold').addEventListener('toggle',measure);
ready();
