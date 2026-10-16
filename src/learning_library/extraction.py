"""Turn saved response bytes into a reviewable document; no network or storage.

The raw snapshot remains authoritative. HTML extraction is heuristic: preserve
preformatted blocks in place and expose omissions rather than certifying fullness.
"""

import hashlib
import json
import re
from datetime import date
from urllib.parse import urlparse

import trafilatura


def extract_document(body: bytes, url: str, content_type: str | None) -> dict:
    """Extract HTML or UTF-8 text/Markdown, or raise ValueError without saving.

    Publication dates require explicit publication metadata. Missing/conflicting
    declarations stay unknown; a declared date is not independently verified.
    """
    media = (content_type or "").split(";", 1)[0].strip().lower()
    html = bool(
        re.match(
            rb"\s*<(?:!doctype\s+html|html|head|body|article)\b",
            body.removeprefix(b"\xef\xbb\xbf"),
            re.IGNORECASE,
        )
    )
    if not html and media in {"text/plain", "text/markdown", "text/x-markdown"}:
        text = body.decode("utf-8-sig")
        if len(text.strip()) < 100:
            raise ValueError("No substantial text; raw response retained")
        heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        return {
            "kind": "article",
            "url": url,
            "author": "",
            "published": None,
            "title": heading.group(1) if heading else urlparse(url).path.rsplit("/", 1)[-1] or url,
            "text": text,
            "completeness": "text_saved; requires_review",
            "extraction": {
                "version": "original-v2",
                "format": "text",
                "method": "utf8_verbatim",
                "date": {"status": "unknown", "evidence": []},
                "warnings": ["未发现明确的发布日期元数据；正文中的日期未自动认定为发布日期。"],
            },
        }
    if not html and media not in {"", "text/html", "application/xhtml+xml"}:
        raise ValueError(f"Unsupported content type: {media}; raw response retained")
    tree = trafilatura.load_html(body)
    if tree is None:
        raise ValueError("Could not parse HTML; raw response retained")
    tree.make_links_absolute(url, resolve_base_href=True)
    published, date_info = _publication_date(tree)
    # Legacy blogs may mark the article with div.body rather than <article>.
    # Trafilatura's paragraph fallback can silently omit its headings and lists.
    # Normalize only an unambiguous, titled container; leave the snapshot intact.
    content_container = "automatic"
    if not tree.xpath('//article | //*[@itemprop="articleBody"]'):
        bodies = tree.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), " body ")][h1]'
        )
        if len(bodies) == 1:
            bodies[0].tag = "article"
            content_container = "div.body[h1]"
    # Use deterministic markers so rerunning the same snapshot is idempotent.
    prefix = "SAVEDPRE" + hashlib.sha256(body).hexdigest()[:16]
    blocks = []
    line_numbers_removed = 0
    for index, node in enumerate(tree.xpath("//pre")):
        numbers = node.xpath(
            './/code/span[contains(concat(" ", normalize-space(@class), " "), " l ")]'
            '/span[contains(concat(" ", normalize-space(@class), " "), " ln ")]'
        )
        if numbers and [n.text_content() for n in numbers] == [str(i) for i in range(1, len(numbers) + 1)]:
            for number in numbers:
                number.drop_tree()  # Keep the code in the tail; discard only the display number.
            line_numbers_removed += len(numbers)
        text = "".join(node.itertext())
        if not text.strip():
            continue
        marker = f"{prefix}N{index}END"
        blocks.append((marker, text))
        node.clear(keep_tail=True)
        node.tag, node.text = "p", marker
    article = trafilatura.bare_extraction(
        tree,
        url=url,
        include_comments=False,
        include_links=True,
        include_tables=True,
        include_formatting=True,
        with_metadata=True,
        date_extraction_params={"extensive_search": False},
    )
    if article is None or not article.text:
        raise ValueError("No substantial article text extracted; raw response retained")
    text = article.text
    preserved = 0
    for marker, code in blocks:
        if marker in text:
            # Fences are presentation delimiters; original indentation stays intact.
            fence = "`" * max(3, max((len(m) + 1 for m in re.findall(r"`+", code)), default=3))
            text = text.replace(marker, f"{fence}\n{code}\n{fence}")
            preserved += 1
    if len(text.strip()) < 100:
        raise ValueError("No substantial article text extracted; raw response retained")
    warnings = ["HTML 提取是阅读副本，图片、公式及正文完整性仍需对照原网页。"]
    if preserved != len(blocks):
        warnings.append(
            f"网页有 {len(blocks)} 个非空预格式块，正文保留 {preserved} 个；请核对遗漏。"
        )
    if date_info["status"] != "declared":
        warnings.append("发布日期未知或声明冲突；未使用 URL、引用年份或猜测日期。")
    return {
        "kind": "article",
        "title": article.title or url,
        "url": url,
        "author": article.author or "",
        "published": published,
        "text": text,
        "completeness": "html_saved; extracted_text_requires_review",
        "extraction": {
            "version": "original-v4",
            "format": "html",
            "method": "trafilatura_protected_pre",
            "content_container": content_container,
            "date": date_info,
            "preformatted_blocks": {"in_page": len(blocks), "in_text": preserved},
            "presentation_line_numbers_removed": line_numbers_removed,
            "warnings": warnings,
        },
    }


def _publication_date(tree):
    evidence = []

    def add(source, raw):
        if not isinstance(raw, str) or not raw.strip():
            return
        raw = raw.strip()
        try:
            value = date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            value = None
        evidence.append({"source": source, "value": raw, "date": value})

    for node in tree.xpath("//meta"):
        key = (node.get("property") or node.get("name") or node.get("itemprop") or "").lower()
        if key in {"article:published_time", "datepublished", "pubdate"}:
            add("meta:" + key, node.get("content"))
    for node in tree.xpath('//time[@itemprop="datePublished"]'):
        add("time:datePublished", node.get("datetime") or "".join(node.itertext()))

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if isinstance(types, list) and any(
                t in {"Article", "BlogPosting", "NewsArticle", "ScholarlyArticle"}
                for t in types
                if isinstance(t, str)
            ):
                add("jsonld:datePublished", value.get("datePublished"))
            # Do not mistake nested referenced works for the current article.
            visit(value.get("@graph", []))

    for node in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            visit(json.loads(node.text or ""))
        except (ValueError, RecursionError):
            continue
    dates = {item["date"] for item in evidence if item["date"]}
    status = "declared" if len(dates) == 1 else "conflict" if dates else "unknown"
    return (next(iter(dates)) if len(dates) == 1 else None), {
        "status": status,
        "evidence": evidence,
    }
