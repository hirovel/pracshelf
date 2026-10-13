"""Synthetic HTTP cases; never use the real key or write formal human records."""

import json
import sqlite3
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient

from learning_library.article_insights import ArticleInsights, prepare
from learning_library.pipeline import Pipeline
from learning_library.review import ReviewConflict
from learning_library.storage import Store
from learning_library.web import create_app

SOURCE = "# Synthetic AI practice\n\nKeep the original\nsource and check conditions.\n\n```python\nif ready:\n    run_task()\n```\n\n" + "Explain a concrete engineering choice. " * 220
QUOTE = "Keep the original source and check conditions."


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "synthetic-model")
    calls = []
    body = {
        "category": "workflow", "recommendation": "read", "reason": "合成实践资料，供行为测试。",
        "reading_kind": "practice", "reading_reason": "合成实际做法，供分类测试。",
        "reading_evidence": {"quote": QUOTE, "supports": "合成实践依据"},
        "core": "合成核心内容，不作为实际文章分析。", "evidence": [{"quote": QUOTE, "supports": "原文条件"}],
        "points": [{"text": "保留原始资料。", "claim_type": "author_judgment", "evidence": [{"quote": QUOTE, "supports": "原文条件"}]}],
        "conditions": [], "time_dependencies": [], "unknowns": ["这是合成测试，不证明真实质量。"],
    }
    mode = {"failure": None, "source": SOURCE}

    def model(request):
        calls.append(json.loads(request.content))
        if mode["failure"] == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        content = json.dumps(body, ensure_ascii=False)
        finish = "stop"
        if mode["failure"] == "truncated":
            content, finish = content[:-1], "length"
        if mode["failure"] == "schema":
            content = '{"recommendation":"read"}'
        return httpx.Response(200, json={
            "id": "synthetic-response", "model": "synthetic-provider-model",
            "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
            "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}],
        })

    with TestClient(create_app(tmp_path, httpx.MockTransport(lambda r: httpx.Response(
        200, text=mode["source"], headers={"Content-Type": "text/markdown"},
    )), translation_transport=httpx.MockTransport(model))) as client:
        run = client.post("/api/runs", json={"url": "https://example.com/practice.md"}).json()
        base = "/api/runs/" + run["id"]
        assert client.post(base + "/stages/fetch").status_code == 200
        assert client.post(base + "/stages/extract").status_code == 200
        yield client, base, run["id"], calls, body, mode, tmp_path


def frozen(root):
    with closing(sqlite3.connect(root / "library.sqlite3")) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name: db.execute("SELECT * FROM " + name + " ORDER BY rowid").fetchall() for name in (
            "versions", "requests", "candidate_screenings", "translations", "translation_attempts",
            "processing_results", "processing_reviews", "curation_records", "original_reviews",
        ) if name in names}


def test_one_full_call_reuse_and_no_other_stage_or_human_write(setup):
    client, base, run, calls, _, _, root = setup
    # Construct every read Module before taking the comparison snapshot.
    client.get("/api/workflows/" + run)
    before = frozen(root)
    source = client.get(base + "/artifacts/document").json()["document"]["text"]
    pending = client.get(base + "/insights").json()
    assert pending["input"]["checked_characters"] == len(source)
    assert pending["input"]["checked_characters"] > 6000
    assert not calls
    first = client.post(base + "/insights", json={"submission_id": "once"})
    assert first.status_code == 202, first.text
    assert first.json()["launch"]
    result = client.get(base + "/insights").json()
    output = result["latest"]["output"]
    assert output["core_available"] and output["effective_recommendation"] == "read"
    assert output["quote_alignment"] == "passed"
    assert output["evidence_checks"][0]["match_type"] == "layout_whitespace"
    assert output["evidence_checks"][0]["source_quote"] == "Keep the original\nsource and check conditions."
    assert len(calls) == result["latest"]["response"]["request_count"] == 1
    assert json.loads(calls[0]["messages"][1]["content"])["source"]["text"] == source
    assert result["latest"]["response"]["requests"][0]["body"] == calls[0]
    assert output["usage"]["total_tokens"] == 180
    for submission in ("once", "another-click"):
        reply = client.post(base + "/insights", json={"submission_id": submission}).json()
        assert reply["id"] == first.json()["id"] and not reply["launch"]
    assert len(client.get(base + "/insights").json()["history"]) == 1
    view = client.get("/api/workflows/" + run).json()
    assert view["steps"][1]["title"] == "核心提取"
    assert view["steps"][1]["output"] == output
    assert view["steps"][3]["label"] == "可阅读"
    assert view["steps"][2]["status"] == "not_started"
    assert "synthetic-key" not in json.dumps(view)
    assert len(calls) == 1
    assert frozen(root) == before


@pytest.mark.parametrize("case", ["missing_quote", "code_indent"])
def test_bad_quotes_preserved_but_core_not_presented(setup, case):
    client, base, run, calls, body, _, _ = setup
    bad = "This quote never occurs in the source." if case == "missing_quote" else "if ready:\n  run_task()"
    body["points"][0]["evidence"][0]["quote"] = bad
    client.post(base + "/insights", json={"submission_id": "bad-evidence"})
    result = client.get(base + "/insights").json()["latest"]
    assert result["status"] == "completed"
    assert result["output"]["judgment"]["core"] == body["core"]
    assert result["output"]["effective_recommendation"] == "uncertain"
    assert not result["output"]["core_available"]
    assert json.loads(result["response"]["body"])["choices"][0]["message"]["content"] == json.dumps(body, ensure_ascii=False)
    view = client.get("/api/workflows/" + run).json()
    assert view["steps"][1]["status"] == "stopped"
    assert len(calls) == 1


def test_skip_has_reason_without_forced_core(setup):
    client, base, _, calls, body, _, _ = setup
    body.update(recommendation="skip", core="", points=[], reason="合成公告，没有展开实践方法。")
    client.post(base + "/insights", json={"submission_id": "skip"})
    output = client.get(base + "/insights").json()["latest"]["output"]
    assert output["effective_recommendation"] == "skip" and not output["core_available"]
    assert output["judgment"]["core"] == ""
    assert len(calls) == 1


def test_reader_combines_saved_core_without_translation_or_extra_calls(setup):
    client, base, run, calls, _, _, _ = setup
    before = client.get('/api/library/' + run).json()
    assert not before['has_translation'] and '<h1>Synthetic AI practice</h1>' in before['source_html']
    client.post(base + '/insights', json={'submission_id': 'readable-core'})
    for _ in range(2):
        article = client.get('/api/library/' + run).json()
        assert article['insights']['version_id'] == article['version_id']
        assert article['insights']['output']['judgment']['core'] == '合成核心内容，不作为实际文章分析。'
        assert article['insights']['output']['semantic_fidelity'] == 'unverified'
        assert article['insights']['request_count'] == 1
        assert article['insights']['observation'] == client.get('/api/workflows/'+run+'?detail=summary').json()['insights']['latest']['observation']
        assert article['insights']['observation']['tokens']['input'] == 120
        assert article['insights']['observation']['tokens']['cached_input'] is None
        assert not article['has_translation'] and article['pairs'] == []
        assert article['note'] == '' and article['review_note'] == ''
        assert article['classification']['kind'] == 'practice'
        assert article['classification']['origin'] == 'model_api'
        assert article['classification']['insights_id'] == article['insights']['id']
    assert len(calls) == 1
    assert client.get(base + '/curation').json()['history'] == []
    assert client.get(base + '/reviews').json()['history'] == []


def test_old_topic_only_output_stays_unclassified_without_another_call(setup):
    client, base, run, calls, body, _, _ = setup
    for key in ('reading_kind', 'reading_reason', 'reading_evidence'):
        body.pop(key)
    client.post(base + '/insights', json={'submission_id': 'legacy-output'})
    for _ in range(2):
        assert client.get('/api/library/' + run).json()['classification']['kind'] == 'unclassified'
        assert client.get('/api/library').json()[0]['classification']['kind'] == 'unclassified'
    assert len(calls) == 1


def test_classification_with_missing_source_quote_stays_unclassified(setup):
    client, base, run, calls, body, _, _ = setup
    body['reading_evidence']['quote'] = 'This classification evidence never occurs in the source.'
    client.post(base + '/insights', json={'submission_id': 'bad-classification'})
    result = client.get(base + '/insights').json()['latest']
    assert result['output']['judgment']['reading_kind'] == 'practice'
    assert result['output']['quote_alignment'] == 'failed'
    assert client.get('/api/library/' + run).json()['classification']['kind'] == 'unclassified'
    assert len(calls) == 1


def test_classification_prompt_upgrade_keeps_old_core_and_does_not_trigger_another_call(setup):
    client, base, run, calls, _, _, root = setup
    client.post(base + '/insights', json={'submission_id': 'existing-core'})
    first = client.get(base + '/insights').json()['latest']
    # Simulate the frozen v1 prompt record in this isolated database, not real history.
    incoming = dict(first['input'])
    incoming['prompt_version'] = 'article-insights-zh-v1'
    incoming['cache_key'] = 'synthetic-old-key'
    incoming['request']['messages'][0]['content'] = 'Synthetic old system instructions'
    with closing(sqlite3.connect(root / 'library.sqlite3')) as db, db:
        db.execute('UPDATE article_insights SET input=? WHERE id=?', (json.dumps(incoming), first['id']))
    state = client.get(base + '/insights').json()
    assert state['latest']['id'] == first['id'] and not state['latest']['current']
    assert state['action'] is None and '不会重跑' in state['reason']
    retry = client.post(base + '/insights', json={'submission_id': 'classification-only'}).json()
    assert retry['id'] == first['id'] and not retry['launch']
    view = client.get('/api/workflows/' + run).json()
    assert view['steps'][1]['title'] == '核心提取' and view['steps'][1]['status'] == 'completed'
    assert len(calls) == 1


def test_new_source_reader_does_not_inherit_old_core(setup, monkeypatch):
    client, base, run, calls, _, mode, _ = setup
    client.post(base + '/insights', json={'submission_id': 'old-source'})
    old = client.get('/api/library/' + run).json()
    monkeypatch.setenv('DEEPSEEK_MODEL', 'changed-model-settings')
    assert client.get('/api/library/' + run).json()['insights']['id'] == old['insights']['id']
    mode['source'] = SOURCE + '\nThe author added a new restriction.\n'
    updated = client.post('/api/runs', json={'url': 'https://example.com/practice.md'}).json()
    client.post('/api/runs/' + updated['id'] + '/stages/fetch')
    client.post('/api/runs/' + updated['id'] + '/stages/extract')
    new = client.get('/api/library/' + updated['id']).json()
    assert new['version_id'] != old['version_id'] and new['insights'] is None
    assert 'The author added a new restriction.' in new['source_html']
    assert client.get('/api/library/' + run).json()['insights']['output'] == old['insights']['output']
    assert len(calls) == 1


def test_high_selection_reuses_existing_medium_core_without_a_model_task(setup, monkeypatch):
    from learning_library import article_insights
    client, base, _, _, body, _, _ = setup
    monkeypatch.setenv('LIBRARY_MODEL_PROVIDER', 'codex')
    monkeypatch.setenv('LIBRARY_CODEX_EFFORT', 'medium')
    calls = []

    def model(request, schema, **kwargs):
        calls.append(request)
        return {'response': {'provider':'codex','terminal_status':'completed'},
                'output':schema.model_validate(body).model_dump(mode='json'), 'error':None}

    monkeypatch.setattr(article_insights, 'call_json', model)
    first = client.post(base+'/insights', json={'submission_id':'medium-core'}).json()
    assert len(calls) == 1 and calls[0]['effort'] == 'medium'
    monkeypatch.setenv('LIBRARY_CODEX_EFFORT', 'high')
    state = client.get(base+'/insights').json()
    assert state['input']['request']['effort'] == 'high'
    assert state['latest']['input']['request']['effort'] == 'medium'
    assert state['action'] is None
    reused = client.post(base+'/insights', json={'submission_id':'high-selection'}).json()
    assert reused['id'] == first['id'] and reused['reused'] and not reused['launch']
    assert len(calls) == 1 and len(client.get(base+'/insights').json()['history']) == 1


@pytest.mark.parametrize("failure", ["timeout", "truncated", "schema"])
def test_failure_preserved_and_only_explicit_new_submission_retries(setup, failure):
    client, base, _, calls, _, mode, _ = setup
    mode["failure"] = failure
    client.post(base + "/insights", json={"submission_id": "failed"})
    failed = client.get(base + "/insights").json()
    assert failed["latest"]["status"] == "failed" and failed["action"] == "retry"
    assert failed["latest"]["response"]["request_count"] == len(calls) == 1
    if failure != "timeout":
        assert failed["latest"]["response"]["body"]
        assert failed["latest"]["response"]["usage"]["total_tokens"] == 180
    assert not client.post(base + "/insights", json={"submission_id": "failed"}).json()["launch"]
    assert len(calls) == 1
    mode["failure"] = None
    client.post(base + "/insights", json={"submission_id": "retry"})
    history = client.get(base + "/insights").json()["history"]
    assert [r["status"] for r in history] == ["failed", "completed"]
    assert history[0] == failed["history"][0]
    assert len(calls) == 2


def test_model_change_keeps_same_version_readable_and_new_source_requires_new_result(setup, monkeypatch):
    client, base, _, calls, _, mode, _ = setup
    client.post(base + "/insights", json={"submission_id": "v1"})
    first = client.get(base + "/insights").json()["latest"]
    original = client.get(base + "/artifacts/document").json()
    monkeypatch.setenv("DEEPSEEK_MODEL", "different-model")
    kept = client.get(base + "/insights").json()
    assert kept["latest"]["id"] == first["id"] and kept["action"] is None
    assert client.post(base + "/insights", json={"submission_id": "model-change"}).json()["reused"]
    assert len(calls) == 1
    mode["source"] = SOURCE + "\nNew source content.\n"
    updated = client.post("/api/runs", json={"url": "https://example.com/practice.md"}).json()
    updated_base = "/api/runs/" + updated["id"]
    client.post(updated_base + "/stages/fetch")
    client.post(updated_base + "/stages/extract")
    assert client.get(updated_base + "/insights").json()["latest"] is None
    client.post(updated_base + "/insights", json={"submission_id": "source-change"})
    assert len(calls) == 2 and calls[-1]["model"] == "different-model"
    new_doc = client.get(updated_base + "/artifacts/document").json()
    assert json.loads(calls[-1]["messages"][1]["content"])["source"]["text"] == new_doc["document"]["text"]
    assert new_doc["id"] != first["input"]["version_id"]
    assert client.get(base + "/artifacts/document").json() == original
    old = client.get(base + "/insights").json()["history"][0]
    assert {k: v for k, v in old.items() if k != "current"} == {k: v for k, v in first.items() if k != "current"}


def test_missing_key_prevents_submission_without_any_request(setup, monkeypatch):
    client, base, _, calls, _, _, _ = setup
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    state = client.get(base + "/insights").json()
    assert state["action"] is None
    assert client.post(base + "/insights", json={"submission_id": "no-key"}).status_code == 400
    assert client.get(base + "/insights").json()["history"] == []
    assert not calls


def test_queued_claim_duplicate_execution_and_source_change_before_call(setup):
    _, _, run, calls, _, _, root = setup
    with closing(Store(root)) as store, httpx.Client() as client:
        module = ArticleInsights(Pipeline(store, client), transport=httpx.MockTransport(lambda r: pytest.fail("must not call")))
        queued = module.begin(run, "queued")
        assert not module.begin(run, "queued")["launch"]
        with pytest.raises(ReviewConflict, match="进行中"):
            module.begin(run, "second")
        # A newly extracted source invalidates a queued task. Frozen settings do not.
        version = store.version(module.inspect(run)["input"]["version_id"])
        new_document = version["document"] | {"text": "Different saved original."}
        store.save(version["document_key"], new_document, None)
        new_version = store.documents()[0]["version_id"]
        latest = store.db.execute("SELECT id,output FROM pipeline_attempts WHERE pipeline_id=? AND stage='extract' ORDER BY id DESC", (run,)).fetchone()
        outgoing = json.loads(latest["output"]) | {"version_ids": [new_version]}
        with store.db:
            store.db.execute("UPDATE pipeline_attempts SET output=? WHERE id=?", (json.dumps(outgoing), latest["id"]))
        module.execute(queued["id"])
        module.execute(queued["id"])
        attempt = module.inspect(run)["history"][-1]
        assert attempt["status"] == "failed" and attempt["response"]["request_count"] == 0
    assert not calls


def test_source_limit_rejects_without_truncating():
    with pytest.raises(ValueError, match="未截断"):
        prepare({"id": 1, "content_hash": "fixture", "document": {
            "kind": "article", "title": "fixture", "url": "https://example.com", "text": "x" * 60001,
        }}, "goal", "model")


def test_summary_reads_preserve_results_and_lazy_details_without_model_work(setup):
    client, base, run, calls, _, _, root = setup
    client.post(base + "/insights", json={"submission_id": "synthetic-only"})
    full = client.get(base + "/insights").json()
    identifier = full["latest"]["id"]
    # Synthetic large raw evidence exercises the status projection, not a provider.
    with sqlite3.connect(root / "library.sqlite3") as db:
        response = full["latest"]["response"] | {"events": [{"delta": "x" * 100_000}]}
        db.execute("UPDATE article_insights SET response=? WHERE id=?", (json.dumps(response), identifier))
    client.get("/api/workflows/" + run)  # Construct the read Modules before taking a snapshot.
    with sqlite3.connect(root / "library.sqlite3") as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in tables}
    for _ in range(2):
        summary = client.get("/api/workflows/" + run + "?detail=summary")
        assert summary.status_code == 200
        value = summary.json()["insights"]
        assert value["read"]["model_tasks_started"] == 0
        assert value["read"]["saved_result_id"] == identifier
        assert value["latest"]["output"] == full["latest"]["output"]
        assert value["latest"]["observation"]["tokens"]["total"] == 180
        assert value["latest"]["observation"]["tokens"]["cached_input"] is None
        assert '"events":' not in summary.text and '"messages":' not in summary.text
    detail = client.get(base + "/insights/" + str(identifier))
    assert detail.json()["input"] == full["latest"]["input"]
    assert detail.json()["response"] == response
    assert len(summary.content) < len(client.get("/api/workflows/" + run).content) / 2
    other = client.post("/api/runs", json={"url": "https://example.com/other.md"}).json()["id"]
    assert client.get(f"/api/runs/{other}/insights/{identifier}").status_code == 400
    assert client.get(base + "/insights/99999").status_code == 400
    assert client.get(base + "/insights?detail=unknown").status_code == 422
    # Ignore only the second run inserted explicitly by this test.
    with sqlite3.connect(root / "library.sqlite3") as db:
        after = {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in tables if name != "pipelines"}
    assert after == {name: rows for name, rows in before.items() if name != "pipelines"}
    assert len(calls) == 1
