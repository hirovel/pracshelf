"""Regression from Codemode: multiline link labels are valid Markdown."""

import json
from contextlib import closing

import httpx

from learning_library.pipeline import Pipeline
from learning_library.processing import ProcessingResults, check_result
from learning_library.storage import Store


def test_layout_changes_preserve_parsed_links_but_broken_or_changed_links_fail():
    source = [{"kind": "text", "source": "[Original\nlabel](https://example.com/doc) and [second](https://example.com/other)"}]
    translated = [{"index": 0, "text": "[中文标签](https://example.com/doc) 和 [另一个](https://example.com/other)"}]
    assert check_result(source, translated)["status"] == "passed"
    for broken in (
        translated[0]["text"].replace("/doc)", "/doc）"),
        translated[0]["text"].replace("/doc)", "/different)"),
        "https://example.com/doc 和 [另一个](https://example.com/other)",
    ):
        checks = check_result(source, [{"index": 0, "text": broken}])
        assert "markdown_links" in {i["code"] for i in checks["issues"]}


def test_local_recheck_keeps_old_checks_and_result_immutable(tmp_path):
    # Use the public receive/inspect Interface with a minimal real saved original.
    source = "# Synthetic\n\n[Original\nlabel](https://example.com/doc)\n\n" + "Explanation. " * 20
    with closing(Store(tmp_path)) as store, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=source, headers={"Content-Type": "text/markdown"}))) as client:
        pipeline = Pipeline(store, client)
        run = pipeline.create("https://example.com/synthetic.md")["id"]
        pipeline.run(run, "fetch")
        pipeline.run(run, "extract")
        results = ProcessingResults(store)
        task = results.task(run)
        text = source.replace("Original\nlabel", "中文标签")
        results.receive(run, {"task_key": task["task_key"], "submission_id": "synthetic",
                             "origin": "demo", "executor": "Test fixture", "segments": [{"index": 0, "text": text}]})
        legacy = {"status": "issues", "issues": [{"code": "markdown_links", "index": 0, "message": "Legacy regex false positive"}]}
        with store.db:
            store.db.execute("UPDATE processing_results SET checks=?", (json.dumps(legacy),))
        before = tuple(store.db.execute("SELECT * FROM processing_results").fetchone())
        result = results.inspect(run)["results"][0]
        assert result["recorded_checks"] == legacy and result["checks"]["status"] == "passed"
        assert result["reviews"] == []
        assert tuple(store.db.execute("SELECT * FROM processing_results").fetchone()) == before
