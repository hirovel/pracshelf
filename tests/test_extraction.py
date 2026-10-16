"""Synthetic articles test the extraction interface, not model judgment."""

import pytest

from learning_library.extraction import extract_document


def page(content, metadata=""):
    return (
        '<html><head><meta charset="utf-8"><title>Fixture</title>'
        + metadata
        + "</head><body><article><h1>Fixture</h1><p>"
        + "This synthetic article explains the conditions for a reproducible experiment. " * 6
        + "</p>"
        + content
        + "</article></body></html>"
    ).encode()


def test_preserves_preformatted_templates_in_order_with_indentation():
    code = "Review the candidate:\n    keep this indentation\n\nDo not assume success."
    doc = extract_document(
        page(
            f'<h2>Instructions</h2><pre>{code}</pre><p>Closing limitation. '
            '<a href="#conditions">Conditions</a></p>'
        ),
        "https://example.com/article",
        "text/html",
    )
    assert code in doc["text"]
    assert doc["text"].index("Instructions") < doc["text"].index(code)
    assert doc["text"].index(code) < doc["text"].index("Closing limitation.")
    assert "https://example.com/article#conditions" in doc["text"]
    assert doc["extraction"]["preformatted_blocks"] == {"in_page": 1, "in_text": 1}


def test_display_line_numbers_are_removed_without_changing_code_numbers_or_indent():
    doc = extract_document(
        page('<pre><code><span class="l"><span class="ln">1</span>const count = 42;</span>\n'
             '<span class="l"><span class="ln">2</span>    return count + 1;</span>\n'
             '<span class="l"><span class="ln">3</span></span></code></pre>'),
        'https://example.com/article', 'text/html',
    )
    assert 'const count = 42;\n    return count + 1;\n' in doc['text']
    assert '1const' not in doc['text'] and '2    return' not in doc['text']
    assert doc['extraction']['presentation_line_numbers_removed'] == 3


@pytest.mark.parametrize('code', [
    '<span class="ln">42</span> is a code value',
    ('<code><span class="l"><span class="ln">1</span>first</span>\n'
     '<span class="l"><span class="ln">9</span>last</span></code>'),
])
def test_unrecognized_number_markup_is_preserved(code):
    doc = extract_document(page(f'<pre>{code}</pre>'), 'https://example.com/article', 'text/html')
    assert doc['extraction']['presentation_line_numbers_removed'] == 0
    assert ('42 is a code value' in doc['text']) or ('1first\n9last' in doc['text'])


@pytest.mark.parametrize("container", ['div class="body"', "article"])
def test_article_structure_survives_long_prose_and_highlighted_code(container):
    # Long prose triggers paragraph-only fallback when a legacy body is missed.
    prose = "This article explains tool orchestration and its limits. " * 10
    code = 'if ready:\n    print("<result>")\n\n    # keep ``` inside code\n'
    body = (
        '<html><head><meta charset="utf-8"><title>Practical example</title></head>'
        '<body><nav>Navigation outside the article</nav>'
        f'<{container}><h1>Practical example</h1><p>{prose}</p>'
        '<h2>Limits</h2><ul><li><strong>Consistent results:</strong> preserve '
        'the <code>outputSchema</code> for every batch.</li>'
        '<li>Keep failures visible.<ol><li>Record the error.</li>'
        '<li>Retry only after review.</li></ol></li></ul>'
        '<h3>Example code</h3><div class="highlight"><pre><span>if ready:</span>\n'
        '    print("&lt;result&gt;")\n\n    # keep ``` inside code\n</pre></div>'
        '<p>See <a href="/reference">the reference</a> before applying this.</p>'
        f'</{container.split()[0]}><footer>Footer outside the article</footer>'
        '</body></html>'
    ).encode()
    doc = extract_document(body, "https://example.com/post", "text/html")
    text = doc["text"]
    assert "# Practical example" in text
    assert "## Limits" in text
    assert "### Example code" in text
    assert "- **Consistent results:** preserve the `outputSchema` for every batch." in text
    assert "Record the error." in text and "Retry only after review." in text
    assert code in text
    assert "https://example.com/reference" in text
    assert text.index("## Limits") < text.index("Consistent results") < text.index(code)
    assert "Navigation outside" not in text and "Footer outside" not in text


def test_reference_year_and_modified_date_do_not_become_publication_date():
    doc = extract_document(
        page(
            "<p>References: published 2025-08-25.</p>",
            '<meta property="article:modified_time" content="2026-10-09">',
        ),
        "https://example.com/2025/08/25/article",
        "text/html",
    )
    assert doc["published"] is None
    assert doc["extraction"]["date"]["status"] == "unknown"


def test_publication_declarations_keep_evidence_and_conflicts():
    meta = '<meta property="article:published_time" content="2026-10-01T09:00:00Z">'
    doc = extract_document(page("", meta), "https://example.com/article", "text/html")
    assert doc["published"] == "2026-10-01"
    assert doc["extraction"]["date"]["evidence"][0]["value"] == "2026-10-01T09:00:00Z"
    meta += '<script type="application/ld+json">{"@type":"Article","datePublished":"2026-10-02"}</script>'
    conflict = extract_document(page("", meta), "https://example.com/article", "text/html")
    assert conflict["published"] is None
    assert conflict["extraction"]["date"]["status"] == "conflict"


def test_markdown_is_saved_verbatim_and_binary_is_rejected():
    text = (
        "# 原文标题\n\n" + "保留原文，不生成总结。" * 12 + "\n```html\n<html>example</html>\n```\n"
    )
    doc = extract_document(text.encode(), "https://example.com/doc.md", "text/plain; charset=utf-8")
    assert doc["text"] == text
    assert doc["title"] == "原文标题"
    assert doc["extraction"]["method"] == "utf8_verbatim"
    with pytest.raises(ValueError, match="Unsupported content type"):
        extract_document(b"%PDF-1.7 fake", "https://example.com/paper.pdf", "application/pdf")
