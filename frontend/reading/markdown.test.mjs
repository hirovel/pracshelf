import {test} from 'node:test';
import assert from 'node:assert/strict';
import {renderNote,noteExcerpt} from './markdown.mjs';

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
