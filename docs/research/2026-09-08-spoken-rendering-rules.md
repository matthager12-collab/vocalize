# Spoken rendering rules

This spec says how vocalize turns written text into text a voice reads well. Every rule is a pure string change in `preprocess.py`, expressed only in punctuation and line breaks every engine honours. `--raw` bypasses all of it.

## Principles

- **A pause is a comma or a full stop.** The only two marks Kokoro, `say` and the cloud voices all honour.
- **Rewrite, never decorate.** A construct becomes ordinary words and punctuation, not a marker.
- **Structure gets words, not longer silence.** Pause length isn't adjustable without SSML, so headings, quotes and skipped sections spend a short cue word instead.
- **Under-strip.** Anything ambiguous, nested, malformed or over-long is left as it is today.
- **A period is only a sentence end when it really is one.** Abbreviation and digit-to-digit periods are protected.
- **Blocks are separated by a blank line.** The pause is the full stop, not the newline — no engine is known to lengthen a pause there.
- **Every rule is a test row,** and `flatten_markdown` run twice equals once.

## The rules

Bracket order: footnote, then numeric citation, then word bracket; checkboxes and ref-links are excluded from all three.

| Construct | Spoken as | Example in → out | Default | Config |
|---|---|---|---|---|
| Soft line wrap | one space, no pause | `wraps\nonto` → `wraps onto` | join | fixed |
| Blank line | full stop if the block lacks `.?!:`, then a blank line | `First para\n\nSecond.` → `First para.\n\nSecond.` | on | fixed |
| Heading, levels 1–2 (ATX `#`, or setext `===`/`---` underline directly under text) | `Heading,` cue, then text (keeps its own `?`/`!`, adds nothing) | `# Title One` → `Heading, Title One.` | cue | `headings` |
| Heading, levels 3–6 | `Sub-heading,` cue | `### Sub Three` → `Sub-heading, Sub Three.` | cue | `headings` |
| Em dash, en dash or spaced hyphen as a dash | comma | `clear—we shipped` → `clear, we shipped`; `simple - just restart` → `simple, just restart` | comma | fixed |
| En dash between numbers | the word "to" | `pages 3–5` → `pages 3 to 5` | to | fixed |
| Guard, leave as-is: compound hyphen, digit-period, numbers/units/times/percents, ALL CAPS | unchanged | `well-known`, `3.5 GB, v2.0.1`, `12:30, 50%`, `URGENT NOTICE` → all unchanged | unchanged | fixed |
| Parenthetical mid-sentence | comma, content, comma | `results (which surprised everyone) were` → `results, which surprised everyone, were` | pause | `parentheticals` |
| Parenthetical at a sentence end, or a whole sentence | comma at the bracket if mid-sentence continues; brackets vanish if the parenthetical is the whole sentence | `The trend is clear (see fig. 3).` → `The trend is clear, see fig 3.`; `(See the appendix.)` → `See the appendix.` | pause | `parentheticals` |
| Square brackets around words | comma, content, comma (guard: nested, unmatched or over-long stays untouched) | `the plan [the second one] was` → `the plan, the second one, was`; `((a+b)*c)` → unchanged | pause | `parentheticals` |
| Task-checkbox / reference-style link | resolved as a plain item or link, not a citation | `- [x] done` → `First, done.`; `[the docs][1]` → `the docs` | unchanged | fixed |
| Abbreviation period, title list | period always deleted (a title is always followed by a name) | `Dr. Smith` → `Dr Smith` | strip | `abbreviations` |
| Abbreviation period, context list | kept before a capital word or block end (it may be a sentence end); dropped before a lowercase word, digit or comma | `pens, etc. Then start.` → unchanged; `Fig. 3` → `Fig 3` | keep/strip | `abbreviations` |
| Abbreviation, Latin list | expanded, comma kept (see list below) | `e.g. chips` → `for example, chips` | expand | `abbreviations` |
| Initials | periods deleted, letters spaced | `J. R. R. Tolkien` → `J R R Tolkien` | strip | `abbreviations` |
| Bulleted or numbered list | ordinals or `Item N:`, as today | `- one\n- two` → `First, one. Second, two.`; `1. buy milk` → `Item 1: buy milk.` | unchanged | fixed |
| Nested list item | `Sub-item,` cue; parent count skips it | `- a\n  - b\n- c` → `First, a. Sub-item, b. Second, c.` | cue | `nested_lists` |
| Table | row sentences, as today, in their own block | Q1 revenue row → `Table with 1 row. For Q1: Revenue is 4.2M.` | unchanged | fixed |
| Inline code, bold, italic, HTML tag | markers or tags stripped, text kept | `` `pip install` `` → `pip install`; `a<br>b` → `a b` | unchanged | fixed |
| Code block, fenced or indented (4 spaces) | one placeholder sentence, own block | `Here's the fix:` + fence → `Here's the fix:\n\nSkipping a code block.` | unchanged | fixed |
| Markdown link, image | link text, or alt text ("image" if none) | `[the docs](url)` → `the docs`; `![Revenue chart](c.png)` → `Revenue chart` | unchanged | fixed |
| Bare URL | host, dots as the word "dot" | `https://example.com/page` → `example dot com` | domain | `urls` |
| Block quote | `>` stripped, `Quote,` cue, `End quote.` only if longer than one line | `> A line.\n> Two.` → `Quote, A line. Two. End quote.` | cue | `blockquotes` |
| Footnote marker, body line | dropped (body announced once per document) | `support[^1].` → `support.`; `[^1]: Smith, 2019.` → nothing (`Skipping 1 footnote.`) | drop | `citations` |
| Numeric citation bracket | dropped if tight to a word; a 4-digit year is kept | `shown previously [12].` → `shown previously.`; `[2024]` unchanged | drop | `citations` |
| Reference list section | one sentence in its place | `## References` + entries → `Skipping the reference list.` | skip | `references` |
| Page number line, running head or footer | page numbers dropped outright; a repeating head/footer only via the furniture guard: short, unpunctuated, non-list/table/heading, repeated 3+ | `Vocalize User Guide` ×5 → removed; `Yes.` ×3 → unchanged | drop | `furniture` |
| Horizontal rule, YAML front matter | block break after a blank line (a setext heading if it follows text instead); at document start, dropped and announced only if that's the whole input | `Before.\n\n---\n\nAfter.` → `Before.\n\nAfter.`; `---\ntitle: X\n---\nBody.` → `Body.` | drop | fixed |
| Ellipsis | comma mid-sentence, full stop line-final | `And then... nothing` → `And then, nothing` | comma | fixed |
| Emoji | dropped, space repaired | `job! 🎉 Let's` → `job! Let's` | drop | `emoji` |
| Final spacing pass | one space between sentences, blank lines kept, no doubled marks | `Done  .   Next (really) .` → `Done. Next, really.` | on | fixed |

**Title list** (period always dropped): `Dr, Mr, Mrs, Ms, Prof, St`.
**Context list** (period dropped unless before a capital word or block end): `fig, Fig, No, approx, etc, Inc, al, vs`.
**Latin list**: `e.g.` → `for example,`; `i.e.` → `that is,`.

## Pauses

| Pause | Written as | What every engine does |
|---|---|---|
| None | a space | runs the words together |
| Breath | `, ` | short break, no intonation reset |
| Sentence | `. ` (or `? `, `! `) | full stop, new intonation |
| Block | `.` then a blank line | full stop; the blank line is a hint, not a longer silence |

The blank line matters only past a provider's `MAX_CHARS`, where `split_for_synthesis` actually chunks — below it nothing chunks, so no silence rides on the blank line. Above it, `_PARAGRAPH_SPLIT_RE` breaks there first: a request-boundary effect, not an audible pause.

## The config table

```toml
[speech]
headings       = "cue"      # cue | plain | off
blockquotes    = "cue"      # cue | plain | off
nested_lists   = "cue"      # cue | flat | off
parentheticals = "pause"    # pause | drop | off
abbreviations  = "expand"   # expand | protect | off
citations      = "drop"     # drop | speak | off
references     = true       # skip reference-list sections
furniture      = true       # drop repeated running heads/footers
urls           = "domain"   # domain | full | drop
emoji          = true       # drop emoji

SPEECH_BRACKET_MAX_CHARS     = 120  # bracket-guard length
SPEECH_FURNITURE_MAX_CHARS   = 60   # furniture line length cap
SPEECH_FURNITURE_MIN_REPEATS = 3    # repeats to drop furniture
```

`off` always means: leave that construct exactly as written.

Wiring mirrors `[notes]`/`[stt]`: add `speech` to `KNOWN_SPEECH_KEYS`, add `_validate_speech_table` (unknown key warns, bad value raises; `SPEECH_*` bounded like `stt.max_seconds`), add `resolve_speech` against `SPEECH_DEFAULTS`. `flatten_markdown(text, speech=None)` keeps every call site working.

## Decisions

All seven approved by the owner on 2026-09-08. The config table's defaults are these.

1. **Heading cue.** Use `Heading,` for levels 1–2 and `Sub-heading,` for 3–6.
2. **Parentheticals.** Default to pause; drop silently deletes content you asked to hear.
3. **Block quotes.** Cue on by default, so quoted words aren't mistaken for the author's.
4. **Bare URLs.** Read the host only, as "example dot com".
5. **Reference sections.** Skip References, Bibliography or Works Cited only when the lines after look like entries; "Notes" doesn't trigger it.
6. **Latin abbreviations.** Expand them, because "e g" is one of the worst sounds today.
7. **Emoji.** Drop them, because behaviour differs on every engine.

`flatten_markdown` has one call site, `vocalize/cli.py:318` — notes and dictation never pass through it.

## What this supersedes

- **Issue #8, kept:** cue-and-pause headings, pauses at paragraph/list/table boundaries, parenthetical pause-content-pause with a drop mode, abbreviation periods never sentence ends.
- **Issue #8, changed:** the heading cue is a comma clause, not a sentence; parenthetical pauses are commas, not periods; no "longer pause" — depth is spoken as words.
- **Issue #7, kept:** running heads, page numbers, cite markers, footnote bodies and reference lists all removed.
- **Issue #7, changed:** a reference list is announced, not silently deleted; "Notes" no longer triggers the skip.
- **Not doing:** SSML, `[[slnc]]`, a pronunciation lexicon, acronym expansion, number verbalisation, emoji naming, language detection.

## Next

1. Order: block rules (furniture, front matter, references, fences/indented code, tables, rules, headings, quotes, lists), then inline rules (emoji, footnotes, citations, brackets, links, URLs, HTML tags, emphasis, dashes, ellipsis), then abbreviations and initials last (they need un-bracketed text), then the spacing pass.
2. Fix `re.sub(r"\s+", " ")` to collapse only spaces and tabs, and `truncate_for_budget` to split on any whitespace.
3. Rewrite `tests/test_preprocess.py` as one input-expected table (a row per rule above), plus two property tests: flatten is idempotent, and the output holds no character outside the allowed set (assert only, never filter — `R&D`, `C++`, `24/7` must survive).
4. The engines bundle came back a stub, so no engine claim here is measured; before locking defaults, listen to one long document on Kokoro and `say`, checking the blank-line pause, the comma parenthetical, and the heading cue.
