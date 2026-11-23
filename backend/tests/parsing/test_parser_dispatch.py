from pathlib import Path

import pytest

from app.parsing import parser, pdf
from app.parsing.contract import FileFormat, ParseError, ParseErrorCode
from app.parsing.document import Block, RawDocument

from .builders import make_epub


def error_code(path: Path) -> ParseErrorCode:
    with pytest.raises(ParseError) as info:
        parser.parse_book(path)
    return info.value.code


@pytest.mark.parametrize(
    "content", [b"", b"hello, this is plain text", b"\x89PNG\r\n\x1a\n...", b"PK\x05\x06empty"]
)
def test_unknown_signature_is_unsupported_format(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "file.epub"
    path.write_bytes(content)
    assert error_code(path) is ParseErrorCode.UNSUPPORTED_FORMAT


def test_missing_file_is_corrupt_file(tmp_path: Path) -> None:
    assert error_code(tmp_path / "does-not-exist") is ParseErrorCode.CORRUPT_FILE


def test_directory_is_corrupt_file(tmp_path: Path) -> None:
    assert error_code(tmp_path) is ParseErrorCode.CORRUPT_FILE


def test_zip_signature_with_garbage_is_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "garbage"
    path.write_bytes(b"PK\x03\x04" + b"\x00garbage" * 50)
    assert error_code(path) is ParseErrorCode.CORRUPT_FILE


def test_file_without_extension_is_detected(tmp_path: Path) -> None:
    built = make_epub(tmp_path, docs=[("a.xhtml", "<p>Some text.</p>")])
    stored = tmp_path / "3f1c2a9e-0000-4000-8000-000000000000"
    built.rename(stored)
    assert parser._detect_format(stored) is FileFormat.EPUB
    assert parser.parse_book(stored).file_format is FileFormat.EPUB


def test_misleading_extension_is_ignored(tmp_path: Path) -> None:
    built = make_epub(tmp_path, docs=[("a.xhtml", "<p>Some text.</p>")])
    stored = tmp_path / "book.pdf"
    built.rename(stored)
    assert parser._detect_format(stored) is FileFormat.EPUB


def test_pdf_signature_routes_to_pdf_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "upload"
    path.write_bytes(b"%PDF-1.7\n...")
    calls: list[Path] = []

    def fake_read_pdf(p: Path) -> RawDocument:
        calls.append(p)
        return RawDocument(
            title=None,
            author=None,
            language=None,
            file_format=FileFormat.PDF,
            blocks=(Block("Text on page one.", 1, None),),
            toc=(),
        )

    monkeypatch.setattr(pdf, "read_pdf", fake_read_pdf)
    book = parser.parse_book(path)
    assert calls == [path]
    assert book.file_format is FileFormat.PDF
    assert book.chapters[0].paragraphs[0].page == 1
