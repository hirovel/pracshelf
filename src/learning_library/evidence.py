"""Locate source quotes; normalize prose layout while preserving code whitespace."""

import re

from markdown_it import MarkdownIt


def _code_ranges(text):
    """Protect block/inline code from whitespace normalization, including samples."""
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    ranges = [
        (offsets[token.map[0]], offsets[token.map[1]])
        for token in MarkdownIt().parse(text)
        if token.type in {"fence", "code_block"} and token.map
    ]
    opening = None
    for match in re.finditer(r"`+", text):
        if any(start <= match.start() < end for start, end in ranges):
            continue
        # Escaped delimiters may be ordinary prose; protecting too much is safe.
        if opening is None:
            opening = match
        elif len(opening.group()) == len(match.group()):
            ranges.append((opening.start(), match.end()))
            opening = None
    if opening is not None:
        ranges.append((opening.start(), len(text)))
    return ranges


def _canonical(text, offset, code_ranges):
    chars, positions = [], []
    for index, char in enumerate(text):
        protected = any(start <= offset + index < end for start, end in code_ranges)
        char = char if protected or not char.isspace() else " "
        if char == " " and not protected and chars and chars[-1] == " ":
            continue
        chars.append(char)
        positions.append(index)
    return "".join(chars), positions


def _align(quote, excerpts, code_ranges):
    for part in excerpts:
        start = part["text"].find(quote)
        end, kind = start + len(quote), "exact"
        if start < 0:
            normalized, positions = _canonical(part["text"], part["start"], code_ranges)
            needle, quote_positions = _canonical(quote, 0, _code_ranges(quote))
            position = normalized.find(needle)
            if position < 0:
                continue
            # A bare code quote may lack Markdown delimiters. Compare every
            # protected source span against its aligned, unnormalized quote span.
            code_unchanged = True
            for left, right in code_ranges:
                indices = [i for i in range(len(needle))
                           if left <= part["start"] + positions[position + i] < right]
                if not indices:
                    continue
                first, last = indices[0], indices[-1]
                original = part["text"][positions[position + first]:positions[position + last] + 1]
                quoted = quote[quote_positions[first]:quote_positions[last] + 1]
                if original != quoted:
                    code_unchanged = False
                    break
            if not code_unchanged:
                continue
            start, end = positions[position], positions[position + len(needle) - 1] + 1
            kind = "layout_whitespace"
        return {
            "matched": True, "start": part["start"] + start, "end": part["start"] + end,
            "match_type": kind, "source_quote": part["text"][start:end],
        }
    return {"matched": False, "start": -1, "end": -1, "match_type": "missing", "source_quote": None}
