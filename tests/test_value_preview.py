import json

import httpx
import pytest

from learning_library.value_preview import Screening, prepare, revalidate, run, validate


def incoming(text):
    return prepare(
        {"id": 1, "content_hash": "fixture-hash", "document": {"title": "Test", "text": text}},
        "学习开发实践",
        "fixture-model",
    )


def response(quote, recommendation="skip"):
    return Screening.model_validate(
        {
            "category": "other",
            "recommendation": recommendation,
            "learning_value": "仅用于测试。",
            "reason": "未看到具体案例。",
            "evidence": [{"quote": quote, "supports": "这是原文片段。"}],
            "conditions": [],
            "time_dependencies": [],
            "unknowns": ["其他内容未检查。"],
        }
    )


def test_sampling_is_bounded_and_records_exact_ranges():
    text = "a" * 2500 + "important unreviewed evidence" + "b" * 9500
    request = incoming(text)
    assert request["source_scope"] == "sampled_saved_text"
    assert request["checked_characters"] == 6000
    data = json.loads(request["request"]["messages"][1]["content"])
    for part in data["excerpts"]:
        assert part["text"] == text[part["start"] : part["end"]]
    assert request["request"]["max_tokens"] == 1200
    assert request["source_characters"] == len(text)
    assert incoming("short original")["source_scope"] == "full_saved_text"


def test_quote_must_be_in_sent_excerpt_not_merely_somewhere_in_full_source():
    text = "a" * 2500 + "important unreviewed evidence" + "b" * 9500
    output = validate(response("important unreviewed evidence", "read"), incoming(text))
    assert output["quote_alignment"] == "failed"
    assert output["effective_recommendation"] == "uncertain"
    assert output["evidence_checks"][0]["start"] == -1


def test_partial_coverage_cannot_justify_rejecting_entire_article():
    output = validate(response("a" * 12), incoming("a" * 12000))
    assert output["quote_alignment"] == "passed"
    assert output["judgment"]["recommendation"] == "skip"
    assert output["effective_recommendation"] == "uncertain"


def test_prose_layout_differences_keep_exact_original_quote_and_offsets():
    text = "# Heading\n\nKeep the original\n  source and `store()` results.\n"
    quote = "Keep the original source and `store()` results."
    output = validate(response(quote, "read"), incoming(text))
    check = output["evidence_checks"][0]
    assert output["quote_alignment"] == "passed"
    assert check["match_type"] == "layout_whitespace"
    assert check["source_quote"] == text[check["start"]:check["end"]]
    assert check["source_quote"] == "Keep the original\n  source and `store()` results."
    assert check["quote"] == quote


@pytest.mark.parametrize("text,quote", [
    ("```python\nif ready:\n    run_task()\n```", "if ready:\n  run_task()"),
    ("Use `echo  hello` carefully.", "Use `echo hello` carefully."),
    ("```python\nreturn a + b\n```", "return a\n+ b"),
])
def test_code_whitespace_changes_are_rejected_even_without_quote_delimiters(text, quote):
    output = validate(response(quote, "read"), incoming(text))
    assert output["quote_alignment"] == "failed"
    assert output["effective_recommendation"] == "uncertain"


def test_sample_beginning_inside_code_cannot_drop_indentation():
    text = "Intro " * 500 + "\n```python\n" + "if ready:\n    run_task()\n" * 180 + "```\n" + "After " * 500
    output = validate(response("if ready:\n  run_task()", "read"), incoming(text))
    assert output["quote_alignment"] == "failed"


def test_saved_result_can_be_rechecked_without_changing_request_or_old_judgment():
    text = "Keep the original\nsource and check conditions."
    request = incoming(text)
    judgment = response("Keep the original source and check conditions.", "read")
    old = {"judgment": judgment.model_dump(), "quote_alignment": "failed",
           "effective_recommendation": "uncertain", "usage": {"total_tokens": 77}}
    checked = revalidate(request, old, text)
    assert checked["quote_alignment"] == "passed"
    assert checked["effective_recommendation"] == "read"
    assert checked["usage"] == old["usage"]
    assert old["quote_alignment"] == "failed"
    assert json.loads(request["request"]["messages"][1]["content"])["excerpts"][0]["text"] == text


def completion(content, *, finish_reason="stop"):
    return {
        "id": "fixture-response",
        "model": "fixture-provider-model",
        "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
            }
        ],
    }


def test_langchain_wire_request_structured_result_and_redacted_trace():
    calls = []
    text = "Source evidence is preserved."
    request = incoming(text)
    judgment = response(text, "read")
    judgment.learning_value = "fixture-api-key"
    body = completion(judgment.model_dump_json())
    body["provider_extra"] = "fixture-api-key"  # Provider extensions must survive, redacted.

    def respond(req):
        calls.append(json.loads(req.content))
        assert str(req.url) == "https://api.deepseek.com/chat/completions"
        return httpx.Response(200, json=body)

    result = run(request, api_key="fixture-api-key", transport=httpx.MockTransport(respond))
    assert result["error"] is None
    assert len(calls) == result["response"]["request_count"] == 1
    wire = calls[0]
    assert wire["messages"] == request["request"]["messages"]
    assert wire["max_tokens"] == 1200
    assert wire["thinking"] == {"type": "disabled"}
    assert wire["response_format"] == {"type": "json_object"}
    assert not wire["stream"]
    assert result["response"]["requests"][0]["body"] == wire
    assert json.loads(result["response"]["body"])["provider_extra"] == "[REDACTED]"
    assert result["response"]["langchain_message"]["type"] == "ai"
    assert result["output"]["provider_model"] == "fixture-provider-model"
    assert result["output"]["usage"] == body["usage"]
    assert result["output"]["semantic_fidelity"] == "unverified"
    assert "fixture-api-key" not in json.dumps(result)


@pytest.mark.parametrize("case", ["truncated", "invalid_schema", "repaired_json", "rate_limit"])
def test_invalid_provider_results_preserve_response_and_never_retry(case):
    content = response("Source evidence.", "read").model_dump_json()
    if case == "invalid_schema":
        content = '{"recommendation":"read"}'
    if case == "repaired_json":
        content = content[:-1]  # LangChain's JSON parser can repair this; our contract rejects it.
    body = completion(content, finish_reason="length" if case == "truncated" else "stop")
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(429 if case == "rate_limit" else 200, json=body)

    result = run(
        incoming("Source evidence."), api_key="fixture-key", transport=httpx.MockTransport(respond)
    )
    assert len(calls) == result["response"]["request_count"] == 1
    assert result["error"]
    assert result["output"] is None
    assert json.loads(result["response"]["body"]) == body
    assert result["response"]["usage"] == body["usage"]


def test_missing_key_records_zero_requests():
    result = run(incoming("Source evidence."), api_key="")
    assert result["error"]
    assert result["response"]["request_count"] == 0
    assert result["output"] is None
