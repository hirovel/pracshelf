/* Shared presentation controls for catalogue and article; no requests or writes. */
const dock=document.querySelector('.dock');
const highlight=dock.querySelector('.dock-highlight');
const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
let animation;

function hoverItem(item){
  if(item.classList.contains('library-mark'))return;
  const box=item.getBoundingClientRect(),parent=dock.getBoundingClientRect();
  const next=`translateX(${box.left-parent.left}px)`,from=getComputedStyle(highlight).transform;
  animation?.cancel();highlight.style.width=box.width+'px';highlight.style.transform=next;highlight.style.opacity='1';
  if(!reduced())animation=highlight.animate([{transform:from},{transform:next}],{duration:350,easing:'cubic-bezier(.22,1,.36,1)'});
}
dock.querySelectorAll('a,button').forEach(item=>{
  item.addEventListener('pointerenter',()=>hoverItem(item));item.addEventListener('focus',()=>hoverItem(item));
});
dock.addEventListener('pointerleave',()=>{highlight.style.opacity='0';});
dock.addEventListener('focusout',event=>{if(!dock.contains(event.relatedTarget))highlight.style.opacity='0';});
dock.addEventListener('keydown',event=>{
  if(!['ArrowLeft','ArrowRight'].includes(event.key))return;
  const items=[...dock.querySelectorAll('a,button')],index=items.indexOf(document.activeElement);
  if(index<0)return;
  event.preventDefault();items[(index+(event.key==='ArrowRight'?1:items.length-1))%items.length].focus();
});
document.querySelector('#reading-top').onclick=()=>window.scrollTo({top:0,behavior:reduced()?'instant':'smooth'});
