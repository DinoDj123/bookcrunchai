import re
import time
from pathlib import Path

import pytest

from app.parsing.contract import FileFormat, ParseError, ParseErrorCode
from app.parsing.document import Block, RawDocument, TocEntry
from app.parsing.pdf import read_pdf

from .builders import PDF_PAGE_WIDTH, PdfLine, make_pdf

BOOKS = Path(__file__).parent.parent / "fixtures" / "books"

# Synthetic layout: 10 pt body text on a 12 pt line grid, left margin 50, first-line indent 62.
BODY = 10.0
MARGIN = 50.0
INDENT = 62.0
STEP = 12.0
TOP = 100.0
PDF_CENTRE = PDF_PAGE_WIDTH / 2


def filler(n: int, tag: str = "w") -> list[str]:
    """n full-width body lines (about 60 characters each, wider than 85 % of the column)."""
    return [f"{tag}{i} ordinary words that fill one whole line of body text here" for i in range(n)]


def column(*paragraphs: list[str], top: float = TOP, indent: bool = True) -> list[PdfLine]:
    """Body lines on the 12 pt grid; the first line of each paragraph is indented."""
    lines: list[PdfLine] = []
    y = top
    for paragraph in paragraphs:
        for i, text in enumerate(paragraph):
            x = INDENT if i == 0 and indent else MARGIN
            lines.append((text, x, y, BODY))
            y += STEP
    return lines


def texts(doc: RawDocument) -> list[str]:
    return [b.text for b in doc.blocks]


def error_code(path: Path) -> ParseErrorCode:
    with pytest.raises(ParseError) as info:
        read_pdf(path)
    return info.value.code


# --------------------------------------------------------------------------- errors


def test_password_protected_pdf_is_encrypted_file(tmp_path: Path) -> None:
    path = make_pdf(tmp_path, [column(filler(5))], password="secret")
    assert error_code(path) is ParseErrorCode.ENCRYPTED_FILE


def test_pages_without_text_are_no_text_layer(tmp_path: Path) -> None:
    path = make_pdf(tmp_path, [[], [], []])
    assert error_code(path) is ParseErrorCode.NO_TEXT_LAYER


def test_too_little_text_per_page_is_no_text_layer(tmp_path: Path) -> None:
    path = make_pdf(tmp_path, [[("Page 1", MARGIN, TOP, BODY)], [], []])
    assert error_code(path) is ParseErrorCode.NO_TEXT_LAYER


def test_truncated_pdf_is_corrupt_file(tmp_path: Path) -> None:
    whole = make_pdf(tmp_path, [column(filler(5))]).read_bytes()
    path = tmp_path / "truncated.pdf"
    path.write_bytes(whole[:200])  # PyMuPDF raises FileDataError
    assert error_code(path) is ParseErrorCode.CORRUPT_FILE


def test_pdf_repaired_to_zero_pages_is_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "head.pdf"
    # The first 2 KB of a real book open after repair, but with no pages at all.
    path.write_bytes((BOOKS / "the-prince.pdf").read_bytes()[:2000])
    assert error_code(path) is ParseErrorCode.CORRUPT_FILE


def test_pdf_signature_with_garbage_is_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"\x00garbage" * 50)
    assert error_code(path) is ParseErrorCode.CORRUPT_FILE


# --------------------------------------------------------------------------- metadata and outline


def test_reads_metadata_and_language(tmp_path: Path) -> None:
    path = make_pdf(
        tmp_path,
        [column(filler(5))],
        metadata={"title": "  My   Book ", "author": "Jane Doe"},
        language="en-GB",
    )
    doc = read_pdf(path)
    assert (doc.title, doc.author, doc.language) == ("My Book", "Jane Doe", "en-GB")
    assert doc.file_format is FileFormat.PDF


@pytest.mark.parametrize(
    "junk",
    ["Microsoft Word - draft.docx", "untitled", "Untitled", "scan_0012.pdf", "Chapter1.docx", "  "],
)
def test_junk_metadata_title_is_none(tmp_path: Path, junk: str) -> None:
    path = make_pdf(tmp_path, [column(filler(5))], metadata={"title": junk, "author": junk})
    doc = read_pdf(path)
    assert (doc.title, doc.author, doc.language) == (None, None, None)


def test_outline_read_with_pages_and_entries_without_page_dropped(tmp_path: Path) -> None:
    pages = [column(filler(5, f"p{n}")) for n in range(3)]
    outline = [(1, "The Book", 1), (2, "Chapter[*]  One", 2), (2, "Two", 3), (1, "Share", -1)]
    doc = read_pdf(make_pdf(tmp_path, pages, outline=outline))
    assert doc.toc == (
        TocEntry("The Book", 1, 1),
        TocEntry("Chapter One", 2, 2),
        TocEntry("Two", 2, 3),
    )


# --------------------------------------------------------------------------- headers and footers


def test_repeated_header_and_page_numbers_removed(tmp_path: Path) -> None:
    pages = [column(filler(4, f"p{n}x")) for n in range(1, 7)]
    doc = read_pdf(make_pdf(tmp_path, pages, header="My Running Header", footer_numbers=True))
    assert not any("Running Header" in t for t in texts(doc))
    assert not any(t.isdigit() for t in texts(doc))
    assert [b.page for b in doc.blocks] == [1, 2, 3, 4, 5, 6]
    assert all(t.startswith(f"p{n}x0 ") for n, t in enumerate(texts(doc), start=1))


def test_alternating_odd_and_even_headers_removed(tmp_path: Path) -> None:
    pages = []
    for n in range(1, 9):
        header = "Free Books at Example.com" if n % 2 else "The Book Title"
        pages.append([(header, MARGIN, 40.0, 9.0), *column(filler(4, f"p{n}x"))])
    doc = read_pdf(make_pdf(tmp_path, pages, footer_numbers=True))
    assert len(doc.blocks) == 8
    assert not any("Free Books" in t or "Book Title" in t for t in texts(doc))


def test_body_text_in_margin_zone_is_kept_when_not_repeated(tmp_path: Path) -> None:
    pages = [column(filler(4, f"p{n}x")) for n in range(1, 5)]
    pages[1].append(("A closing line that sits low on the page.", MARGIN, 560.0, BODY))
    doc = read_pdf(make_pdf(tmp_path, pages))
    assert any("A closing line that sits low on the page." in t for t in texts(doc))


def test_lone_roman_numeral_at_page_top_is_kept_as_chapter_label(tmp_path: Path) -> None:
    pages = [column(filler(3, f"p{n}x"), ["The end."]) for n in range(1, 6)]
    pages[1].append(("I", MARGIN, 40.0, BODY))
    doc = read_pdf(make_pdf(tmp_path, pages, footer_numbers=True))
    assert "I" in texts(doc)
    assert not any(t.isdigit() for t in texts(doc))


def test_roman_page_numbers_in_bottom_zone_are_removed(tmp_path: Path) -> None:
    numerals = ["i", "ii", "iii", "iv", "v"]
    pages = [
        [*column(filler(4, f"p{n}x")), (numerals[n - 1], PDF_CENTRE, 570.0, 9.0)]
        for n in range(1, 6)
    ]
    doc = read_pdf(make_pdf(tmp_path, pages))
    assert len(doc.blocks) == 5
    assert not any(t in numerals for t in texts(doc))


def test_roman_numeral_is_kept_when_the_numbers_are_in_the_other_zone(tmp_path: Path) -> None:
    # Arabic page numbers at the bottom (3+ pages) do not make a top-zone "I" a page number.
    pages = [column(filler(3, f"p{n}x"), ["The end."]) for n in range(1, 6)]
    pages[2].append(("I", MARGIN, 40.0, BODY))
    doc = read_pdf(make_pdf(tmp_path, pages, footer_numbers=True))
    assert "I" in texts(doc)


# --------------------------------------------------------------------------- hyphens and paragraphs


def test_soft_and_hard_hyphen_joins(tmp_path: Path) -> None:
    lines = [
        "Here the first line breaks inside er\xad",
        "rors and then a hard con-",
        "tent split and a name Anglo-",
        "Saxon at the end.",
    ]
    doc = read_pdf(make_pdf(tmp_path, [column(lines, filler(3))]))
    assert texts(doc)[0] == (
        "Here the first line breaks inside errors and then a hard content split "
        "and a name Anglo-Saxon at the end."
    )


def test_first_line_indent_starts_a_paragraph(tmp_path: Path) -> None:
    doc = read_pdf(make_pdf(tmp_path, [column(filler(3, "a"), filler(2, "b"), filler(4, "c"))]))
    assert [t.split()[0] for t in texts(doc)] == ["a0", "b0", "c0"]
    assert texts(doc)[1] == " ".join(filler(2, "b"))


def test_page_of_only_indented_one_line_paragraphs_keeps_every_paragraph(tmp_path: Path) -> None:
    normal = column(filler(3, "a"), filler(2, "b"), filler(4, "c"))
    only_indented = [
        (f"s{i} a short indented paragraph of one line only", INDENT, TOP + i * STEP, BODY)
        for i in range(4)
    ]
    pages = [normal, normal, normal, only_indented, normal, normal]
    doc = read_pdf(make_pdf(tmp_path, pages))
    on_page_four = [t for b, t in zip(doc.blocks, texts(doc), strict=True) if b.page == 4]
    assert [t.split()[0] for t in on_page_four] == ["s0", "s1", "s2", "s3"]


def test_block_quote_of_indented_full_lines_is_one_block(tmp_path: Path) -> None:
    before, after = column(filler(3, "a")), column(filler(2, "b"))
    quote = [(t, INDENT, TOP + (3 + i) * STEP, BODY) for i, t in enumerate(filler(4, "q"))]
    after = [(t, MARGIN, y + 8 * STEP, size) for t, _, y, size in after]
    page = [*before, *quote, *after]
    normal = column(filler(3, "c"), filler(3, "d"))
    doc = read_pdf(make_pdf(tmp_path, [page, normal, normal, normal, normal]))
    firsts = [t.split()[0] for t in texts(doc)]
    assert firsts[:3] == ["a0", "q0", "b0"]
    assert texts(doc)[1] == " ".join(filler(4, "q"))


def test_vertical_gap_starts_a_paragraph(tmp_path: Path) -> None:
    first = column(filler(3, "a"))
    second = column(filler(3, "b"), top=TOP + 5 * STEP, indent=False)
    doc = read_pdf(make_pdf(tmp_path, [first + second]))
    assert [t.split()[0] for t in texts(doc)] == ["a0", "b0"]


def test_paragraph_across_page_break_keeps_first_page(tmp_path: Path) -> None:
    page_one = column(filler(5, "a"))
    page_two = [
        ("on the next page it ends here.", MARGIN, TOP, BODY),
        *column(filler(3, "b"), top=TOP + STEP),
    ]
    doc = read_pdf(make_pdf(tmp_path, [page_one, page_two]))
    assert texts(doc)[0].endswith(
        "a4 ordinary words that fill one whole line of body text here "
        "on the next page it ends here."
    )
    assert doc.blocks[0].page == 1
    assert [b.page for b in doc.blocks] == [1, 2]


def test_short_last_line_with_full_stop_ends_paragraph_at_page_break(tmp_path: Path) -> None:
    page_one = column([*filler(4, "a"), "Short end."])
    page_two = column(filler(4, "b"), indent=False)
    doc = read_pdf(make_pdf(tmp_path, [page_one, page_two]))
    assert [(t.split()[0], b.page) for t, b in zip(texts(doc), doc.blocks, strict=True)] == [
        ("a0", 1),
        ("b0", 2),
    ]
    assert texts(doc)[0].endswith("Short end.")


# --------------------------------------------------------------------------- drop caps and headings


def test_drop_cap_joined_to_first_word(tmp_path: Path) -> None:
    lines: list[PdfLine] = [
        ("N", MARGIN, 120.0, 28.5),
        ("icolo was born at Florence on the third of May in", 72.0, 104.0, BODY),
        ("the year fourteen sixty nine and he was the second", 72.0, 116.0, BODY),
        ("son of Bernardo, a lawyer of some repute in town.", MARGIN, 128.0, BODY),
        *column(filler(4, "b"), top=140.0),
    ]
    doc = read_pdf(make_pdf(tmp_path, [lines]))
    assert texts(doc)[0] == (
        "Nicolo was born at Florence on the third of May in the year fourteen sixty nine "
        "and he was the second son of Bernardo, a lawyer of some repute in town."
    )
    assert texts(doc)[1].startswith("b0 ")


def test_drop_cap_with_space_is_a_word(tmp_path: Path) -> None:
    lines: list[PdfLine] = [
        ("I ", MARGIN, 120.0, 28.5),
        ("will leave out all discussion on republics, inasmuch", 62.0, 104.0, BODY),
        ("as in another place I have written of them at length.", 62.0, 116.0, BODY),
        *column(filler(4, "b"), top=140.0),
    ]
    doc = read_pdf(make_pdf(tmp_path, [lines]))
    assert texts(doc)[0].startswith("I will leave out all discussion on republics, inasmuch as in")


def test_large_multi_line_heading_merged_into_one_block(tmp_path: Path) -> None:
    title_page = [("The Book", MARGIN, 100.0, 24.0), *column(filler(4, "t"), top=200.0)]
    chapter_page = [
        ("CHAPTER I. HOW MANY", MARGIN, 80.0, 20.0),
        ("KINDS OF THINGS THERE", MARGIN, 104.0, 20.0),
        ("ARE", MARGIN, 128.0, 20.0),
        *column(filler(4, "c"), top=180.0),
    ]
    doc = read_pdf(make_pdf(tmp_path, [title_page, chapter_page]))
    headings = [(b.text, b.heading_level, b.page) for b in doc.blocks if b.heading_level]
    assert headings == [("The Book", 1, 1), ("CHAPTER I. HOW MANY KINDS OF THINGS THERE ARE", 2, 2)]
    assert texts(doc)[-1].startswith("c0 ")


def test_short_all_caps_paragraphs_are_level_3_headings(tmp_path: Path) -> None:
    lines = [
        *column(filler(3, "a")),
        ("DEDICATION", MARGIN, TOP + 5 * STEP, BODY),
        *column(
            ["THE LIFE OF CASTRUCCIO CASTRACANI OF LUC­", "CA"],
            ["And sent to his friends ZANOBI BUONDELMONTI"],
            filler(3, "b"),
            top=TOP + 7 * STEP,
        ),
    ]
    doc = read_pdf(make_pdf(tmp_path, [lines]))
    assert [(b.text.split()[0], b.heading_level) for b in doc.blocks] == [
        ("a0", None),
        ("DEDICATION", 3),
        ("THE", 3),
        ("And", None),
        ("b0", None),
    ]
    assert doc.blocks[2] == Block("THE LIFE OF CASTRUCCIO CASTRACANI OF LUCCA", 1, 3)


# --------------------------------------------------------------------------- footnotes


def test_small_font_lines_at_page_bottom_dropped(tmp_path: Path) -> None:
    lines = [
        *column(filler(4, "a")),
        ("A small aside high on the page stays.", MARGIN, 200.0, 7.0),
        ("1. A footnote in small type at the bottom of the page.", MARGIN, 520.0, 7.0),
        ("that continues on a second small line.", MARGIN, 529.0, 7.0),
    ]
    doc = read_pdf(make_pdf(tmp_path, [lines]))
    assert not any("footnote" in t or "second small line" in t for t in texts(doc))
    assert any("A small aside high on the page stays." in t for t in texts(doc))


def test_footnote_paragraph_after_heading_dropped_and_inline_markers_removed(
    tmp_path: Path,
) -> None:
    lines = [
        ("CHAPTER XVIII[*].", MARGIN, 80.0, 20.0),
        ("[*] The present chapter has given greater offence than any", MARGIN, 130.0, BODY),
        ("other portion of the writings. Burd, p. 297.", MARGIN, 142.0, BODY),
        *column(
            [
                "Every one admits how praiseworthy it is in a prince to keep",
                "faith, there are two ways of contesting,[*] the one by law.",
            ],
            filler(3),
            top=190.0,
        ),
    ]
    doc = read_pdf(make_pdf(tmp_path, [lines]))
    assert texts(doc)[0] == "CHAPTER XVIII."
    assert texts(doc)[1] == (
        "Every one admits how praiseworthy it is in a prince to keep faith, there are two "
        "ways of contesting, the one by law."
    )
    assert not any("offence" in t for t in texts(doc))


def test_footnote_paragraphs_in_running_text_dropped(tmp_path: Path) -> None:
    lines = column(
        filler(3, "a"),
        ["[+] Louis XI, son of the above, born 1423, died 1483."],
        ["[*] During the rioting between the Cancellieri and Pan\xad", "ciatichi factions."],
        filler(3, "b"),
    )
    doc = read_pdf(make_pdf(tmp_path, [lines]))
    assert [t.split()[0] for t in texts(doc)] == ["a0", "b0"]


# --------------------------------------------------------------------------- the real book


@pytest.fixture(scope="module")
def prince() -> tuple[RawDocument, float]:
    start = time.perf_counter()
    doc = read_pdf(BOOKS / "the-prince.pdf")
    return doc, time.perf_counter() - start


def test_prince_parses_fast(prince: tuple[RawDocument, float]) -> None:
    assert prince[1] < 10.0


def test_prince_metadata_and_outline(prince: tuple[RawDocument, float]) -> None:
    doc = prince[0]
    assert (doc.title, doc.author, doc.language) == ("The Prince", "Nicolo Machiavelli", None)
    assert len(doc.toc) == 28
    assert doc.toc[0] == TocEntry("The Prince", 1, 1)
    assert doc.toc[1] == TocEntry("INTRODUCTION", 2, 3)
    assert doc.toc[-1].target == 145
    assert all(isinstance(e.target, int) and e.target >= 1 for e in doc.toc)


def test_prince_has_no_running_footer_or_page_numbers(prince: tuple[RawDocument, float]) -> None:
    for text in texts(prince[0]):
        assert "Free eBooks at Planet eBook" not in text
        assert not re.fullmatch(r"[\d-]+", text)


def test_prince_hyphenation_joined(prince: tuple[RawDocument, float]) -> None:
    for text in texts(prince[0]):
        assert "\xad" not in text
        assert "er rors" not in text
        assert "princ es" not in text


def test_prince_drop_cap_and_first_chapter(prince: tuple[RawDocument, float]) -> None:
    doc = prince[0]
    assert any(t.startswith("All states, all powers") for t in texts(doc))
    chapters = [b for b in doc.blocks if b.heading_level and b.text.startswith("CHAPTER")]
    assert chapters[0].text.startswith("CHAPTER I. HOW MANY KINDS OF PRINCIPALITIES")
    assert chapters[0].page == 19
    assert len(chapters) == 26


def test_prince_extra_works_have_heading_blocks(prince: tuple[RawDocument, float]) -> None:
    headings = {b.text: b.page for b in prince[0].blocks if b.heading_level == 3}
    assert headings["THE LIFE OF CASTRUCCIO CASTRACANI OF LUCCA"] == 159
    description = [t for t in headings if t.startswith("DESCRIPTION OF THE METHODS ADOPTED BY")]
    assert [headings[t] for t in description] == [150]


def test_prince_footnotes_removed(prince: tuple[RawDocument, float]) -> None:
    for text in texts(prince[0]):
        assert not re.search(r"\[[*+%#&$]\]", text)
    assert not any(t.startswith("Duke Lodovico was Lodovico Moro") for t in texts(prince[0]))


def test_prince_pages_non_decreasing(prince: tuple[RawDocument, float]) -> None:
    pages = [b.page for b in prince[0].blocks]
    assert all(isinstance(p, int) for p in pages)
    assert pages == sorted(pages)  # type: ignore[type-var]


def test_prince_has_no_publisher_promo(prince: tuple[RawDocument, float]) -> None:
    for text in texts(prince[0]):
        assert "planet ebook" not in text.lower()
