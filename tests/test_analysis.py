"""Exercise the same prepare/import/review interface exposed by the CLI."""

import json

import pytest

from learning_library.analysis import Analysis
from learning_library.storage import Store


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path)
    run = store.start("article", "https://example.com/practice", {})
    request = store.request(run, "https://example.com/practice", body=b"synthetic HTML", status=200)
    doc = {
        "kind": "article",
        "title": "Synthetic practice",
        "url": "https://example.com/practice",
        "text": "For my small projects, I use one agent.\n\nLarger changes need tests.",
        "completeness": "html_saved; extracted_text_requires_review",
        "published": "2026-01-01",
    }
    store.save("article:test", doc, request)
    yield store, Analysis(store), store.documents()[0]["version_id"]
    store.close()


def response(quote="For my small projects, I use one agent.", paragraph_id="p0001"):
    return json.dumps(
        {
            "reading_guide": {
                "text": "这篇合成文章讨论个人在小项目中的 Agent 使用经验，并指出大改动需要测试。",
                "evidence": [
                    {"paragraph_id": "p0001", "quote": "For my small projects, I use one agent."}
                ],
            },
            "time_dependencies": [],
            "points": [
                {
                    "text": "所有项目都应该只用一个 Agent。",
                    "claim_type": "author_judgment",
                    "evidence": [{"paragraph_id": paragraph_id, "quote": quote}],
                }
            ],
            "no_points_reason": "",
        }
    ).encode()


def test_matching_quote_does_not_approve_unfaithful_meaning(library):
    store, analysis, version = library
    prepared = analysis.prepare(version)
    result = analysis.accept(prepared["analysis_id"], response(), origin="demo")
    assert result["status"] == "needs_review"
    assert result["semantic_status"] == "not_reviewed"
    citation = result["points"][0]["citation_checks"][0]
    span = citation["spans"][0]
    text = store.version(version)["document"]["text"]
    assert text[span["start"] : span["end"]] == citation["quote"]
    analysis.review(
        prepared["analysis_id"],
        result["attempt_id"],
        1,
        "needs_revision",
        "测试中的人工判断：丢失了个人经验和小项目的限定条件。",
    )
    inspected = analysis.inspect(prepared["analysis_id"], result["attempt_id"])
    assert inspected["semantic"] == "needs_revision"
    assert inspected["evidence"] == "matched"
    assert inspected["generation"]["origin"] == "demo"
    assert inspected["generation"]["model_call_observed"] is False


@pytest.mark.parametrize(
    "quote,paragraph", [("Invented quote", "p0001"), ("Larger changes need tests.", "p9999")]
)
def test_bad_evidence_cannot_be_accepted_as_supported(library, quote, paragraph):
    _, analysis, version = library
    analysis_id = analysis.prepare(version)["analysis_id"]
    result = analysis.accept(analysis_id, response(quote, paragraph), origin="demo")
    assert result["status"] == "invalid_evidence"
    with pytest.raises(ValueError, match="unmatched"):
        analysis.review(analysis_id, result["attempt_id"], 1, "supported", "Should be rejected")


def test_preparation_pins_version_and_does_not_follow_later_source_edits(library):
    store, analysis, version = library
    analysis_id = analysis.prepare(version)["analysis_id"]
    old_doc = store.version(version)["document"]
    run = store.start("article", old_doc["url"], {})
    request = store.request(run, old_doc["url"], body=b"new synthetic HTML", status=200)
    store.save("article:test", old_doc | {"text": "A completely different article."}, request)
    result = analysis.accept(analysis_id, response(), origin="demo")
    assert result["status"] == "needs_review"
    assert result["version_id"] == version
    assert store.documents()[0]["version_id"] != version


@pytest.mark.parametrize(
    "raw", [b"not JSON", b'{"points":[]}', b'{"points":{},"no_points_reason":""}']
)
def test_invalid_outputs_remain_observable(library, raw):
    store, analysis, version = library
    analysis_id = analysis.prepare(version)["analysis_id"]
    result = analysis.accept(analysis_id, raw, origin="demo")
    assert result["status"] == "invalid_output"
    attempt = store.root / "analysis" / analysis_id / "attempts" / result["attempt_id"]
    assert (attempt / "response.txt").read_bytes() == raw
    assert result["error"]
    assert analysis.inspect(analysis_id, result["attempt_id"])["format"] == "invalid"


def test_new_attempts_keep_previous_output_and_never_change_human_notes(library):
    store, analysis, version = library
    note = store.root / "human-notes" / "own.md"
    note.write_text("我自己写的短总结", encoding="utf-8")
    analysis_id = analysis.prepare(version)["analysis_id"]
    first = analysis.accept(
        analysis_id, response(), origin="external_model", model="synthetic-test"
    )
    second = analysis.accept(analysis_id, response(), origin="human_edited")
    assert first["attempt_id"] != second["attempt_id"]
    assert len(analysis.inspect(analysis_id)["attempt_ids"]) == 2
    assert note.read_text(encoding="utf-8") == "我自己写的短总结"
    assert first["origin"] == "external_model"
    assert second["origin"] == "human_edited"


def test_feed_candidates_and_modified_inputs_are_rejected(library):
    store, analysis, version = library
    run = store.start("feed", "https://example.com/feed", {})
    request = store.request(run, "https://example.com/feed", body=b"synthetic feed", status=200)
    store.save(
        "candidate:test", store.version(version)["document"] | {"kind": "feed_candidate"}, request
    )
    with pytest.raises(ValueError, match="candidates"):
        analysis.prepare(store.documents()[0]["version_id"])
    prepared = analysis.prepare(version)
    prompt = store.root / "analysis" / prepared["analysis_id"] / "prompt.txt"
    prompt.write_text("Modified prompt", encoding="utf-8")
    with pytest.raises(ValueError, match="modified"):
        analysis.accept(prepared["analysis_id"], response(), origin="demo")


def test_empty_extraction_requires_reason_and_remains_unreviewed(library):
    _, analysis, version = library
    analysis_id = analysis.prepare(version)["analysis_id"]
    payload = json.loads(response())
    payload.update(points=[], no_points_reason="测试样本没有额外可提取的论断。")
    raw = json.dumps(payload).encode()
    result = analysis.accept(analysis_id, raw, origin="demo")
    assert result["status"] == "needs_review"
    assert analysis.inspect(analysis_id, result["attempt_id"])["semantic"] == "not_reviewed"


def test_article_view_includes_full_source_and_never_infers_current_validity(library):
    store, analysis, version = library
    prepared = analysis.prepare(version)
    result = analysis.accept(prepared["analysis_id"], response(), origin="demo")
    analysis.review(
        prepared["analysis_id"],
        result["attempt_id"],
        0,
        "supported",
        "Synthetic review of faithful guide",
    )
    inspected = analysis.inspect(prepared["analysis_id"], result["attempt_id"])
    assert inspected["reading_guide_semantics"] == "supported"
    assert inspected["current_applicability"] == "unverified"
    assert inspected["recommendation_eligible"] is False
    from pathlib import Path

    report = Path(result["report"]).read_text(encoding="utf-8")
    assert store.version(version)["document"]["text"] in report
    assert report.index("## 原文") < report.index("## 阅读导引")


def test_v2_rejects_isolated_tips_without_an_article_guide(library):
    _, analysis, version = library
    analysis_id = analysis.prepare(version)["analysis_id"]
    payload = json.loads(response())
    del payload["reading_guide"]
    result = analysis.accept(analysis_id, json.dumps(payload).encode(), origin="demo")
    assert result["status"] == "invalid_output"
