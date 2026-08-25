"""Turn markdown into text that sounds sensible when spoken aloud.

Most TTS tools read a markdown table cell-by-cell, left to right,
which turns a table into a stream of disconnected numbers ("Q1. 4.2
million. Q2. 5.1 million...") with no sense of what row or column
you're in. This module rewrites tables, lists, headers, links, and
code as short declarative sentences before the text ever reaches the
TTS API — the same trick a person reading a table aloud to someone
else would use instinctively.

Everything here is pure text-in, text-out, so it's fully unit
testable without an API key or network access.
"""

from __future__ import annotations

import re

# One dash per column is legal GitHub-flavored markdown ("| - | - |").
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)")
_NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)")
_FENCE_RE = re.compile(r"^\s*```")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_BOLD_ITALIC_RE = re.compile(r"(\*\*\*|___)(.+?)\1")
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(\*|_)(.+?)\1")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _split_table_row(line: str) -> list[str]:
    row = line.strip()
    row = row.removeprefix("|")
    row = row.removesuffix("|")
    return [cell.strip() for cell in row.split("|")]


def _is_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    header, sep = lines[i], lines[i + 1]
    if "|" not in header or not _TABLE_SEPARATOR_RE.match(sep):
        return False
    # Prose containing a stray "|" above a horizontal rule looks like a
    # header + separator pair, so insist the column counts line up too.
    return len(_split_table_row(header)) == len(_split_table_row(sep))


def _flatten_table(lines: list[str], start: int) -> tuple[str, int]:
    """Convert a markdown table starting at `start` into spoken prose.

    Returns (spoken_text, index_of_first_line_after_table).
    """
    headers = _split_table_row(lines[start])
    i = start + 2  # skip header + separator row
    rows: list[list[str]] = []
    while i < len(lines) and "|" in lines[i] and lines[i].strip():
        rows.append(_split_table_row(lines[i]))
        i += 1

    if not rows:
        return "", i

    noun = "row" if len(rows) == 1 else "rows"
    sentences = [f"Table with {len(rows)} {noun}."]
    for row in rows:
        label = row[0] if row else ""
        parts = []
        # Walk by index, not dict(zip(...)): ragged rows and repeated
        # header names would otherwise lose cells without a word.
        for idx, value in enumerate(row):
            if idx == 0:
                continue  # already spoken as the row's label
            if not value:
                continue
            header = headers[idx] if idx < len(headers) else f"column {idx + 1}"
            parts.append(f"{header} is {value}")
        if parts:
            sentences.append(f"For {label}: " + "; ".join(parts) + ".")
        else:
            sentences.append(f"{label}.")

    return " ".join(sentences), i


def _strip_inline_markdown(text: str) -> str:
    text = _IMAGE_RE.sub(lambda m: m.group(1) or "image", text)
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_ITALIC_RE.sub(lambda m: m.group(2), text)
    text = _BOLD_RE.sub(lambda m: m.group(2), text)
    text = _ITALIC_RE.sub(lambda m: m.group(2), text)
    text = _INLINE_CODE_RE.sub(lambda m: m.group(1), text)
    return text


def flatten_markdown(text: str) -> str:
    """Rewrite markdown as plain, speakable prose.

    - Tables become short "for X, Y is Z" sentences per row.
    - Headings become their own sentence (so there's a natural pause).
    - Bullet/numbered lists become "First, ... Next, ... Finally, ...".
    - Fenced code blocks are replaced with a spoken placeholder — reading
      code character-by-character out loud helps no one.
    - Links, images, bold/italic markers, and inline code ticks are
      stripped down to their readable text.
    """
    lines = text.splitlines()
    out: list[str] = []
    in_code_block = False
    list_ordinal = 0
    ordinals = [
        "First", "Second", "Third", "Fourth", "Fifth",
        "Sixth", "Seventh", "Eighth", "Ninth", "Tenth",
    ]

    i = 0
    while i < len(lines):
        line = lines[i]

        if _FENCE_RE.match(line):
            in_code_block = not in_code_block
            if not in_code_block:
                out.append("End of code block.")
            else:
                out.append("Code block:")
            i += 1
            continue

        if in_code_block:
            # Skip the raw code itself — it isn't worth speaking.
            i += 1
            continue

        if not line.strip():
            list_ordinal = 0
            i += 1
            continue

        if _is_table_start(lines, i):
            spoken, i = _flatten_table(lines, i)
            out.append(spoken)
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            out.append(_strip_inline_markdown(heading_match.group(2)).strip() + ".")
            list_ordinal = 0
            i += 1
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            word = ordinals[list_ordinal] if list_ordinal < len(ordinals) else "Next"
            out.append(f"{word}, {_strip_inline_markdown(bullet_match.group(1)).strip()}.")
            list_ordinal += 1
            i += 1
            continue

        numbered_match = _NUMBERED_RE.match(line)
        if numbered_match:
            out.append(
                f"Item {numbered_match.group(1)}: "
                f"{_strip_inline_markdown(numbered_match.group(2)).strip()}."
            )
            i += 1
            continue

        out.append(_strip_inline_markdown(line).strip())
        list_ordinal = 0
        i += 1

    spoken = " ".join(s for s in out if s)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken


def truncate_for_budget(text: str, max_chars: int | None) -> tuple[str, bool]:
    """Truncate to max_chars, returning (text, was_truncated).

    Useful for keeping a free-tier ElevenLabs quota from being blown
    through by one long document.
    """
    if max_chars is None or len(text) <= max_chars:
        return text, False
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut + "... (truncated)", True
