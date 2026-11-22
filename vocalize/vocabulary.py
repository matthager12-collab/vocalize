"""Deterministic vocabulary corrections, for every speech-to-text engine.

Whisper is prompted with `[stt] vocabulary`; Parakeet ignores it. This
pass runs on the transcript after either engine and before the optional
cleanup: a run of one to four words whose letters and digits, lower-cased,
spell a term's letters and digits is replaced by the term exactly as the
user wrote it ("py project" -> "pyproject", "duck db" -> "DuckDB").

Conservative by construction: no fuzzy or sounds-like matching, a term
of fewer than three letters or digits is never matched, and text with no
term in it comes back byte for byte. Pure text processing: no model, no
network, and the text is never logged.
"""

from __future__ import annotations

import re

MAX_WORDS = 4
MIN_KEY_CHARS = 3
# Characters a word may hold between its first and last letter or digit
# and still be read as part of a spoken term ("sha-256", "malu.db").
_JOINERS = frozenset("-_.")

_WORD = re.compile(r"\S+")


def _key(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _core(token: str) -> tuple[int, int] | None:
    """(start, end) of the token between its first and last letter or digit."""
    start = 0
    while start < len(token) and not token[start].isalnum():
        start += 1
    if start == len(token):
        return None
    end = len(token)
    while not token[end - 1].isalnum():
        end -= 1
    return start, end


def correct(text: str, terms) -> str:
    """`text` with every spoken spelling of a term replaced by the term.

    Longer terms win over shorter ones where matches overlap; the
    punctuation around a run is kept. Untouched text is never rewritten.
    """
    if not text or not terms:
        return text
    by_key: dict[str, str] = {}
    allowed = set(_JOINERS)
    for term in terms:
        if not isinstance(term, str):
            continue
        key = _key(term)
        if len(key) < MIN_KEY_CHARS:
            continue
        by_key.setdefault(key, term)
        allowed.update(ch for ch in term if not ch.isalnum() and not ch.isspace())
    if not by_key:
        return text
    longest = max(len(key) for key in by_key)

    # Each word as (core start, core end, core key), or None when it cannot
    # take part in a run (no letters or digits, or an unexpected character).
    words = []
    for match in _WORD.finditer(text):
        token = match.group()
        bounds = _core(token)
        if bounds is None:
            words.append(None)
            continue
        core = token[bounds[0]:bounds[1]]
        if not all(ch.isalnum() or ch in allowed for ch in core):
            words.append(None)
            continue
        words.append((match.start() + bounds[0], match.start() + bounds[1],
                      _key(core), bounds[0] > 0, bounds[1] < len(token)))

    candidates = []  # (key length, words, start index, end index, term)
    for i, first in enumerate(words):
        if first is None:
            continue
        key = ""
        for n in range(MAX_WORDS):
            j = i + n
            if j >= len(words) or words[j] is None:
                break
            _start, _end, word_key, leading, trailing = words[j]
            # Only the run's outer edges may carry punctuation.
            if n > 0 and leading:
                break
            key += word_key
            if len(key) > longest:
                break
            term = by_key.get(key)
            if term is not None:
                candidates.append((len(key), n + 1, i, j, term))
            if trailing:
                break
    if not candidates:
        return text

    candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))
    taken = [False] * len(words)
    chosen = []
    for _length, _count, i, j, term in candidates:
        if any(taken[i:j + 1]):
            continue
        for k in range(i, j + 1):
            taken[k] = True
        chosen.append((words[i][0], words[j][1], term))

    chosen.sort()
    out = []
    position = 0
    for start, end, term in chosen:
        out.append(text[position:start])
        out.append(term)
        position = end
    out.append(text[position:])
    return "".join(out)
