// Workspace appearance only. Application navigation owns the document context.
const theme=document.getElementById('theme')!;
theme.onclick=()=>{
  const next=document.documentElement.dataset.theme==='dark'?'light':'dark';
  document.documentElement.dataset.theme=next;
  try{localStorage.setItem('workspace-theme',next);}catch{}
};
