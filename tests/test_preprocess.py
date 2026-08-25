from vocalize.preprocess import flatten_markdown, truncate_for_budget


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
    assert "Code block:" in result
    assert "End of code block." in result
    assert "Done." in result


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


def test_single_row_table_is_grammatical():
    md = (
        "| Quarter | Revenue |\n"
        "|---------|---------|\n"
        "| Q1      | 4.2M    |\n"
    )
    result = flatten_markdown(md)
    assert "Table with 1 row." in result
    assert "1 rows" not in result
