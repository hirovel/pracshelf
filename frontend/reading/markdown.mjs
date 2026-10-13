/** Only this renderer's HTML may enter a note preview or a static blog article. */
import MarkdownIt from 'markdown-it';

const markdown=new MarkdownIt({html:false,linkify:false,typographer:false}).disable('image');

export function renderNote(source) {
  return markdown.render(source);
}

export function noteExcerpt(source,maxLength=140) {
  const tokens=markdown.parse(source,{});
  const firstParagraph=tokens.findIndex(token=>token.type==='paragraph_open');
  const inline=tokens.slice(Math.max(0,firstParagraph)).find(token=>token.type==='inline');
  const text=(inline?.children||[]).filter(token=>['text','code_inline','softbreak','hardbreak'].includes(token.type))
    .map(token=>['softbreak','hardbreak'].includes(token.type)?' ':token.content).join('');
  return text.length>maxLength?text.slice(0,maxLength)+'…':text;
}
