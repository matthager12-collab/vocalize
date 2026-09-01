import re

from vocalize.preprocess import (
    flatten_markdown,
    split_for_synthesis,
    truncate_for_budget,
)


def test_flattens_simple_table_into_per_row_sentences():
    md = (
        "| Quarter | Revenue |\n"
        "|---------|---------|\n"
        "| Q1      | 4.2M    |\n"
        "| Q2      | 5.1M    |\n"
    )
    result = flatten_markdown(md)
    assert "Table with 2 rows." in result
    assert "For Q1: Revenue is 4.2M." in result
    assert "For Q2: Revenue is 5.1M." in result


def test_flattens_table_with_multiple_columns():
    md = (
        "| Name | Age | City |\n"
        "|------|-----|------|\n"
        "| Ada  | 34  | London |\n"
    )
    result = flatten_markdown(md)
    assert "For Ada: Age is 34; City is London." in result


def test_strips_headings():
    result = flatten_markdown("# Big Title\n\nSome text.")
    assert "Big Title." in result
    assert "#" not in result


def test_converts_bullet_list_to_ordinals():
    md = "- first thing\n- second thing\n- third thing\n"
    result = flatten_markdown(md)
    assert "First, first thing." in result
    assert "Second, second thing." in result
    assert "Third, third thing." in result


def test_numbered_list_becomes_item_n():
    md = "1. buy milk\n2. walk dog\n"
    result = flatten_markdown(md)
    assert "Item 1: buy milk." in result
    assert "Item 2: walk dog." in result


def test_strips_links_and_keeps_link_text():
    result = flatten_markdown("Check out [the docs](https://example.com) for more.")
    assert "the docs" in result
    assert "https://example.com" not in result
    assert "[" not in result


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
