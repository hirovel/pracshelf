"""Isolated UI fixture. No actual LLM calls; every result is labeled demo.

python -m uvicorn translation_demo:app --app-dir examples --port 8768
"""

import json
import os
from pathlib import Path

import httpx
from fastapi.responses import PlainTextResponse

from learning_library.pipeline import Pipeline
from learning_library.storage import Store
from learning_library.web import create_app

ROOT = Path(__file__).resolve().parents[1] / "data" / "translation-demo"
URL = "http://127.0.0.1:8768/demo-source"
SOURCE = """# A synthetic example for translation

This is demonstration text, not a research finding. A successful extraction does not prove that the saved article is complete.

Only after comparing the beginning, ending and key passages can a reader record their judgment. If the publication date is unknown, keep it unknown rather than guessing.

## Keep the example unchanged

```python
def save_note(text):
    return {"note": text, "reviewed": False}
```

The example does not automatically approve an article. Use `--safe` when following the example at https://example.com/guide.

This method may help with reading, but it does not establish factual accuracy or current applicability.
"""
FIRST = """# 翻译功能的合成示例

这是演示文字，不是研究结论。成功提取正文，并不能证明保存的文章完整。

读者只有对照开头、结尾和关键段落后，才能记录自己的判断。如果发布日期未知，就保留未知，不要猜测。

## 原样保留示例"""
LAST = """这个示例不会自动批准文章。按 ⟦KEEP1⟧ 中的示例操作时，使用 ⟦KEEP0⟧。

这种方法可能有助于阅读，但不能证明事实准确，也不能证明内容目前仍然适用。"""


def model_response(request):
    prompt = json.loads(request.content)["messages"][-1]["content"]
    return httpx.Response(
        200,
        json={
            "model": "synthetic-fixture-not-deepseek",
            "usage": None,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": FIRST if prompt.startswith("# A synthetic") else LAST},
                }
            ],
        },
    )


# Only this isolated demo process receives a dummy key and a mocked transport.
os.environ["DEEPSEEK_API_KEY"] = "demo-not-a-real-key"
os.environ["DEEPSEEK_MODEL"] = "synthetic-fixture"
source_transport = httpx.MockTransport(
    lambda r: (
        httpx.Response(200, text=SOURCE, headers={"Content-Type": "text/markdown; charset=utf-8"})
        if str(r.url) == URL
        else httpx.Response(404)
    )
)
store = Store(ROOT)
with httpx.Client(transport=source_transport) as client:
    flow = Pipeline(store, client, review_origin="demo")
    if not flow.list():
        run = flow.create(URL)
        for stage in ("fetch", "extract", "validate"):
            flow.run(run["id"], stage)
store.close()
app = create_app(
    ROOT,
    source_transport,
    review_origin="demo",
    translation_transport=httpx.MockTransport(model_response),
)


@app.get("/demo-source", response_class=PlainTextResponse)
def original():
    return SOURCE
