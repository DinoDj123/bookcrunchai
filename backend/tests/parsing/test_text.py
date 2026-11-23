import pytest

from app.parsing.boilerplate import is_gutenberg_end, is_gutenberg_start
from app.parsing.text import (
    clean_title,
    cut_at_word,
    join_hyphenated,
    language_code,
    strip_footnote_markers,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("er­\nrors", "errors"),
        ("er­ \n  rors", "errors"),
        ("con-\ntent", "content"),
        ("Anglo-\nSaxon", "Anglo-Saxon"),
        ("well-known", "well-known"),
        ("end of line\nnext", "end of line\nnext"),
        ("a - \nb", "a - \nb"),
    ],
)
def test_join_hyphenated(raw: str, expected: str) -> None:
    assert join_hyphenated(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rome[*] fell", "Rome fell"),
        ("as said [12].", "as said."),
        ("note[a] here", "note here"),
        ("[*]", ""),
        ("arrays [i, j] stay", "arrays [i, j] stay"),
        ("no markers", "no markers"),
    ],
)
def test_strip_footnote_markers(raw: str, expected: str) -> None:
    assert strip_footnote_markers(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en", "en"),
        ("en-US", "en"),
        ("EN_gb", "en"),
        ("cnr-ME", "cnr"),
        (" it ", "it"),
        (None, None),
        ("", None),
        ("english", None),
        ("e", None),
        ("en US", None),
    ],
)
def test_language_code(raw: str | None, expected: str | None) -> None:
    assert language_code(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Pride   and\nPrejudice ", "Pride and Prejudice"),
        ("CHAPTER I.", "CHAPTER I"),
        ("Dedication[*]", "Dedication"),
        ("Title...", "Title"),
        (None, None),
        ("", None),
        ("   ", None),
        ("* * *", None),
        ("[*]", None),
    ],
)
def test_clean_title(raw: str | None, expected: str | None) -> None:
    assert clean_title(raw) == expected


def test_cut_at_word() -> None:
    assert cut_at_word("short", 10) == "short"
    assert cut_at_word("one two three", 9) == "one two"
    assert cut_at_word("one two three", 7) == "one two"
    assert cut_at_word("abcdefghij", 4) == "abcd"


def test_gutenberg_markers() -> None:
    assert is_gutenberg_start("*** START OF THE PROJECT GUTENBERG EBOOK PRIDE AND PREJUDICE ***")
    assert is_gutenberg_start("***START OF THIS PROJECT GUTENBERG EBOOK X")
    assert is_gutenberg_end("*** end of the project gutenberg ebook meditations ***")
    assert not is_gutenberg_start("Project Gutenberg is a library")
    assert not is_gutenberg_end("*** START OF THE PROJECT GUTENBERG EBOOK")
