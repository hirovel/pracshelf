import {noteHash} from './snapshots.mjs';
// Synthetic UI fixture, not a user's note, and never inserted into the private database.
export function demoSnapshot(){
  const note={
    note_id:'note-00000000000000000001',title:'演示：从保存笔记到博客页面',
    source_url:'https://example.com/synthetic-workflow',source_date:null,source_date_status:'unknown',
    category:'未分类',note:'这是一篇合成的页面验收笔记，不是用户的学习内容。\n\n## 输入与输出\n\n这里用列表检查 Markdown 是否保留结构。\n\n- 输入：已经保存的笔记正文。\n- 输出：只包含选中字段的固定预览。\n- 最后由使用者确认本次内容。\n\n## 代码示例\n\n```python\ndef note_preview(saved_note):\n    return {"note": saved_note}\n```\n\n示例代码只说明排版，不代表项目的实际实现。\n\n[参考来源](https://example.com/synthetic-workflow)\n\n## 验收范围\n\n这页用于检查标题、段落、列表、代码缩进和来源链接，不提供经过验证的 AI 使用建议。',
    purpose:'',quote:'',
    applicability:'',freshness:'适用性待核实',
    checked_on:null,trial:'未试用',content_origin:'demo',note_updated_at:'2026-10-10T12:00:00+00:00',
  };
  return {schema:'public-note-snapshot/v1',mode:'approved',approved:true,deployed:false,demo:true,content_hash:noteHash(note),note};
}
