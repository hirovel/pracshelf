import {test} from 'node:test';
import assert from 'node:assert/strict';
import {renderNote,noteExcerpt,renderNoteDocument} from './markdown.mjs';

test('blog notes preserve headings, paragraphs, lists, links and code indentation',()=>{
  const source='## 实际尝试\n\n第一段 **观察**。\n\n- 保存原文\n- 核对结果\n\n```python\ndef check():\n    return "ok"\n```\n\n[来源](https://example.test/article)';
  const html=renderNote(source);
  assert.match(html,/<h2>实际尝试<\/h2>/);
  assert.match(html,/<strong>观察<\/strong>/);
  assert.match(html,/<ul>\s*<li>保存原文<\/li>/);
  assert.match(html,/<pre><code class="language-python">def check\(\):\n    return/);
  assert.match(html,/href="https:\/\/example.test\/article"/);
  assert.equal(noteExcerpt(source),'第一段 观察。');
});

test('raw HTML, executable links and images remain inert text',()=>{
  const html=renderNote('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert(1))\n\n![external](https://example.test/private.png)');
  assert.doesNotMatch(html,/<script\b|<img\b|href="javascript:/i);
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/href="https:\/\/example.test\/private.png"/);
});

test('article outline links are unique, deterministic and refer to rendered headings',()=>{
  const source='## 重试与 `cache`\n\n正文。\n\n### 相同标题\n\n## 相同标题\n\n## !!!\n\n```python\n    return "unchanged"\n```';
  const first=renderNoteDocument(source),again=renderNoteDocument(source);
  assert.deepEqual(first,again);
  assert.equal(new Set(first.outline.map(item=>item.id)).size,4);
  assert.deepEqual(first.outline.map(item=>item.level),[2,3,2,2]);
  assert.equal(first.outline[0].title,'重试与 cache');
  for(const item of first.outline)assert.ok(first.html.includes(`id="${item.id}"`));
  assert.ok(first.html.includes('    return &quot;unchanged&quot;'));
  const unsafe=renderNoteDocument('## <script>alert(1)</script>\n\n[bad](javascript:alert(1))');
  assert.doesNotMatch(unsafe.html,/<script\b|href="javascript:/i);
});
