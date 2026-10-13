"""Offline synthetic product demo. No credentials, live fetches or model calls.

Run on 8770; records live only in data/curation-demo and are labelled demo.
"""

from pathlib import Path

import httpx
from fastapi.responses import PlainTextResponse

from learning_library.curation import Curation, CurationInput
from learning_library.pipeline import Pipeline
from learning_library.storage import Store
from learning_library.web import create_app

ROOT = Path(__file__).resolve().parents[1] / "data" / "curation-demo"
SOURCES = {
    "skill": """# 演示：一个用于核对原文的 Skill

这是一份合成材料，用来演示资料整理功能。它不是已验证的方法，也不代表真实作者观点。

## 任务

核对保存的正文是否保留了来源网页中的主要段落、列表与代码。先读原文，再检查输出。

## 检查

程序检查通过不等于内容正确。缺少证据时，保留未知，不要替使用者填写通过。

```text
输入：原文与保存副本
输出：遗漏位置、原文依据、待核对事项
```

使用条件：本例仅作软件演示，不能作为实际效果证据。
""",
    "workflow": """# 演示：记录一次工作流试用

这是一份合成材料。记录的目的是演示如何区分方法介绍与自己的使用经验。
先写清楚任务与环境，再记做了什么、观察到了什么，以及哪些结论仍没有依据。

本演示没有真实试用。请在页面上练习填写记录、保存、刷新并查看历史。
原文版本与笔记分别保存，修改笔记不会改变这段正文。
""",
    "announcement": """# 演示：没有操作细节的产品公告

本页是合成的产品公告样本，用来测试归档状态和恢复操作，不是真实产品发布。
页面只介绍一个假想工具的新功能，没有给出完整提示词、配置、使用过程或者评估条件。
因此，这条演示记录暂时归档。归档不删除原文，也不妨碍以后重新阅读和修改决定。
""",
}


def response(request):
    body = SOURCES.get(request.url.path.rsplit("/", 1)[-1])
    return httpx.Response(
        200 if body else 404,
        text=body or "Demo source not found",
        headers={"Content-Type": "text/markdown; charset=utf-8"},
    )


transport = httpx.MockTransport(response)
store = Store(ROOT)
with httpx.Client(transport=transport) as client:
    flow = Pipeline(store, client, review_origin="demo")
    records = Curation(store, origin="demo")
    if not flow.list():
        for kind in SOURCES:
            run = flow.create("http://127.0.0.1:8770/demo-source/" + kind)
            for stage in ("fetch", "extract"):
                flow.run(run["id"], stage)
            state = records.inspect(run["id"])
            if kind in {"skill", "announcement"}:
                records.record(
                    run["id"],
                    CurationInput(
                        submission_id="demo-seed",
                        previous_id=None,
                        version_id=state["version_id"],
                        category="skill" if kind == "skill" else "knowledge",
                        decision="kept" if kind == "skill" else "archived",
                        purpose="演示：查看完整指令和使用条件。"
                        if kind == "skill"
                        else "演示：只有公告，没有操作细节。",
                        note="演示笔记，不是用户本人判断。",
                    ),
                )
store.close()
app = create_app(ROOT, transport, review_origin="demo")


@app.get("/demo-source/{name}")
def source(name: str):
    return PlainTextResponse(SOURCES.get(name, "Demo source not found"), media_type="text/markdown")
