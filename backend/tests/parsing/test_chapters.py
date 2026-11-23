"""Chapter detection (design §5) on hand-built RawDocuments, plus the three real books."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.parsing.chapters import (
    BACK_KEYWORDS,
    CHAPTER_PATTERN,
    DROP_KEYWORDS,
    FRONT_KEYWORDS,
    build_chapters,
    classify_kind,
)
from app.parsing.contract import (
    Chapter,
    ChapterKind,
    FileFormat,
    ParsedBook,
    validate_parsed_book,
)
from app.parsing.document import Block, RawDocument, TocEntry
from app.parsing.parser import parse_book

BOOKS = Path(__file__).parent.parent / "fixtures" / "books"
F, C, B = ChapterKind.FRONT_MATTER, ChapterKind.CHAPTER, ChapterKind.BACK_MATTER

BODY = " ".join(["word"] * 40)  # a 40-word paragraph


def para(text: str = BODY, page: int | None = None) -> Block:
    return Block(text, page, None)


def head(text: str, level: int = 2, page: int | None = None, *anchors: str) -> Block:
    return Block(text, page, level, tuple(anchors))


def doc(
    blocks: list[Block],
    toc: list[TocEntry] | None = None,
    title: str | None = "Some Book",
    fmt: FileFormat = FileFormat.EPUB,
) -> RawDocument:
    return RawDocument(title, None, "en", fmt, tuple(blocks), tuple(toc or ()))


def check(raw: RawDocument) -> tuple[tuple[Chapter, ...], bool]:
    chapters, detected = build_chapters(raw)
    validate_parsed_book(ParsedBook("Some Book", None, "en", raw.file_format, detected, chapters))
    return chapters, detected


def summary(chapters: tuple[Chapter, ...]) -> list[tuple[str, ChapterKind]]:
    return [(c.title, c.kind) for c in chapters]


# ------------------------------------------------------------------ TOC mode


def test_toc_with_anchors_splits_at_the_right_blocks() -> None:
    blocks = [
        head("Chapter One", 2, None, "a.xhtml#c1"),
        para("one a"),
        para("one b"),
        head("Chapter Two", 2, None, "a.xhtml#c2"),
        para("two a"),
    ]
    toc = [TocEntry("Chapter One", 1, "a.xhtml#c1"), TocEntry("Chapter Two", 1, "a.xhtml#c2")]
    chapters, detected = check(doc(blocks, toc))
    assert detected
    assert summary(chapters) == [("Chapter One", C), ("Chapter Two", C)]
    assert [p.text for p in chapters[0].paragraphs] == ["one a", "one b"]
    assert [c.position for c in chapters] == [1, 2]


def test_title_comes_from_the_heading_block_not_the_toc_label() -> None:
    blocks = [
        head("CHAPTER I.", 2, None, "a#1"),
        para(),
        head("CHAPTER II.", 2, None, "a#2"),
        para(),
    ]
    toc = [
        TocEntry("Chapter I.", 1, "a#1"),
        TocEntry("He rode a black horse. CHAPTER II.", 1, "a#2"),
    ]
    chapters, _ = check(doc(blocks, toc))
    assert [c.title for c in chapters] == ["CHAPTER I", "CHAPTER II"]


def test_toc_label_is_used_when_the_target_is_not_a_heading() -> None:
    blocks = [
        Block("one a", None, None, ("a#1",)),
        para("one b"),
        Block("two a", None, None, ("a#2",)),
        para("two b"),
    ]
    toc = [TocEntry("The Start", 1, "a#1"), TocEntry("The End", 1, "a#2")]
    chapters, _ = check(doc(blocks, toc))
    assert summary(chapters) == [("The Start", C), ("The End", C)]
    assert chapters[0].paragraphs[0].text == "one a"  # not a heading: stays text


def test_toc_entry_on_the_same_block_is_merged_keeping_the_first_title() -> None:
    blocks = [
        head("Chapter 1", 2, None, "a#1", "a#1b"),
        para(),
        head("Chapter 2", 2, None, "a#2"),
        para(),
    ]
    toc = [
        TocEntry("Chapter 1", 1, "a#1"),
        TocEntry("Sub heading", 1, "a#1b"),
        TocEntry("Chapter 2", 1, "a#2"),
    ]
    chapters, _ = check(doc(blocks, toc))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]


def test_nested_toc_uses_the_level_matching_chapter_patterns() -> None:
    blocks = [
        head("Chapter 1", 2, None, "a#1"),
        para(),
        head("A subsection", 3, None, "a#s1"),
        para(),
        head("Chapter 2", 2, None, "a#2"),
        para(),
    ]
    toc = [
        TocEntry("Chapter 1", 1, "a#1"),
        TocEntry("A subsection", 2, "a#s1"),
        TocEntry("Chapter 2", 1, "a#2"),
    ]
    chapters, _ = check(doc(blocks, toc))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]
    assert chapters[0].paragraphs[1].text == "A subsection"

    deep = [
        TocEntry("Volume", 1, "a#1"),
        TocEntry("Chapter 1", 2, "a#1"),
        TocEntry("Chapter 2", 2, "a#2"),
    ]
    chapters, _ = check(doc(blocks, deep))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]


def test_toc_without_pattern_titles_uses_level_one() -> None:
    blocks = [
        head("The Storm", 2, None, "a#1"),
        para(),
        head("Calm", 3, None, "a#1b"),
        para(),
        head("The Shore", 2, None, "a#2"),
        para(),
    ]
    toc = [
        TocEntry("The Storm", 1, "a#1"),
        TocEntry("Calm", 2, "a#1b"),
        TocEntry("The Shore", 1, "a#2"),
    ]
    chapters, _ = check(doc(blocks, toc))
    assert [c.title for c in chapters] == ["The Storm", "The Shore"]


def test_entry_equal_to_book_title_is_skipped() -> None:
    blocks = [
        head("PRIDE. and PREJUDICE", 1, None, "a#t"),
        para("by Jane"),
        head("Chapter 1", 2, None, "a#1"),
        para(),
        head("Chapter 2", 2, None, "a#2"),
        para(),
    ]
    toc = [
        TocEntry("PRIDE. and PREJUDICE", 1, "a#t"),
        TocEntry("Chapter 1", 1, "a#1"),
        TocEntry("Chapter 2", 1, "a#2"),
    ]
    chapters, _ = check(doc(blocks, toc, title="Pride and Prejudice"))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]


def test_pdf_toc_by_page_uses_the_first_heading_on_that_page() -> None:
    blocks = [
        para("front", 1),
        head("Chapter 1", 2, 2),
        para(BODY, 2),
        para(BODY, 3),
        para(BODY, 4),
        head("Chapter 2", 2, 4),
        para(BODY, 4),
    ]
    toc = [TocEntry("Chapter 1", 1, 2), TocEntry("Chapter 2", 1, 4)]
    chapters, _ = check(doc(blocks, toc, fmt=FileFormat.PDF))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]
    # the heading on page 4 is the boundary, so the page-4 paragraph before it stays in chapter 1
    assert len(chapters[0].paragraphs) == 3
    assert [p.page for p in chapters[1].paragraphs] == [4]


def test_pdf_toc_page_without_heading_uses_first_block_on_the_page() -> None:
    blocks = [para("a", 1), para("b", 1), para("c", 2), para("d", 2), para("e", 3)]
    toc = [TocEntry("Start", 1, 1), TocEntry("Later", 1, 2)]
    chapters, _ = check(doc(blocks, toc, fmt=FileFormat.PDF))
    assert [c.title for c in chapters] == ["Start", "Later"]
    assert [p.text for p in chapters[1].paragraphs] == ["c", "d", "e"]


def test_keyword_heading_not_in_toc_adds_a_boundary() -> None:
    blocks = [
        head("INTRODUCTION", 2, 1),
        para(BODY, 1),
        head("DEDICATION", 3, 2),
        para(BODY, 2),
        head("CHAPTER I. HOW", 2, 3),
        para(BODY, 3),
        head("CHAPTER II. WHY", 2, 4),
        para(BODY, 4),
    ]
    toc = [
        TocEntry("INTRODUCTION", 1, 1),
        TocEntry("CHAPTER I. HOW", 1, 3),
        TocEntry("CHAPTER II. WHY", 1, 4),
    ]
    chapters, _ = check(doc(blocks, toc, fmt=FileFormat.PDF))
    assert summary(chapters) == [
        ("INTRODUCTION", F),
        ("DEDICATION", F),
        ("CHAPTER I. HOW", C),
        ("CHAPTER II. WHY", C),
    ]


def test_toc_with_one_position_falls_through_to_patterns() -> None:
    blocks = [
        head("Chapter 1", 2, None, "a#1"),
        para(),
        head("Chapter 2", 2),
        para(),
    ]
    chapters, detected = check(doc(blocks, [TocEntry("Everything", 1, "a#1")]))
    assert detected
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]


# -------------------------------------------------------------- pattern mode


@pytest.mark.parametrize(
    "heading_texts",
    [
        ["Chapter 7", "Chapter 8", "Chapter 9"],
        ["CHAPTER VII", "CHAPTER VIII"],
        ["Book II", "Book III"],
        ["THE FIRST BOOK", "THE SECOND BOOK"],
        ["Glava 3", "Glava 4", "Glava 5"],
        ["CHAPTERXXVII.", "CHAPTERXXVIII."],
    ],
)
def test_pattern_detection_without_toc(heading_texts: list[str]) -> None:
    blocks: list[Block] = []
    for text in heading_texts:
        blocks += [head(text), para()]
    chapters, detected = check(doc(blocks))
    assert detected
    assert [c.title for c in chapters] == [t.rstrip(".") for t in heading_texts]
    assert all(c.kind is C for c in chapters)


def test_pattern_regex_rejects_ordinary_headings() -> None:
    for text in ["Partial results", "Bookkeeping", "The Chapter House", "Diocese"]:
        assert CHAPTER_PATTERN.match(text) is None


def test_recurring_level_one_headings_without_pattern() -> None:
    blocks: list[Block] = []
    for name in ["Dawn", "Noon", "Dusk"]:
        blocks += [head(name, 1), para()]
    chapters, detected = check(doc(blocks))
    assert detected
    assert [c.title for c in chapters] == ["Dawn", "Noon", "Dusk"]


def test_fallback_when_nothing_is_found() -> None:
    chapters, detected = check_fallback(doc([head("Lone heading", 1), para(), para()]))
    assert detected is False
    assert [c.title for c in chapters] == ["Part 1"]


def check_fallback(raw: RawDocument) -> tuple[tuple[Chapter, ...], bool]:
    return build_chapters(raw)


def test_empty_heading_is_merged_forward() -> None:
    blocks = [
        head("VOLUME I"),
        head("CHAPTER I"),
        para(),
        head("CHAPTER II"),
        para(),
        head("VOLUME II"),
        head("CHAPTER III"),
        para(),
    ]
    chapters, _ = check(doc(blocks))
    assert [c.title for c in chapters] == ["CHAPTER I", "CHAPTER II", "CHAPTER III"]
    assert all(len(c.paragraphs) == 1 for c in chapters)


# ----------------------------------------------------------- front/back text


def test_text_before_first_boundary_is_front_matter_only_from_30_words() -> None:
    chapters_blocks = [head("Chapter 1"), para(), head("Chapter 2"), para()]
    long_front = [head("A Title", 1), para(BODY)]
    chapters, _ = check(doc(long_front + chapters_blocks))
    assert summary(chapters)[0] == ("Front matter", F)
    assert chapters[0].paragraphs[0].text == "A Title"

    short_front = [para("Just a few words here.")]
    chapters, _ = check(doc(short_front + chapters_blocks))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]


def test_contents_and_list_of_illustrations_are_dropped() -> None:
    blocks = [
        head("Contents"),
        para("Chapter 1 3"),
        head("List of Illustrations"),
        para("A frontispiece"),
        head("Chapter 1"),
        para(),
        head("Chapter 2"),
        para(),
    ]
    chapters, _ = check(doc(blocks))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]


def test_drop_keywords_need_the_whole_title() -> None:
    titles = [
        ("Chapter 1", "text one"),
        ("Contents of the Will", "The will."),
        ("List of Grievances", "The grievances."),
        ("Illustrations of Grief", "The grief."),
        ("CONTENTS.", "Chapter 1 3"),
        ("Chapter 2", "text two"),
    ]
    blocks: list[Block] = []
    toc: list[TocEntry] = []
    for i, (title, text) in enumerate(titles):
        blocks += [head(title, 2, None, f"a#{i}"), para(text)]
        toc.append(TocEntry(title, 1, f"a#{i}"))
    chapters, _ = check(doc(blocks, toc))
    assert summary(chapters) == [
        ("Chapter 1", C),
        ("Contents of the Will", C),
        ("List of Grievances", C),
        ("Illustrations of Grief", C),
        ("Chapter 2", C),
    ]
    assert chapters[1].paragraphs[0].text == "The will."
    assert chapters[2].paragraphs[0].text == "The grievances."


def test_toc_like_entry_is_dropped() -> None:
    blocks = [
        head("Overview"),
        para("One 1"),
        para("Two 5"),
        para("Three 9"),
        para("Four 12"),
        head("Chapter 1"),
        para(),
        head("Chapter 2"),
        para(),
    ]
    chapters, _ = check(doc(blocks))
    assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]
    assert not any(p.text == "One 1" for c in chapters for p in c.paragraphs)


def test_paragraph_of_roman_numerals_is_dropped() -> None:
    numerals = "PREFACE. Chapter: " + ", ".join(["I.", "II.", "III.", "IV.", "V.", "VI.", "VII."])
    blocks = [para(numerals), para(BODY), head("Chapter 1"), para(), head("Chapter 2"), para()]
    chapters, _ = check(doc(blocks))
    assert chapters[0].title == "Front matter"
    assert [p.text for p in chapters[0].paragraphs] == [BODY]


def test_introduction_in_the_middle_becomes_chapter() -> None:
    blocks = [
        head("Chapter 1"),
        para(),
        head("Introduction"),
        para(),
        head("Chapter 2"),
        para(),
    ]
    chapters, _ = check(doc(blocks))
    assert summary(chapters) == [("Chapter 1", C), ("Introduction", C), ("Chapter 2", C)]


def test_notes_after_last_chapter_is_back_matter() -> None:
    blocks = [
        head("Preface"),
        para(),
        head("Chapter 1"),
        para(),
        head("Chapter 2"),
        para(),
        head("Notes"),
        para(),
    ]
    chapters, _ = check(doc(blocks))
    assert summary(chapters) == [
        ("Preface", F),
        ("Chapter 1", C),
        ("Chapter 2", C),
        ("Notes", B),
    ]


def test_non_keyword_entries_around_pattern_entries_are_front_and_back() -> None:
    """R3: with at least 3 pattern boundaries, outsiders are classified by position."""
    texts = ["His Own Words", "THE FIRST BOOK", "THE SECOND BOOK", "THE THIRD BOOK", "Letters"]
    blocks: list[Block] = []
    toc: list[TocEntry] = []
    for i, text in enumerate(texts):
        blocks += [head(text, 2, None, f"a#{i}"), para()]
        toc.append(TocEntry(text, 1, f"a#{i}"))
    chapters, _ = check(doc(blocks, toc))
    assert summary(chapters) == [
        ("His Own Words", F),
        ("THE FIRST BOOK", C),
        ("THE SECOND BOOK", C),
        ("THE THIRD BOOK", C),
        ("Letters", B),
    ]


def test_all_caps_heading_after_last_toc_chapter_is_back_matter() -> None:
    """D2: extra works after the last chapter; by-line headings stay paragraphs."""
    blocks = [
        head("CHAPTER I. ONE", 2, 1),
        para(BODY, 1),
        head("CHAPTER II. TWO", 2, 2),
        para(BODY, 2),
        head("DESCRIPTION OF THE METHODS ADOPTED BY THE DUKE", 3, 3),
        head("BY", 3, 3),
        head("NICOL MACHIAVELLI", 3, 3),
        para(BODY, 3),
        para(BODY, 4),
        head("THE LIFE OF CASTRUCCIO CASTRACANI OF LUCCA", 3, 5),
        head("WRITTEN BY NICOLO MACHIAVELLI", 3, 5),
        para(BODY, 5),
    ]
    toc = [TocEntry("CHAPTER I. ONE", 1, 1), TocEntry("CHAPTER II. TWO", 1, 2)]
    chapters, _ = check(doc(blocks, toc, fmt=FileFormat.PDF))
    assert summary(chapters) == [
        ("CHAPTER I. ONE", C),
        ("CHAPTER II. TWO", C),
        ("DESCRIPTION OF THE METHODS ADOPTED BY THE DUKE", B),
        ("THE LIFE OF CASTRUCCIO CASTRACANI OF LUCCA", B),
    ]
    assert [p.text for p in chapters[2].paragraphs][:2] == ["BY", "NICOL MACHIAVELLI"]
    assert chapters[1].word_count == 40  # the extra works are not inside chapter II


def test_titles_keep_case_and_lose_trailing_dot_and_footnote_markers() -> None:
    blocks = [
        head("CHAPTER I. HOW MANY KINDS OF PRINCIPALITIES[*]."),
        para(),
        head("Chapter II. Of Mixed Things."),
        para(),
    ]
    chapters, _ = check(doc(blocks))
    assert [c.title for c in chapters] == [
        "CHAPTER I. HOW MANY KINDS OF PRINCIPALITIES",
        "Chapter II. Of Mixed Things",
    ]


def test_long_title_is_cut() -> None:
    blocks = [head("Chapter 1 " + "very " * 200), para(), head("Chapter 2"), para()]
    chapters, _ = check(doc(blocks))
    assert len(chapters[0].title) <= 500


def test_only_keyword_entries_fall_back_to_parts() -> None:
    chapters, detected = build_chapters(doc([head("Preface"), para(), head("Notes"), para()]))
    assert detected is False
    assert [c.title for c in chapters] == ["Part 1"]


def test_classify_kind() -> None:
    assert classify_kind("Introduction") is F
    assert classify_kind("PREFACE") is F
    assert classify_kind("Predgovor") is F
    assert classify_kind("Notes") is B
    assert classify_kind("Appendix A") is B
    assert classify_kind("Contents") is None
    assert classify_kind("List of Illustrations") is None
    assert classify_kind("Sadržaj") is None
    assert classify_kind("The Storm") is C
    assert classify_kind("Indexing the world") is C
    assert classify_kind("CONTENTS.") is None
    assert classify_kind("Contents of the Will") is C
    assert classify_kind("List of Grievances") is C
    assert FRONT_KEYWORDS and BACK_KEYWORDS and DROP_KEYWORDS


# ---------------------------------------------------------------- real books


def test_real_pride_and_prejudice() -> None:
    book = parse_book(BOOKS / "pride-and-prejudice.epub")
    validate_parsed_book(book)
    assert book.chapters_detected
    chapters = [c for c in book.chapters if c.kind is C]
    assert len(chapters) == 61
    assert chapters[0].title == "Chapter I" and chapters[2].title == "CHAPTER III"
    assert all(c.word_count > 100 for c in chapters)
    texts = [p.text for c in book.chapters for p in c.paragraphs]
    assert not any(t.startswith("PREFACE. List of Illustrations") for t in texts)


def test_real_the_prince() -> None:
    book = parse_book(BOOKS / "the-prince.pdf")
    validate_parsed_book(book)
    assert book.chapters_detected
    chapters = [c for c in book.chapters if c.kind is C]
    assert len(chapters) == 26
    assert chapters[0].title.startswith("CHAPTER I.")
    assert chapters[-1].title.startswith("CHAPTER XXVI.")
    front = [c.title for c in book.chapters if c.kind is F]
    assert "INTRODUCTION" in front and "DEDICATION" in front
    back = [c.title for c in book.chapters if c.kind is B]
    assert len(back) == 2 and back[0].startswith("DESCRIPTION OF THE METHODS")
    assert back[1].startswith("THE LIFE OF CASTRUCCIO")


def test_real_meditations() -> None:
    book = parse_book(BOOKS / "meditations.epub")
    validate_parsed_book(book)
    assert book.chapters_detected
    chapters = [c for c in book.chapters if c.kind is C]
    assert len(chapters) == 12
    assert chapters[0].title == "THE FIRST BOOK"
    assert [c.title for c in book.chapters if c.kind is F][-2:] == [
        "INTRODUCTION",
        "HIS FIRST BOOK",
    ]
    assert [c.title for c in book.chapters if c.kind is B] == ["APPENDIX", "NOTES", "GLOSSARY"]
    assert "BOOKS" not in [c.title for c in book.chapters]
