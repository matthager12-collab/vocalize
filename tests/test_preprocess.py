import re

import pytest

from vocalize.preprocess import (
    flatten_markdown,
    split_for_synthesis,
    truncate_for_budget,
)

RULE_CASES = [
    # 1. Soft line wrap
    ("wraps\nonto", "wraps onto"),
    # 2. Blank line
    ("First para\n\nSecond.", "First para.\n\nSecond."),
    # 3. Heading levels 1-2
    ("# Title One", "Heading, Title One."),
    ("## Title Two", "Heading, Title Two."),
    ("Title Setext\n===", "Heading, Title Setext."),
    ("Heading Question?\n---", "Heading, Heading Question?"),
    # 4. Heading levels 3-6
    ("### Sub Three", "Sub-heading, Sub Three."),
    ("#### Sub Four", "Sub-heading, Sub Four."),
    # 5. Em dash, en dash, or spaced hyphen
    ("clear—we shipped", "clear, we shipped"),
    ("simple - just restart", "simple, just restart"),
    # 6. En dash between numbers
    ("pages 3–5", "pages 3 to 5"),
    # 7. Guards: compound hyphen, digit-period, numbers, ALL CAPS
    ("well-known", "well-known"),
    ("3.5 GB, v2.0.1", "3.5 GB, v2.0.1"),
    ("12:30, 50%", "12:30, 50%"),
    ("URGENT NOTICE", "URGENT NOTICE"),
    # 8. Parenthetical mid-sentence
    ("results (which surprised everyone) were", "results, which surprised everyone, were"),
    # 9. Parenthetical at sentence end
    ("The trend is clear (see fig. 3).", "The trend is clear, see fig 3."),
    # 10. Parenthetical whole sentence
    ("(See the appendix.)", "See the appendix."),
    # 11. Square brackets around words
    ("the plan [the second one] was", "the plan, the second one, was"),
    # 12. Guard bracket: nested
    ("((a+b)*c)", "((a+b)*c)"),
    # 13. Task checkbox and ref-link
    ("- [x] done", "First, done."),
    ("[the docs][1]", "the docs"),
    # 14. Abbreviation period, title list
    ("Dr. Smith", "Dr Smith"),
    ("Prof. Higgins", "Prof Higgins"),
    # 15. Abbreviation period, context list
    ("pens, etc. Then start.", "pens, etc. Then start."),
    ("Fig. 3", "Fig 3"),
    # 16. Abbreviation, Latin list
    ("e.g. chips", "for example, chips"),
    ("i.e. this", "that is, this"),
    # 17. Initials
    ("J. R. R. Tolkien", "J R R Tolkien"),
    ("J.R.R. Tolkien", "J R R Tolkien"),
    # 18. Bulleted / numbered list
    ("- one\n- two", "First, one.\n\nSecond, two."),
    ("1. buy milk\n2. walk dog", "Item 1: buy milk.\n\nItem 2: walk dog."),
    # 19. Nested list item
    ("- a\n  - b\n- c", "First, a.\n\nSub-item, b.\n\nSecond, c."),
    # 20. Table
    ("| Q1 | Revenue |\n|---|---|\n| Jan | 4.2M |", "Table with 1 row. For Jan: Revenue is 4.2M."),
    # 21. Inline code, bold, italic, HTML tag
    ("Run `pip install` now.", "Run pip install now."),
    ("a<br>b", "a b"),
    ("This is **very** important and *also* urgent.", "This is very important and also urgent."),
    # 22. Code block
    ("```python\ndef f(): pass\n```", "Skipping a code block."),
    # 23. Markdown link, image
    ("[the docs](https://example.com)", "the docs"),
    ("![Revenue chart](c.png)", "Revenue chart"),
    # 24. Bare URL
    ("https://example.com/page", "example dot com"),
    # 25. Block quote
    ("> A line.\n> Two.", "Quote, A line. Two. End quote."),
    ("> Single line.", "Quote, Single line."),
    # 26. Footnotes
    ("support[^1].\n\n[^1]: Smith, 2019.", "support.\n\nSkipping 1 footnote."),
    # 27. Numeric citation bracket
    ("shown previously [12].", "shown previously."),
    ("Published in [2024].", "Published in [2024]."),
    # 28. Reference list section
    ("## References\n[1] Smith, 2019.", "Skipping the reference list."),
    # 29. Furniture line repeated 3+ times dropped
    ("Running Header\n\nPage one.\n\nRunning Header\n\nPage two.\n\nRunning Header\n\nPage three.", "Page one.\n\nPage two.\n\nPage three."),
    # 30. Front matter
    ("---\ntitle: X\n---\nBody text.", "Body text."),
    # 31. Ellipsis
    ("And then... nothing", "And then, nothing"),
    # 32. Emoji
    ("job! 🎉 Let's", "job! Let's"),
    # 33. Final spacing pass
    ("Done  .   Next (really) .", "Done. Next, really."),
]


@pytest.mark.parametrize("input_md,expected", RULE_CASES)
def test_spoken_rendering_rules_table(input_md, expected):
    result = flatten_markdown(input_md)
    assert result == expected


def test_flatten_markdown_is_idempotent():
    samples = [
        "# Title One\n\nSome text with (a parenthetical) and Dr. Smith.\n\n- item 1\n  - nested\n- item 2",
        "Here is a table:\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nAnd a quote:\n> First line\n> Second line",
        "pages 3–5 of the report. See e.g. chips and [the link](http://example.com).",
        "Check out https://github.com/vocalize and email info@example.com.",
        "Done  .   Next (really) .",
    ]
    for sample in samples:
        first = flatten_markdown(sample)
        second = flatten_markdown(first)
        assert first == second, f"Idempotency failed:\nFirst: {first}\nSecond: {second}"


def test_flatten_markdown_preserves_technical_tokens():
    text = "Engineers in R&D work with C++ and Go 24/7 on TCP/IP protocols."
    result = flatten_markdown(text)
    assert "R&D" in result
    assert "C++" in result
    assert "24/7" in result
    assert "TCP/IP" in result


def test_strips_bold_and_italic_markers():
    result = flatten_markdown("This is **very** important and *also* urgent.")
    assert "very important" in result
    assert "*" not in result


def test_code_block_is_replaced_not_read_verbatim():
    md = "Here's the fix:\n\n```python\ndef f():\n    return 1\n```\n\nDone."
    result = flatten_markdown(md)
    assert "def f()" not in result
    assert "Skipping a code block." in result
    # The closing fence stays silent: announcing both ends of every block ate
    # the whole spoken budget on a real Claude Code response.
    assert "End of code block" not in result
    assert "Done." in result


def test_consecutive_code_blocks_collapse_to_one_placeholder():
    md = "```python\na = 1\n```\n\n```python\nb = 2\n```\n"
    result = flatten_markdown(md)
    assert result.count("Skipping a code block.") == 1


def test_code_blocks_separated_by_prose_each_get_a_placeholder():
    md = "```python\na = 1\n```\n\nThen run it.\n\n```python\nb = 2\n```\n"
    result = flatten_markdown(md)
    assert result.count("Skipping a code block.") == 2
    assert "Then run it." in result


def test_inline_code_ticks_are_stripped():
    result = flatten_markdown("Run `pip install vocalize` to get started.")
    assert "pip install vocalize" in result
    assert "`" not in result


def test_truncate_for_budget_no_op_when_under_limit():
    text, truncated = truncate_for_budget("short text", max_chars=1000)
    assert text == "short text"
    assert truncated is False


def test_truncate_for_budget_cuts_on_word_boundary():
    text, truncated = truncate_for_budget("one two three four five", max_chars=13)
    assert truncated is True
    assert text.startswith("one two")
    assert not text.startswith("one two thre")


def test_truncate_for_budget_leaves_no_spoken_marker():
    # The result is read aloud; a literal "(truncated)" would be spoken.
    text, truncated = truncate_for_budget("one two three four five", max_chars=13)
    assert truncated is True
    assert "truncated" not in text
    assert "(" not in text


def test_truncate_for_budget_none_means_unlimited():
    long_text = "word " * 10000
    text, truncated = truncate_for_budget(long_text, max_chars=None)
    assert truncated is False
    assert text == long_text


def test_prose_line_with_pipe_before_a_horizontal_rule_is_not_a_table():
    md = "Use the pipe | operator here\n---\nNext paragraph.\n"
    result = flatten_markdown(md)
    assert "pipe" in result
    assert "operator" in result
    assert "Next paragraph." in result


def test_setext_underlined_heading_containing_a_pipe_survives():
    md = "Shell pipes | and filters\n---\nBody text.\n"
    result = flatten_markdown(md)
    assert "Shell pipes" in result
    assert "and filters" in result


def test_row_with_more_cells_than_headers_keeps_the_extra_value():
    md = (
        "| Name | Age |\n"
        "|------|-----|\n"
        "| Ada  | 34  | London |\n"
    )
    result = flatten_markdown(md)
    assert "column 3 is London" in result


def test_row_with_fewer_cells_than_headers_speaks_what_is_present():
    md = (
        "| Name | Age | City |\n"
        "|------|-----|------|\n"
        "| Ada  | 34  |\n"
    )
    result = flatten_markdown(md)
    assert "For Ada: Age is 34." in result
    assert "City is" not in result


def test_duplicate_header_names_do_not_drop_columns():
    md = (
        "| Name | Name |\n"
        "|------|------|\n"
        "| Ada  | Lovelace |\n"
    )
    result = flatten_markdown(md)
    assert "Ada" in result
    assert "Lovelace" in result


def test_single_dash_separator_is_a_table():
    md = (
        "| Metric | Q1 |\n"
        "| - | - |\n"
        "| Revenue | 4.2m |\n"
    )
    result = flatten_markdown(md)
    assert "For Revenue: Q1 is 4.2m." in result
    assert "|" not in result


def test_single_row_table_is_grammatical():
    md = (
        "| Quarter | Revenue |\n"
        "|---------|---------|\n"
        "| Q1      | 4.2M    |\n"
    )
    result = flatten_markdown(md)
    assert "Table with 1 row." in result
    assert "1 rows" not in result


def test_split_for_synthesis_short_text_is_returned_unchanged():
    text = "  short text with padding  "
    assert split_for_synthesis(text, max_chars=1000) == [text]


def test_split_for_synthesis_exact_limit_is_not_split():
    text = "x" * 50
    assert split_for_synthesis(text, max_chars=50) == [text]


def test_split_for_synthesis_prefers_paragraph_over_mid_sentence_cuts():
    para1 = "Alpha bravo charlie delta echo foxtrot golf hotel."
    para2 = "India juliet kilo lima mike november oscar papa."
    text = f"{para1}\n\n{para2}"
    assert len(para1) <= 60
    assert len(para2) <= 60
    assert len(text) > 60  # forces a split; only the paragraph gap should be used

    chunks = split_for_synthesis(text, max_chars=60)

    assert chunks == [para1, para2]
    # A mid-sentence cut would end a chunk without terminal punctuation.
    for chunk in chunks:
        assert chunk[-1] in ".!?"


def test_split_for_synthesis_long_mixed_input_stays_within_limit():
    paragraphs = [
        "First paragraph. It has two sentences.",
        (
            "Second paragraph is a fair bit longer than the first one, "
            "with several clauses strung together to pad it out some more."
        ),
        "Third short one.",
        "A" * 40,  # a single unbroken run, shorter than max_chars on its own
    ]
    text = "\n\n".join(paragraphs)

    chunks = split_for_synthesis(text, max_chars=30)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk == chunk.strip()
        assert chunk != ""
        assert len(chunk) <= 30


def test_split_for_synthesis_preserves_all_content():
    text = (
        "# Heading\n\n"
        "First paragraph with a couple of sentences. Here is the second one.\n\n"
        "Second paragraph, longer, rambling on for a while about nothing "
        "in particular just to pad out the length a bit further still.\n\n"
        "- bullet one\n- bullet two\n- bullet three\n"
    )
    chunks = split_for_synthesis(text, max_chars=40)

    rejoined = re.sub(r"\s+", " ", " ".join(chunks)).strip()
    normalized_input = re.sub(r"\s+", " ", text).strip()
    assert rejoined == normalized_input


def test_split_for_synthesis_never_merges_hard_slices_into_neighbours():
    token = "x" * 50
    text = f"see {token} end."

    chunks = split_for_synthesis(text, max_chars=20)

    # The over-long token's slices stay standalone chunks, in order, and
    # concatenate directly back into the token — no invented word breaks.
    assert chunks == ["see", "x" * 20, "x" * 20, "x" * 10, "end."]
    assert "".join(chunks[1:4]) == token


def test_split_for_synthesis_hard_slices_a_single_unbroken_run():
    text = "x" * 25000  # no spaces anywhere — nothing but a hard slice can break this up

    chunks = split_for_synthesis(text, max_chars=9500)

    assert len(chunks) == 3
    assert all(0 < len(chunk) <= 9500 for chunk in chunks)
    assert "".join(chunks) == text
