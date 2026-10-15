/** Only this renderer's HTML may enter a note preview or a static blog article. */
import MarkdownIt from 'markdown-it';

const markdown=new MarkdownIt({html:false,linkify:false,typographer:false}).disable('image');

export function renderNote(source) {
  return markdown.render(source);
}

/** Build heading links from the same tokens that produce the safe note HTML. */
export function renderNoteDocument(source) {
  const env={},tokens=markdown.parse(source,env),outline=[],used=new Set();
  for(let index=0;index<tokens.length;index++) {
    const token=tokens[index];
    if(token.type!=='heading_open'||!['h2','h3'].includes(token.tag))continue;
    const inline=tokens[index+1];
    const title=(inline?.children||[]).filter(child=>['text','code_inline','softbreak','hardbreak'].includes(child.type))
      .map(child=>child.type.endsWith('break')?' ':child.content).join('').trim();
    const stem='note-'+(title.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,'-').replace(/^-|-$/g,'')||'section');
    let id=stem,suffix=1;
    while(used.has(id))id=stem+'-'+(++suffix);
    used.add(id);token.attrSet('id',id);
    outline.push({id,title,level:Number(token.tag.slice(1))});
  }
  return {html:markdown.renderer.render(tokens,markdown.options,env),outline};
}

export function noteExcerpt(source,maxLength=140) {
  const tokens=markdown.parse(source,{});
  const firstParagraph=tokens.findIndex(token=>token.type==='paragraph_open');
  const inline=tokens.slice(Math.max(0,firstParagraph)).find(token=>token.type==='inline');
  const text=(inline?.children||[]).filter(token=>['text','code_inline','softbreak','hardbreak'].includes(token.type))
    .map(token=>['softbreak','hardbreak'].includes(token.type)?' ':token.content).join('');
  return text.length>maxLength?text.slice(0,maxLength)+'…':text;
}
