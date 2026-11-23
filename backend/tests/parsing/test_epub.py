import re
import zipfile
from pathlib import Path

import pytest

from app.parsing.contract import FileFormat, ParseError, ParseErrorCode
from app.parsing.document import Block, RawDocument
from app.parsing.epub import read_epub

from .builders import encryption_xml, make_epub, paragraphs

BOOKS = Path(__file__).parent.parent / "fixtures" / "books"


def texts(doc: RawDocument) -> list[str]:
    return [b.text for b in doc.blocks]


def block_with(doc: RawDocument, anchor: str) -> Block:
    return next(b for b in doc.blocks if anchor in b.anchors)


# --------------------------------------------------------------------------- metadata and spine


def test_reads_metadata(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        title="  My   Book ",
        author="Jane Doe",
        language="en-GB",
        docs=[("a.xhtml", "<p>Hello.</p>")],
    )
    doc = read_epub(path)
    assert (doc.title, doc.author, doc.language) == ("My Book", "Jane Doe", "en-GB")
    assert doc.file_format is FileFormat.EPUB


@pytest.mark.parametrize(
    ("creator", "file_as", "expected"),
    [
        # Gutenberg Meditations: words in front of the name in dc:creator.
        ("Emperor of Rome Marcus Aurelius", "Marcus Aurelius, Emperor of Rome", "Marcus Aurelius"),
        # "Surname, Given name": one-word first part, dc:creator is kept.
        ("Jane Austen", "Austen, Jane", "Jane Austen"),
        # Same first word: dc:creator is already the name.
        ("Lev Tolstoy", "Lev Nikolayevich Tolstoy", "Lev Tolstoy"),
        (" Jane Doe ", None, "Jane Doe"),
    ],
)
def test_author_uses_file_as_name_part(
    tmp_path: Path, creator: str, file_as: str | None, expected: str
) -> None:
    path = make_epub(
        tmp_path, author=creator, author_file_as=file_as, docs=[("a.xhtml", "<p>x</p>")]
    )
    assert read_epub(path).author == expected


def test_real_book_authors() -> None:
    assert read_epub(BOOKS / "meditations.epub").author == "Marcus Aurelius"
    assert read_epub(BOOKS / "pride-and-prejudice.epub").author == "Jane Austen"


def test_missing_metadata_is_none(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path, title=None, author=None, language=None, docs=[("a.xhtml", "<p>x</p>")]
    )
    doc = read_epub(path)
    assert (doc.title, doc.author, doc.language) == (None, None, None)


def test_spine_order_is_respected(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        docs=[
            ("z.xhtml", "<p>first</p>"),
            ("a.xhtml", "<p>second</p>"),
            ("m.xhtml", "<p>third</p>"),
        ],
    )
    assert texts(read_epub(path)) == ["first", "second", "third"]


def test_every_page_is_none(tmp_path: Path) -> None:
    path = make_epub(tmp_path, docs=[("a.xhtml", paragraphs(3))])
    assert all(b.page is None for b in read_epub(path).blocks)


# --------------------------------------------------------------------------- XHTML -> blocks


def test_unwanted_elements_are_not_in_blocks(tmp_path: Path) -> None:
    body = """
    <script>var secret = 1;</script>
    <nav><ol><li><a href="#x">Nav entry</a></li></ol></nav>
    <p>Before <img src="i.png" alt="ALT TEXT"/>after.</p>
    <svg><text>SVG TEXT</text></svg>
    <figure><img src="i.png"/><figcaption>Figure caption</figcaption></figure>
    <h2><span class="caption">Illustration caption</span><br/>CHAPTER II.</h2>
    <p>Text<span epub:type="pagebreak" title="12">12</span> continues.</p>
    <aside epub:type="footnote"><p>Footnote text</p></aside>
    """
    joined = " | ".join(texts(read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))))
    for unwanted in ("secret", "Nav entry", "ALT TEXT", "SVG TEXT", "Figure caption"):
        assert unwanted not in joined
    assert "Illustration caption" not in joined
    assert "Footnote text" not in joined
    assert joined == "Before after. | CHAPTER II. | Text continues."


def test_headings_get_heading_level(tmp_path: Path) -> None:
    body = "<h1>Book</h1><h2>Chapter 1</h2><p>Text.</p><h3>Section</h3><p>More.</p>"
    doc = read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))
    assert [(b.text, b.heading_level) for b in doc.blocks] == [
        ("Book", 1),
        ("Chapter 1", 2),
        ("Text.", None),
        ("Section", 3),
        ("More.", None),
    ]


def test_block_structure(tmp_path: Path) -> None:
    body = """
    <div>Loose text in a div
      <p>Para in div</p>
      tail text
    </div>
    <blockquote><p>Quoted one</p><p>Quoted two</p></blockquote>
    <ul><li>Item <em>one</em></li><li>Item two</li></ul>
    <table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>
    <p>Line one<br/>line two</p>
    <p>   </p>
    """
    assert texts(read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))) == [
        "Loose text in a div",
        "Para in div",
        "tail text",
        "Quoted one",
        "Quoted two",
        "Item one",
        "Item two",
        "a b",
        "c d",
        "Line one line two",
    ]


def test_inline_markup_does_not_add_spaces(tmp_path: Path) -> None:
    body = '<p><span class="letra"><span>M</span></span>R. BENNET was <i>very</i> late.</p>'
    assert texts(read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))) == [
        "MR. BENNET was very late."
    ]


def test_text_is_normalized_and_soft_hyphens_removed(tmp_path: Path) -> None:
    body = "<p>  Some\n   wrapped   er­rors​ here &amp; there&#160;now </p>"
    assert texts(read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))) == [
        "Some wrapped errors here & there now"
    ]


def test_html_named_entities(tmp_path: Path) -> None:
    body = "<p>caf&eacute; &mdash; done&nbsp;now</p>"
    assert texts(read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))) == ["café — done now"]


def test_not_well_formed_xhtml_falls_back_to_html_parser(tmp_path: Path) -> None:
    path = tmp_path / "bad.epub"
    good = make_epub(tmp_path, docs=[("a.xhtml", "<p>placeholder</p>")])
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(path, "w") as dst:
        for item in src.infolist():
            data = src.read(item)
            if item.filename == "OEBPS/a.xhtml":
                data = b"<html><body><p>Unclosed <b>bold<p>Second</body></html>"
            dst.writestr(item, data)
    assert texts(read_epub(path)) == ["Unclosed bold", "Second"]


# --------------------------------------------------------------------------- anchors


def test_anchor_on_empty_a_before_heading_lands_on_heading(tmp_path: Path) -> None:
    body = '<p>Intro.</p><a id="ch1"></a><h2>Chapter I</h2><p>Text.</p>'
    doc = read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))
    assert block_with(doc, "OEBPS/a.xhtml#ch1").text == "Chapter I"


def test_anchor_inside_heading_and_on_heading(tmp_path: Path) -> None:
    body = '<h2 id="h"><a id="CHAPTER_II"/>CHAPTER II.</h2><p>Text.</p>'
    doc = read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))
    heading = block_with(doc, "OEBPS/a.xhtml#CHAPTER_II")
    assert heading.text == "CHAPTER II."
    assert "OEBPS/a.xhtml#h" in heading.anchors
    # The self-closing <a/> must not swallow the following paragraph (XML parsing).
    assert texts(doc) == ["CHAPTER II.", "Text."]


def test_container_id_attaches_to_its_first_block(tmp_path: Path) -> None:
    body = '<p>Before.</p><div id="chap"><div><h2>Title</h2></div><p>Body.</p></div>'
    doc = read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))
    assert block_with(doc, "OEBPS/a.xhtml#chap").text == "Title"


def test_id_on_skipped_element_moves_to_next_block(tmp_path: Path) -> None:
    body = '<p>Before.</p><img id="pic" src="x.png"/><p>After.</p>'
    doc = read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))
    assert block_with(doc, "OEBPS/a.xhtml#pic").text == "After."


def test_first_block_of_a_document_gets_the_bare_path(tmp_path: Path) -> None:
    doc = read_epub(
        make_epub(tmp_path, docs=[("a.xhtml", "<p>One.</p>"), ("b.xhtml", "<p>Two.</p><p>3</p>")])
    )
    assert block_with(doc, "OEBPS/a.xhtml").text == "One."
    assert block_with(doc, "OEBPS/b.xhtml").text == "Two."
    assert doc.blocks[2].anchors == ()


# --------------------------------------------------------------------------- navigation


def test_epub3_nav_with_levels_and_resolved_targets(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        docs=[
            ("text/a.xhtml", '<h1 id="p1">Part One</h1><h2 id="c1">Chapter 1</h2><p>x</p>'),
            ("text/b%20c.xhtml", "<h2>Chapter 2</h2><p>y</p>"),
        ],
        nav=[
            ("Part One", "text/a.xhtml#p1", 1),
            ("Chapter 1", "text/a.xhtml#c1", 2),
            ("Chapter 2", "text/b%20c.xhtml", 2),
        ],
    )
    doc = read_epub(path)
    assert [(e.title, e.level, e.target) for e in doc.toc] == [
        ("Part One", 1, "OEBPS/text/a.xhtml#p1"),
        ("Chapter 1", 2, "OEBPS/text/a.xhtml#c1"),
        ("Chapter 2", 2, "OEBPS/text/b c.xhtml"),
    ]
    assert block_with(doc, "OEBPS/text/b c.xhtml").text == "Chapter 2"


def test_epub2_ncx(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        docs=[("a.xhtml", '<h2 id="c1">One</h2><p>x</p><h2 id="c2">Two</h2><p>y</p>')],
        nav=[("Book", "a.xhtml", 1), ("One", "a.xhtml#c1", 2), ("Two", "a.xhtml#c2", 2)],
        ncx=True,
    )
    doc = read_epub(path)
    assert [(e.title, e.level, e.target) for e in doc.toc] == [
        ("Book", 1, "OEBPS/a.xhtml"),
        ("One", 2, "OEBPS/a.xhtml#c1"),
        ("Two", 2, "OEBPS/a.xhtml#c2"),
    ]


def test_toc_entries_pointing_nowhere_are_dropped(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        docs=[("a.xhtml", '<h2 id="c1">One</h2><p>x</p>')],
        nav=[("One", "a.xhtml#c1"), ("Missing", "a.xhtml#nope"), ("Other", "zzz.xhtml")],
    )
    assert [e.title for e in read_epub(path).toc] == ["One"]


# --------------------------------------------------------------------------- Gutenberg


GUTENBERG_HEADER = """
<header class="pg-boilerplate pgheader" id="pg-header">
  <h2 id="pg-header-heading">The Project Gutenberg eBook of Test</h2>
  <p>This ebook is for the use of anyone anywhere.</p>
  <div id="pg-start-separator">*** START OF THE PROJECT GUTENBERG EBOOK TEST ***</div>
</header>
"""
GUTENBERG_FOOTER = """
<footer class="pg-boilerplate" id="pg-footer">
  <div id="pg-end-separator">*** END OF THE PROJECT GUTENBERG EBOOK TEST ***</div>
  <h2 id="lic">THE FULL PROJECT GUTENBERG LICENSE</h2>
  <p>Project Gutenberg is a registered trademark.</p>
</footer>
"""


def test_gutenberg_header_and_footer_removed_by_id(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        docs=[
            ("a.xhtml", GUTENBERG_HEADER + '<h2 id="c1">Chapter 1</h2><p>Real text.</p>'),
            ("b.xhtml", "<p>Last words.</p>" + GUTENBERG_FOOTER),
        ],
        nav=[("Chapter 1", "a.xhtml#c1"), ("License", "b.xhtml#lic")],
    )
    doc = read_epub(path)
    assert texts(doc) == ["Chapter 1", "Real text.", "Last words."]
    assert [e.title for e in doc.toc] == ["Chapter 1"]


def test_gutenberg_header_and_footer_removed_by_markers(tmp_path: Path) -> None:
    body = """
    <p>The Project Gutenberg eBook of Test. This ebook is for the use of anyone.</p>
    <p>*** START OF THE PROJECT GUTENBERG EBOOK TEST ***</p>
    <h2>Chapter 1</h2><p>Real text.</p>
    <p>*** END OF THIS PROJECT GUTENBERG EBOOK TEST ***</p>
    <p>Project Gutenberg licence text.</p>
    """
    doc = read_epub(make_epub(tmp_path, docs=[("a.xhtml", body)]))
    assert texts(doc) == ["Chapter 1", "Real text."]


def test_only_boilerplate_gives_no_blocks(tmp_path: Path) -> None:
    path = make_epub(tmp_path, docs=[("a.xhtml", GUTENBERG_HEADER + GUTENBERG_FOOTER)])
    assert read_epub(path).blocks == ()


# --------------------------------------------------------------------------- errors


def test_content_encryption_is_encrypted_file(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        docs=[("a.xhtml", "<p>x</p>")],
        encryption_xml=encryption_xml(
            "http://www.w3.org/2001/04/xmlenc#aes128-cbc", "OEBPS/a.xhtml"
        ),
    )
    with pytest.raises(ParseError) as info:
        read_epub(path)
    assert info.value.code is ParseErrorCode.ENCRYPTED_FILE


@pytest.mark.parametrize(
    "algorithm", ["http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"]
)
def test_font_obfuscation_is_not_drm(tmp_path: Path, algorithm: str) -> None:
    path = make_epub(
        tmp_path,
        docs=[("a.xhtml", "<p>Readable.</p>")],
        encryption_xml=encryption_xml(algorithm, "OEBPS/fonts/font.otf"),
    )
    assert texts(read_epub(path)) == ["Readable."]


def test_rights_xml_is_encrypted_file(tmp_path: Path) -> None:
    path = make_epub(tmp_path, docs=[("a.xhtml", "<p>x</p>")])
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("META-INF/rights.xml", "<rights/>")
    with pytest.raises(ParseError) as info:
        read_epub(path)
    assert info.value.code is ParseErrorCode.ENCRYPTED_FILE


def _patch_central_directory(path: Path, name: str, offset: int, value: int) -> None:
    """Overwrite a 2-byte field of one entry's central directory header (little endian)."""
    data = bytearray(path.read_bytes())
    encoded = name.encode()
    position = data.find(b"PK\x01\x02")
    while data[position + 46 : position + 46 + len(encoded)] != encoded:
        position = data.find(b"PK\x01\x02", position + 4)
        assert position != -1, name
    data[position + offset : position + offset + 2] = value.to_bytes(2, "little")
    path.write_bytes(bytes(data))


def test_password_protected_zip_entry_is_encrypted_file(tmp_path: Path) -> None:
    path = make_epub(tmp_path, docs=[("a.xhtml", "<p>x</p>")])
    _patch_central_directory(path, "OEBPS/a.xhtml", offset=8, value=0x1)  # flag bit 0
    with pytest.raises(ParseError) as info:
        read_epub(path)
    assert info.value.code is ParseErrorCode.ENCRYPTED_FILE


def test_unsupported_compression_is_corrupt_file(tmp_path: Path) -> None:
    path = make_epub(tmp_path, docs=[("a.xhtml", "<p>x</p>")])
    _patch_central_directory(path, "OEBPS/a.xhtml", offset=10, value=9)  # Deflate64
    with pytest.raises(ParseError) as info:
        read_epub(path)
    assert info.value.code is ParseErrorCode.CORRUPT_FILE


def _zip(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "broken.epub"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    "files",
    [
        {"mimetype": "application/epub+zip"},
        {"mimetype": "application/epub+zip", "META-INF/container.xml": "<container/>"},
        {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": '<container><rootfiles><rootfile full-path="x.opf"/>'
            "</rootfiles></container>",
        },
        {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": '<container><rootfiles><rootfile full-path="x.opf"/>'
            "</rootfiles></container>",
            "x.opf": "<notapackage/>",
        },
        {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": '<container><rootfiles><rootfile full-path="x.opf"/>'
            "</rootfiles></container>",
            "x.opf": "<package><manifest/></package>",
        },
    ],
    ids=["no-container", "no-rootfile", "missing-opf", "not-an-opf", "no-spine"],
)
def test_broken_structure_is_corrupt_file(tmp_path: Path, files: dict[str, str]) -> None:
    with pytest.raises(ParseError) as info:
        read_epub(_zip(tmp_path, files))
    assert info.value.code is ParseErrorCode.CORRUPT_FILE


def test_missing_spine_document_is_corrupt_file(tmp_path: Path) -> None:
    path = make_epub(tmp_path, docs=[("a.xhtml", "<p>x</p>")])
    rebuilt = tmp_path / "rebuilt.epub"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(rebuilt, "w") as dst:
        for item in src.infolist():
            if item.filename != "OEBPS/a.xhtml":
                dst.writestr(item, src.read(item))
    with pytest.raises(ParseError) as info:
        read_epub(rebuilt)
    assert info.value.code is ParseErrorCode.CORRUPT_FILE


def test_truncated_epub_is_corrupt_file(tmp_path: Path) -> None:
    data = (BOOKS / "meditations.epub").read_bytes()
    path = tmp_path / "truncated.epub"
    path.write_bytes(data[: len(data) // 2])
    with pytest.raises(ParseError) as info:
        read_epub(path)
    assert info.value.code is ParseErrorCode.CORRUPT_FILE


# --------------------------------------------------------------------------- footnote markers


def test_inline_footnote_markers_are_removed_from_paragraphs_and_headings(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        docs=[("a.xhtml", "<h2>Part[*] One</h2><p>Rome[*] fell, he said [12].</p>")],
    )
    doc = read_epub(path)
    assert texts(doc) == ["Part One", "Rome fell, he said."]


def test_footnote_markers_are_removed_from_toc_titles(tmp_path: Path) -> None:
    docs = [("a.xhtml", '<h2 id="c1">One</h2><p>x</p>')]
    nav = [("One [3]", "a.xhtml#c1", 1)]
    assert [e.title for e in read_epub(make_epub(tmp_path, docs=docs, nav=nav)).toc] == ["One"]
    ncx_dir = tmp_path / "ncx"
    ncx_dir.mkdir()
    ncx = make_epub(ncx_dir, docs=docs, nav=nav, ncx=True)
    assert [e.title for e in read_epub(ncx).toc] == ["One"]


# --------------------------------------------------------------------------- real books


@pytest.mark.parametrize("name", ["pride-and-prejudice.epub", "meditations.epub"])
def test_real_book_has_no_boilerplate(name: str) -> None:
    doc = read_epub(BOOKS / name)
    assert doc.blocks
    for block in doc.blocks:
        assert "project gutenberg" not in block.text.lower()
        assert "­" not in block.text
        assert not re.search(r"\[(?:\*+|\d+)\]", block.text)
    for entry in doc.toc:
        assert "gutenberg" not in entry.title.lower()


def test_pride_and_prejudice_toc() -> None:
    doc = read_epub(BOOKS / "pride-and-prejudice.epub")
    # NCX labels still carry illustration captions ("He rode a black horse. CHAPTER III.") and
    # sometimes no space ("CHAPTERXXVII."); cleaning titles is chapter detection's job (A4).
    chapter = re.compile(r"\bchapter\s*[ivxlc]+\b", re.IGNORECASE)
    chapter_entries = [e for e in doc.toc if chapter.search(e.title)]
    assert len(chapter_entries) == 61
    anchors = {a for b in doc.blocks for a in b.anchors}
    assert all(e.target in anchors for e in doc.toc)


def test_meditations_toc() -> None:
    doc = read_epub(BOOKS / "meditations.epub")
    titles = [e.title for e in doc.toc]
    books = [t for t in titles if t.startswith("THE ") and t.endswith(" BOOK")]
    assert len(books) == 12
    assert {"INTRODUCTION", "APPENDIX", "NOTES", "GLOSSARY"} <= set(titles)
