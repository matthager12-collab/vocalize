"""Vocabulary corrections: deterministic, conservative, engine-agnostic."""

import time

import pytest

from vocalize import vocabulary

TERMS = ["pyproject", "sha256", "Kokoro", "DuckDB", "repository root", "uv --no-project"]


@pytest.mark.parametrize("heard,expected", [
    ("edit the py project file", "edit the pyproject file"),
    ("open duck db now", "open DuckDB now"),
    ("open duckdb now", "open DuckDB now"),
    ("the kokoro voice", "the Kokoro voice"),
    ("check the sha 256 sum", "check the sha256 sum"),
    ("run uv no project here", "run uv --no-project here"),
    ("go to the repository root first", "go to the repository root first"),
    ("go to the Repository Root first", "go to the repository root first"),
    ("try duck-db or duck_db or duck.db", "try DuckDB or DuckDB or DuckDB"),
])
def test_examples(heard, expected):
    assert vocabulary.correct(heard, TERMS) == expected


def test_punctuation_after_the_run_is_kept():
    assert vocabulary.correct("I use duck db, mostly.", TERMS) == "I use DuckDB, mostly."
    assert vocabulary.correct("Is it kokoro?", TERMS) == "Is it Kokoro?"
    assert vocabulary.correct('He said "py project".', TERMS) == 'He said "pyproject".'
    assert vocabulary.correct("(sha 256)", TERMS) == "(sha256)"


def test_sentence_starts_take_the_term_as_written():
    assert vocabulary.correct("Kokoro speaks. Duck db stores.", TERMS) == "Kokoro speaks. DuckDB stores."
    assert vocabulary.correct("Py project is here.", TERMS) == "pyproject is here."


def test_punctuation_inside_a_run_stops_it():
    # A comma or full stop between words is a boundary, not part of a term.
    assert vocabulary.correct("we said duck, db said no", TERMS) == "we said duck, db said no"
    assert vocabulary.correct("it is py. Project later", TERMS) == "it is py. Project later"


def test_longer_terms_win_over_shorter_overlapping_ones():
    terms = ["repository", "Repository Root", "root"]
    assert vocabulary.correct("the repository root is", terms) == "the Repository Root is"
    assert vocabulary.correct("a repository and a root", ["Repository", "Root"]) == "a Repository and a Root"
    # "duck db" is the longest match; "db" alone is too short to be a term.
    assert vocabulary.correct("duck db", ["duck", "DuckDB"]) == "DuckDB"


def test_multi_word_terms_and_the_four_word_limit():
    assert vocabulary.correct("set new york city now", ["New York City"]) == "set New York City now"
    assert vocabulary.correct("a b c d e f", ["abcdef"]) == "a b c d e f"  # six words: never
    assert vocabulary.correct("ab cd ef gh", ["abcdefgh"]) == "abcdefgh"


def test_exact_term_is_a_no_op():
    text = "Kokoro, DuckDB and pyproject with uv --no-project."
    assert vocabulary.correct(text, TERMS) == text


def test_short_terms_are_never_matched():
    assert vocabulary.correct("u v is fast and so is a i", ["uv", "AI", "c++"]) == "u v is fast and so is a i"
    assert vocabulary.correct("it is ok", ["OK"]) == "it is ok"


def test_empty_vocabulary_and_text():
    assert vocabulary.correct("py project", []) == "py project"
    assert vocabulary.correct("py project", None) == "py project"
    assert vocabulary.correct("", TERMS) == ""


def test_no_fuzzy_matching():
    assert vocabulary.correct("kokora and duck dbs and py projects", TERMS) == \
        "kokora and duck dbs and py projects"


PASSAGE_PARAGRAPH = (
    "On a cold morning in late November, the harbour town woke slowly. Fishermen "
    "checked their nets along the stone wall, and the baker opened his shutters to "
    "let the smell of fresh bread drift across the square. Children walked to school "
    "in small groups, talking about the storm that had passed in the night; a few of "
    "them stopped to look at the broken branches near the church. Mrs. Ellis, who ran "
    "the post office, said the wind had been the strongest in twenty years. Her "
    "neighbour disagreed, of course, and the two of them argued about it until the "
    "first customers arrived. By noon the sky had cleared, the gulls were back on the "
    "roofs, and the ferry left on time for the island, carrying 42 passengers, three "
    "cars and a crate of oranges.\n\n"
)


def test_two_thousand_word_plain_passage_is_unchanged():
    words = len(PASSAGE_PARAGRAPH.split())
    passage = PASSAGE_PARAGRAPH * (2000 // words + 1)
    assert len(passage.split()) >= 2000
    assert vocabulary.correct(passage, TERMS) == passage


def test_fast_on_a_long_transcript():
    text = ("so we opened the py project file and checked the sha 256 of the duck db "
            "release before kokoro read it out loud ") * 1000  # 20,000+ words
    assert len(text.split()) >= 20000
    started = time.perf_counter()
    out = vocabulary.correct(text, TERMS)
    assert time.perf_counter() - started < 1.0
    assert out.count("pyproject") == out.count("DuckDB") == out.count("Kokoro") == 1000
