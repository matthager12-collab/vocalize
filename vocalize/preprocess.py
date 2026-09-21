"""Turn markdown into text that sounds sensible when spoken aloud.

Implements the forty spoken rendering rules defined in
docs/research/2026-09-08-spoken-rendering-rules.md.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# One dash per column is legal GitHub-flavored markdown ("| - | - |").
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)")
_NUMBERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_BOLD_ITALIC_RE = re.compile(r"(\*\*\*|___)(.+?)\1")
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(\*|_)(.+?)\1")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_CHECKBOX_RE = re.compile(r"^\s*\[[ xX]\]\s*")

# Emoji regex covering Unicode emoji ranges and common symbols
_EMOJI_RE = re.compile(
    r"[\U00010000-\U0010ffff"
    r"\u2600-\u27bf"
    r"\u2300-\u23ff"
    r"\u2b50\u2b55\u200d\ufe0f]"
)

_CODE_PLACEHOLDER = "Skipping a code block."

# Lists for abbreviation expansion
_TITLE_LIST = ("Dr", "Mr", "Mrs", "Ms", "Prof", "St")
_CONTEXT_LIST = ("fig", "Fig", "No", "approx", "etc", "Inc", "al", "vs")

_ORDINALS = [
    "First", "Second", "Third", "Fourth", "Fifth",
    "Sixth", "Seventh", "Eighth", "Ninth", "Tenth",
]


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
        for idx, value in enumerate(row):
            if idx == 0:
                continue
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
    text = _REF_LINK_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_ITALIC_RE.sub(lambda m: m.group(2), text)
    text = _BOLD_RE.sub(lambda m: m.group(2), text)
    text = _ITALIC_RE.sub(lambda m: m.group(2), text)
    text = _INLINE_CODE_RE.sub(lambda m: m.group(1), text)
    return text


def _clean_punctuation_spacing(text: str) -> str:
    """Normalize whitespace and punctuation spacing."""
    # Remove space before punctuation
    text = re.sub(r"\s+([,.:;?!])", r"\1", text)
    # Collapse double commas, double periods (excluding ellipsis)
    text = re.sub(r",\s*,+", ", ", text)
    text = re.sub(r"(?<!\.)\.\s*\.(?!\.)", ".", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\.\s*,", ".", text)
    # Ensure a single space after punctuation when followed by a letter (never digit-period-digit)
    text = re.sub(r"(?<=[A-Za-z0-9])([,;:?!])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"(?<!\d)\.([A-Za-z])", r". \1", text)
    # Collapse multiple horizontal spaces and tabs into a single space
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _ensure_block_terminator(block: str) -> str:
    """Ensure the block ends with sentence termination (.?!:) if non-empty."""
    b = block.rstrip()
    if not b:
        return ""
    if b[-1] not in ".?!:":
        b += "."
    return b


def _apply_inline_rules(text: str, speech: dict[str, Any]) -> str:
    """Apply inline rules: emoji, URLs, citations, parentheticals, dashes, abbreviations."""
    # 1. Emoji
    if speech.get("emoji", True):
        text = _EMOJI_RE.sub("", text)

    # 2. HTML tags: <br> becomes space, others stripped
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)

    # 3. Markdown links and inline markup
    text = _strip_inline_markdown(text)

    # 4. Bare URLs
    urls_mode = speech.get("urls", "domain")
    if urls_mode == "domain":
        def _replace_url(m: re.Match) -> str:
            raw_url = m.group(0)
            parsed = urlparse(raw_url if "://" in raw_url else f"http://{raw_url}")
            host = parsed.netloc or parsed.path.split("/")[0]
            host = host.split(":")[0]  # strip port
            return host.replace(".", " dot ")
        text = re.sub(r"\bhttps?://[^\s<>]+|\bwww\.[^\s<>]+\b", _replace_url, text)
    elif urls_mode == "drop":
        text = re.sub(r"\bhttps?://[^\s<>]+|\bwww\.[^\s<>]+\b", "", text)

    # 5. Citations and Footnotes
    citations_mode = speech.get("citations", "drop")
    # Footnote references in body: [^1]
    text = re.sub(r"\[\^[^\]]+\]", "", text)
    # Numeric citations: [12] or [1, 2] or [3-5]
    if citations_mode == "drop":
        def _citation_sub(m: re.Match) -> str:
            content = m.group(1).strip()
            # Guard: Keep 4-digit year like [2024]
            if re.fullmatch(r"(?:19|20)\d{2}", content):
                return m.group(0)
            return ""
        text = re.sub(r"(?<=\S)\s*\[(\d+(?:[,\s–-]+\d+)*)\]", _citation_sub, text)
    elif citations_mode == "speak":
        def _citation_speak(m: re.Match) -> str:
            content = m.group(1).strip()
            if re.fullmatch(r"(?:19|20)\d{2}", content):
                return m.group(0)
            return f", {content},"
        text = re.sub(r"(?<=\S)\s*\[(\d+(?:[,\s–-]+\d+)*)\]", _citation_speak, text)

    # 6. Dashes
    # En dash between numbers: "pages 3–5" -> "pages 3 to 5"
    text = re.sub(r"(?<=\d)\s*[–—]\s*(?=\d)", " to ", text)
    # Em dash, en dash, or spaced hyphen as a dash -> comma
    text = re.sub(r"(?:—|–|\s+-\s+)", ", ", text)

    # 7. Ellipsis
    # Sentence/line-final ellipsis
    text = re.sub(r"(?:\.\.\.|…)\s*(?=[A-Z\n]|$)", ". ", text)
    # Mid-sentence ellipsis
    text = re.sub(r"(?:\.\.\.|…)", ", ", text)

    # 8. Parentheticals and square brackets
    parentheticals_mode = speech.get("parentheticals", "pause")
    max_bracket = speech.get("bracket_max_chars", 120)

    if parentheticals_mode in ("pause", "drop"):
        def _has_nested_or_unmatched(s: str, open_c: str, close_c: str) -> bool:
            depth = 0
            for c in s:
                if c == open_c:
                    depth += 1
                    if depth > 1:
                        return True
                elif c == close_c:
                    depth -= 1
                    if depth < 0:
                        return True
            return depth != 0

        # Process non-nested parentheses
        if not _has_nested_or_unmatched(text, "(", ")"):
            def _paren_sub(m: re.Match) -> str:
                content = m.group(1).strip()
                if len(content) > max_bracket:
                    return m.group(0)
                if parentheticals_mode == "drop":
                    return ""
                # Pause mode:
                # Whole sentence parenthetical: (See the appendix.)
                if m.start() == 0 and m.end() == len(m.string.strip()):
                    return content
                # Sentence end: word (see fig. 3).
                end_match = re.search(r"\)\s*\.", m.string[m.start():m.end() + 2])
                if end_match:
                    return f", {content}."
                return f", {content},"

            text = re.sub(r"\(([^()]+)\)", _paren_sub, text)

        # Process square brackets around words: [the second one]
        if not _has_nested_or_unmatched(text, "[", "]"):
            def _bracket_sub(m: re.Match) -> str:
                content = m.group(1).strip()
                if len(content) > max_bracket:
                    return m.group(0)
                # Guard: Keep 4-digit years like [2024] unchanged
                if re.fullmatch(r"(?:19|20)\d{2}", content):
                    return m.group(0)
                if parentheticals_mode == "drop":
                    return ""
                return f", {content},"

            text = re.sub(r"\[([a-zA-Z0-9\s,;'-]+)\]", _bracket_sub, text)

    # 9. Abbreviations and Initials
    abbrevs_mode = speech.get("abbreviations", "expand")
    if abbrevs_mode in ("expand", "protect"):
        # Latin abbreviations: e.g. -> for example, i.e. -> that is
        if abbrevs_mode == "expand":
            text = re.sub(r"\be\.g\.,?\s*", "for example, ", text)
            text = re.sub(r"\bi\.e\.,?\s*", "that is, ", text)

        # Title list: period always deleted (Dr. Smith -> Dr Smith)
        titles_pat = r"\b(" + "|".join(_TITLE_LIST) + r")\.\s+"
        text = re.sub(titles_pat, r"\1 ", text)

        # Context list: period dropped unless before capital word or block end
        # Dropped before lowercase, digit, or comma
        context_pat = r"\b(" + "|".join(_CONTEXT_LIST) + r")\.\s*(?=[a-z0-9,;])"
        text = re.sub(context_pat, r"\1 ", text)

        # Initials: J. R. R. Tolkien or J.R.R. Tolkien -> J R R Tolkien
        text = re.sub(r"\b([A-Z])\.\s*([A-Z])\.\s*([A-Z])\.\s*", r"\1 \2 \3 ", text)
        text = re.sub(r"\b([A-Z])\.\s*([A-Z])\.\s*", r"\1 \2 ", text)
        text = re.sub(r"\b([A-Z])\.\s+(?=[A-Z][a-z])", r"\1 ", text)

    return _clean_punctuation_spacing(text)


def _drop_furniture_lines(lines: list[str], speech: dict[str, Any]) -> list[str]:
    """Drop repeated running heads, footers, or page numbers."""
    if not speech.get("furniture", True):
        return lines

    max_chars = speech.get("furniture_max_chars", 60)
    min_repeats = speech.get("furniture_min_repeats", 3)

    counts: dict[str, int] = {}
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Check if candidate furniture line
        # Must be short, unpunctuated (.?!:), not markdown structure
        if (
            len(s) <= max_chars
            and s[-1] not in ".?!:"
            and not _HEADING_RE.match(s)
            and not _BULLET_RE.match(s)
            and not _NUMBERED_RE.match(s)
            and not _FENCE_RE.match(s)
            and not s.startswith(">")
            and not s.startswith("|")
        ):
            counts[s] = counts.get(s, 0) + 1

    dropped_set = {s for s, count in counts.items() if count >= min_repeats}

    cleaned: list[str] = []
    for line in lines:
        s = line.strip()
        # Page numbers outright
        if re.fullmatch(r"(?:Page\s+)?\d+(?:\s+of\s+\d+)?", s, re.IGNORECASE):
            continue
        if s in dropped_set:
            continue
        cleaned.append(line)
    return cleaned


def _strip_front_matter(lines: list[str]) -> list[str]:
    """Strip YAML front matter if present at document start."""
    if not lines:
        return lines
    if lines[0].strip() == "---":
        for idx in range(1, len(lines)):
            if lines[idx].strip() in ("---", "..."):
                return lines[idx + 1:]
    return lines


def flatten_markdown(text: str, speech: dict[str, Any] | None = None) -> str:
    """Rewrite markdown as plain, speakable prose.

    Honours the forty spoken rendering rules and the [speech] config table.
    """
    if speech is None:
        try:
            from .config import resolve_speech
            speech = resolve_speech()
        except (ImportError, OSError, ValueError):
            from .config import SPEECH_DEFAULTS
            speech = dict(SPEECH_DEFAULTS)

    raw_lines = text.splitlines()
    raw_lines = _strip_front_matter(raw_lines)
    raw_lines = _drop_furniture_lines(raw_lines, speech)

    blocks: list[str] = []
    in_code_block = False
    list_ordinal = 0
    i = 0
    footnotes_count = 0

    headings_mode = speech.get("headings", "cue")
    quotes_mode = speech.get("blockquotes", "cue")
    nested_mode = speech.get("nested_lists", "cue")
    skip_references = speech.get("references", True)

    while i < len(raw_lines):
        line = raw_lines[i]
        stripped = line.strip()

        # Check code fence
        if _FENCE_RE.match(line):
            in_code_block = not in_code_block
            if in_code_block:
                last = blocks[-1] if blocks else None
                if last != _CODE_PLACEHOLDER:
                    blocks.append(_CODE_PLACEHOLDER)
            i += 1
            continue

        if in_code_block:
            i += 1
            continue

        # Blank line resets list ordinals
        if not stripped:
            list_ordinal = 0
            i += 1
            continue

        # Horizontal rule surrounded by blank lines (or alone)
        if stripped in ("---", "***", "___") and (i == 0 or not raw_lines[i - 1].strip()):
            i += 1
            continue

        # Footnote definitions: [^1]: ...
        if re.match(r"^\[\^[^\]]+\]:\s*", stripped):
            footnotes_count += 1
            i += 1
            continue

        # Reference-style link definitions: [ref]: http...
        if re.match(r"^\[[^\]]+\]:\s*https?://", stripped):
            i += 1
            continue

        # Reference list sections: ## References / Bibliography / Works Cited
        ref_heading_match = re.match(
            r"^(#{1,6})\s+(References|Bibliography|Works Cited)\b", stripped, re.IGNORECASE
        )
        if ref_heading_match and skip_references:
            # Check if followed by entries
            has_entries = False
            j = i + 1
            while j < len(raw_lines) and not _HEADING_RE.match(raw_lines[j].strip()):
                if raw_lines[j].strip():
                    has_entries = True
                    break
                j += 1
            if has_entries:
                blocks.append("Skipping the reference list.")
                # Skip to next heading or end
                i += 1
                while i < len(raw_lines) and not _HEADING_RE.match(raw_lines[i].strip()):
                    i += 1
                continue

        # Table
        if _is_table_start(raw_lines, i):
            spoken, i = _flatten_table(raw_lines, i)
            if spoken:
                blocks.append(_apply_inline_rules(spoken, speech))
            continue

        # Setext heading: Line followed by === or ---
        if (
            i + 1 < len(raw_lines)
            and stripped
            and not _HEADING_RE.match(stripped)
            and not _BULLET_RE.match(line)
            and not _NUMBERED_RE.match(line)
        ):
            next_line = raw_lines[i + 1].strip()
            if next_line and (all(c == "=" for c in next_line) or all(c == "-" for c in next_line)):
                h_text = _apply_inline_rules(stripped, speech)
                h_text = _ensure_block_terminator(h_text)
                if headings_mode == "cue":
                    blocks.append(f"Heading, {h_text}")
                else:
                    blocks.append(h_text)
                list_ordinal = 0
                i += 2
                continue

        # ATX Heading: # Title
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            h_raw = heading_match.group(2).strip()
            h_text = _apply_inline_rules(h_raw, speech)
            # Retain existing ending punctuation (? or !), else add period
            if not h_text.endswith("?") and not h_text.endswith("!"):
                h_text = _ensure_block_terminator(h_text)

            if headings_mode == "cue":
                if level <= 2:
                    blocks.append(f"Heading, {h_text}")
                else:
                    blocks.append(f"Sub-heading, {h_text}")
            elif headings_mode == "plain":
                blocks.append(h_text)
            else:
                blocks.append(h_text)
            list_ordinal = 0
            i += 1
            continue

        # Blockquote: > A quote
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < len(raw_lines) and raw_lines[i].strip().startswith(">"):
                q_line = raw_lines[i].strip().lstrip(">").strip()
                if q_line:
                    quote_lines.append(_apply_inline_rules(q_line, speech))
                i += 1
            if quote_lines:
                joined_quote = ". ".join(q.rstrip(".") for q in quote_lines) + "."
                if quotes_mode == "cue":
                    if len(quote_lines) > 1:
                        blocks.append(f"Quote, {joined_quote} End quote.")
                    else:
                        blocks.append(f"Quote, {joined_quote}")
                else:
                    blocks.append(joined_quote)
            continue

        # Bullet list item
        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            indent = bullet_match.group(1)
            content = _CHECKBOX_RE.sub("", bullet_match.group(2)).strip()
            item_text = _apply_inline_rules(content, speech)
            item_text = _ensure_block_terminator(item_text)

            is_nested = len(indent) >= 2 or "\t" in indent
            if is_nested and nested_mode == "cue":
                blocks.append(f"Sub-item, {item_text}")
            else:
                word = _ORDINALS[list_ordinal] if list_ordinal < len(_ORDINALS) else "Next"
                blocks.append(f"{word}, {item_text}")
                list_ordinal += 1
            i += 1
            continue

        # Numbered list item
        numbered_match = _NUMBERED_RE.match(line)
        if numbered_match:
            indent = numbered_match.group(1)
            num = numbered_match.group(2)
            content = _CHECKBOX_RE.sub("", numbered_match.group(3)).strip()
            item_text = _apply_inline_rules(content, speech)
            item_text = _ensure_block_terminator(item_text)

            is_nested = len(indent) >= 2 or "\t" in indent
            if is_nested and nested_mode == "cue":
                blocks.append(f"Sub-item, {item_text}")
            else:
                blocks.append(f"Item {num}: {item_text}")
            i += 1
            continue

        # Regular prose paragraph: accumulate until empty line, heading, list, or table
        para_lines = [line.strip()]
        i += 1
        while i < len(raw_lines):
            next_line = raw_lines[i]
            next_stripped = next_line.strip()
            if (
                not next_stripped
                or _FENCE_RE.match(next_line)
                or _HEADING_RE.match(next_stripped)
                or _BULLET_RE.match(next_line)
                or _NUMBERED_RE.match(next_line)
                or next_stripped.startswith(">")
                or _is_table_start(raw_lines, i)
            ):
                break
            para_lines.append(next_stripped)
            i += 1

        para_text = " ".join(para_lines)
        spoken_para = _apply_inline_rules(para_text, speech)
        if spoken_para:
            blocks.append(_ensure_block_terminator(spoken_para))
        list_ordinal = 0

    if footnotes_count > 0:
        noun = "footnote" if footnotes_count == 1 else "footnotes"
        blocks.append(f"Skipping {footnotes_count} {noun}.")

    # Join blocks with double newline to keep distinct spoken blocks
    result = "\n\n".join(b for b in blocks if b)
    return result.strip()


def truncate_for_budget(text: str, max_chars: int | None) -> tuple[str, bool]:
    """Truncate to max_chars, returning (text, was_truncated).

    Useful for keeping a free-tier ElevenLabs quota from being blown
    through by one long document.
    """
    if max_chars is None or len(text) <= max_chars:
        return text, False
    parts = text[:max_chars].rsplit(None, 1)
    return parts[0] if parts else "", True


# The eleven_multilingual_v2 model caps a single request at 10,000
# characters; 9,500 leaves margin.
DEFAULT_CHUNK_CHARS = 9500

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


def _pack(pieces: list[tuple[str, bool]], max_chars: int) -> list[str]:
    """Greedily re-merge adjacent pieces, joined by a single space, up to
    max_chars — so a boundary search doesn't produce more/smaller chunks
    than the limit actually requires.
    """
    chunks: list[str] = []
    current = ""
    for piece, mergeable in pieces:
        if not mergeable:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)
        elif not current:
            current = piece
        elif len(current) + 1 + len(piece) <= max_chars:
            current = f"{current} {piece}"
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def _pieces(text: str, max_chars: int) -> list[tuple[str, bool]]:
    """Break text into (piece, mergeable) tuples of at most max_chars each."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [(text, True)]

    for splitter in (_PARAGRAPH_SPLIT_RE.split, _SENTENCE_SPLIT_RE.split, str.split):
        units = splitter(text)
        if len(units) > 1:
            return [p for unit in units for p in _pieces(unit, max_chars)]

    return [(text[i : i + max_chars], False) for i in range(0, len(text), max_chars)]


def split_for_synthesis(text: str, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Split text for the TTS API's per-request character cap."""
    if len(text) <= max_chars:
        return [text]
    return _pack(_pieces(text, max_chars), max_chars)
