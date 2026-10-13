"""Local synthetic acceptance demo, isolated from data/library.sqlite3.

Run: python -m uvicorn review_demo:app --app-dir examples --port 8767
All review records in this application are server-labeled demo.
"""

from pathlib import Path

import httpx
from fastapi.responses import HTMLResponse

from learning_library.pipeline import Pipeline
from learning_library.review import ReviewInput
from learning_library.storage import Store
from learning_library.web import create_app

ROOT = Path(__file__).resolve().parents[1] / "data" / "review-demo"
URL = "http://127.0.0.1:8767/demo-source"
HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>演示文章：如何核对保存的原文</title></head><body><article>
<h1>演示文章：如何核对保存的原文</h1>
<p>这是一篇合成文章，仅用于验证人工验收功能。它不是研究资料，也不代表任何真实作者的观点。
验收时，应检查原文的开头、结尾和关键段落是否保留，代码缩进、列表和表格是否发生变化。
程序可以检查是否提取出文字，但你的阅读判断需要单独保存。日期没有明确依据时应保留未知，
不能为了通过验收而填写一个猜测日期。人工验收通过也不证明文章中的观点正确或方法有效。</p>
<h2>检查步骤</h2><ul><li>查看来源与保存正文。</li><li>核对代码与表格。</li><li>记录结论与待处理问题。</li></ul>
<pre>def example():
    return "synthetic fixture"
</pre>
<table><tr><th>内容</th><th>检查重点</th></tr><tr><td>代码</td><td>缩进与完整性</td></tr><tr><td>日期</td><td>明确依据</td></tr></table>
<p>演示文章到此结束。本页未声明发布日期。</p>
</article></body></html>"""


def response(request):
    return (
        httpx.Response(200, text=HTML, headers={"Content-Type": "text/html; charset=utf-8"})
        if str(request.url) == URL
        else httpx.Response(404, text="Demo only")
    )


transport = httpx.MockTransport(response)
store = Store(ROOT)
with httpx.Client(transport=transport) as client:
    flow = Pipeline(store, client, review_origin="demo")
    if not flow.list():
        run = flow.create(URL)
        for stage in ("fetch", "extract", "validate"):
            flow.run(run["id"], stage)
        state = flow.reviews.inspect(run["id"])
        payload = ReviewInput(
            submission_id="demo-initial",
            version_id=state["version_id"],
            extract_attempt_id=state["extract_attempt_id"],
            previous_review_id=None,
            verdict="pending",
            body="matched",
            structure="unreviewed",
            date_status="unknown",
            note="演示：正文已核对，表格和代码尚未核对。",
        )
        saved = flow.reviews.record(run["id"], payload)
        flow.reviews.record(
            run["id"],
            ReviewInput.model_validate(
                payload.model_dump()
                | {
                    "submission_id": "demo-accepted",
                    "previous_review_id": saved["latest"]["id"],
                    "verdict": "accepted",
                    "structure": "matched",
                    "note": "演示：补查了列表、代码与表格；发布日期仍未知。",
                }
            ),
        )
store.close()
app = create_app(ROOT, transport, review_origin="demo")


@app.get("/demo-source", response_class=HTMLResponse)
def original():
    return HTML
