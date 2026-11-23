"""One place that lists every ParseErrorCode with the synthetic file that triggers it.

Some codes are also tested next to the reader that raises them (test_epub, test_pdf,
test_parser_dispatch); here each case goes through `parse_book()`, as the worker calls it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.parsing.contract import USER_MESSAGES, ParseError, ParseErrorCode
from app.parsing.parser import parse_book

from .builders import encryption_xml, make_epub, make_pdf, paragraphs
from .test_epub import GUTENBERG_FOOTER, GUTENBERG_HEADER

BODY_LINES = [
    (f"w{i} ordinary words that fill one whole line of body text here", 50.0, 100.0 + 12 * i, 10.0)
    for i in range(10)
]

Factory = Callable[[Path], Path]


def _random_bytes(tmp_path: Path) -> Path:
    path = tmp_path / "random.epub"
    path.write_bytes(bytes(range(256)) * 8)
    return path


def _text_file(tmp_path: Path) -> Path:
    path = tmp_path / "notes.txt"
    path.write_text("Just a plain text file, not a book.\n", encoding="utf-8")
    return path


def _truncated_epub(tmp_path: Path) -> Path:
    whole = make_epub(tmp_path, docs=[("a.xhtml", paragraphs(20))]).read_bytes()
    path = tmp_path / "truncated.epub"
    path.write_bytes(whole[: len(whole) // 2])  # the central directory is at the end
    return path


def _truncated_pdf(tmp_path: Path) -> Path:
    whole = make_pdf(tmp_path, [BODY_LINES]).read_bytes()
    path = tmp_path / "truncated.pdf"
    path.write_bytes(whole[:200])
    return path


def _missing_file(tmp_path: Path) -> Path:
    return tmp_path / "does-not-exist.epub"


def _encrypted_epub(tmp_path: Path) -> Path:
    return make_epub(
        tmp_path,
        docs=[("a.xhtml", "<p>Scrambled.</p>")],
        encryption_xml=encryption_xml(
            "http://www.w3.org/2001/04/xmlenc#aes128-cbc", "OEBPS/a.xhtml"
        ),
    )


def _password_pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path, [BODY_LINES], password="secret")


def _blank_pages_pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path, [[], [], []])


def _boilerplate_only_epub(tmp_path: Path) -> Path:
    return make_epub(tmp_path, docs=[("a.xhtml", GUTENBERG_HEADER + GUTENBERG_FOOTER)])


CASES: dict[ParseErrorCode, dict[str, Factory]] = {
    ParseErrorCode.UNSUPPORTED_FORMAT: {"random bytes": _random_bytes, "txt file": _text_file},
    ParseErrorCode.CORRUPT_FILE: {
        "truncated epub": _truncated_epub,
        "truncated pdf": _truncated_pdf,
        "missing file": _missing_file,
    },
    ParseErrorCode.ENCRYPTED_FILE: {"drm epub": _encrypted_epub, "password pdf": _password_pdf},
    ParseErrorCode.NO_TEXT_LAYER: {"blank pages pdf": _blank_pages_pdf},
    ParseErrorCode.EMPTY_BOOK: {"gutenberg boilerplate only": _boilerplate_only_epub},
}

ALL_CASES = [
    pytest.param(code, factory, id=f"{code.value}-{name}")
    for code, factories in CASES.items()
    for name, factory in factories.items()
]


def test_every_error_code_has_a_case() -> None:
    assert set(CASES) == set(ParseErrorCode)
    assert all(CASES[code] for code in ParseErrorCode)


@pytest.mark.parametrize(("code", "factory"), ALL_CASES)
def test_error_code_and_user_message(
    tmp_path: Path, code: ParseErrorCode, factory: Factory
) -> None:
    path = factory(tmp_path)
    with pytest.raises(ParseError) as info:
        parse_book(path)
    assert info.value.code is code
    assert info.value.user_message == USER_MESSAGES[code]
