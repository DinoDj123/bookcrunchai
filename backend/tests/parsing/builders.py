"""Helpers that build small test books in tmp_path.

A plain module (not conftest.py) so test_contract.py is not affected by it.
"""

from __future__ import annotations

import zipfile
from html import escape
from pathlib import Path
from urllib.parse import unquote

import pymupdf

# (title, href) or (title, href, level); href is relative to the OEBPS folder.
NavEntry = tuple[str, str] | tuple[str, str, int]

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title><style>p {{ margin: 0 }}</style></head>
<body>
{body}
</body>
</html>
"""


def _nav_entries(nav: list[NavEntry]) -> list[tuple[str, str, int]]:
    return [(e[0], e[1], e[2] if len(e) == 3 else 1) for e in nav]


def _nav_xhtml(nav: list[NavEntry]) -> str:
    """EPUB3 nav document with nested <ol> lists following the entry levels."""
    lines = ['<nav epub:type="toc" id="toc"><h1>Contents</h1>']
    depth = 0
    for title, href, level in _nav_entries(nav):
        if level > depth:
            lines.extend(["<ol><li>"] * (level - depth))
        else:
            lines.append("</li>")
            lines.extend(["</ol></li>"] * (depth - level))
            lines.append("<li>")
        depth = level
        lines.append(f'<a href="{escape(href)}">{escape(title)}</a>')
    if depth:
        lines.append("</li>")
        lines.extend(["</ol></li>"] * (depth - 1))
        lines.append("</ol>")
    lines.append("</nav>")
    return _XHTML.format(title="Contents", body="\n".join(lines))


def _ncx(nav: list[NavEntry]) -> str:
    """EPUB2 NCX with nested navPoints following the entry levels."""
    parts: list[str] = []
    depth = 0
    for order, (title, href, level) in enumerate(_nav_entries(nav), start=1):
        parts.extend(["</navPoint>"] * (depth - level + 1) if depth >= level else [])
        parts.append(
            f'<navPoint id="np{order}" playOrder="{order}">'
            f"<navLabel><text>{escape(title)}</text></navLabel>"
            f'<content src="{escape(href)}"/>'
        )
        depth = level
    parts.extend(["</navPoint>"] * depth)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        "<head/><docTitle><text>t</text></docTitle><navMap>" + "".join(parts) + "</navMap></ncx>"
    )


def make_epub(
    tmp_path: Path,
    *,
    title: str | None = "Test Book",
    author: str | None = "Test Author",
    language: str | None = "en",
    docs: list[tuple[str, str]],
    nav: list[NavEntry] | None = None,
    ncx: bool = False,
    encryption_xml: str | None = None,
    name: str = "book.epub",
) -> Path:
    """Write a minimal valid EPUB and return its path.

    `docs` are (file name, body HTML) pairs in spine order, stored under OEBPS/.
    `nav` entries become an EPUB3 nav document, or an EPUB2 NCX instead when `ncx` is True.
    """
    metadata = []
    if title is not None:
        metadata.append(f"<dc:title>{escape(title)}</dc:title>")
    if author is not None:
        metadata.append(f"<dc:creator>{escape(author)}</dc:creator>")
    if language is not None:
        metadata.append(f"<dc:language>{escape(language)}</dc:language>")

    manifest = []
    spine = []
    for i, (doc_name, _) in enumerate(docs):
        manifest.append(
            f'<item id="doc{i}" href="{escape(doc_name)}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="doc{i}"/>')
    if nav is not None and not ncx:
        manifest.append(
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        )
    if nav is not None and ncx:
        manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

    toc_attr = ' toc="ncx"' if nav is not None and ncx else ""
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">test</dc:identifier>'
        + "".join(metadata)
        + "</metadata><manifest>"
        + "".join(manifest)
        + f"</manifest><spine{toc_attr}>"
        + "".join(spine)
        + "</spine></package>"
    )

    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        # mimetype must be the first entry and stored uncompressed.
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", _CONTAINER, zipfile.ZIP_DEFLATED)
        if encryption_xml is not None:
            archive.writestr("META-INF/encryption.xml", encryption_xml, zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        for doc_name, body in docs:
            content = _XHTML.format(title=escape(doc_name), body=body)
            # hrefs in the OPF are URL-encoded, names inside the zip are not.
            archive.writestr(f"OEBPS/{unquote(doc_name)}", content, zipfile.ZIP_DEFLATED)
        if nav is not None and not ncx:
            archive.writestr("OEBPS/nav.xhtml", _nav_xhtml(nav), zipfile.ZIP_DEFLATED)
        if nav is not None and ncx:
            archive.writestr("OEBPS/toc.ncx", _ncx(nav), zipfile.ZIP_DEFLATED)
    return path


def encryption_xml(algorithm: str, uri: str) -> str:
    """META-INF/encryption.xml that encrypts one file with the given algorithm."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
        'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">'
        f'<enc:EncryptedData><enc:EncryptionMethod Algorithm="{algorithm}"/>'
        f'<enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>'
        "</enc:EncryptedData></encryption>"
    )


def paragraphs(n: int, words: int = 10, prefix: str = "w") -> str:
    """HTML for n paragraphs of `words` words each."""
    return "\n".join(
        "<p>" + " ".join(f"{prefix}{i}x{j}" for j in range(words)) + "</p>" for i in range(n)
    )


# (text, x, baseline y, font size) in PDF points, origin at the top left of the page.
PdfLine = tuple[str, float, float, float]

PDF_PAGE_WIDTH = 400.0
PDF_PAGE_HEIGHT = 600.0
# Baselines of the running header and of the page number, inside the top / bottom 15 %.
_PDF_HEADER_Y = 40.0
_PDF_FOOTER_Y = 570.0
_PDF_MARGIN_SIZE = 9.0


def make_pdf(
    tmp_path: Path,
    pages: list[list[PdfLine]],
    *,
    header: str | None = None,
    footer_numbers: bool = False,
    password: str | None = None,
    outline: list[tuple[int, str, int]] | None = None,
    metadata: dict[str, str] | None = None,
    language: str | None = None,
    name: str = "book.pdf",
) -> Path:
    """Write a PDF with every line at a controlled position and size and return its path.

    Text is written with a TextWriter (Helvetica) so a soft hyphen stays U+00AD in the text layer.
    A page without lines gets only a small image, like a scanned page. `header` is printed at the
    top of every page and `footer_numbers` prints the page number at the bottom.
    `outline` entries are (level, title, page); page -1 makes an entry without a target.
    """
    font = pymupdf.Font("helv")
    doc = pymupdf.open()
    for number, lines in enumerate(pages, start=1):
        page = doc.new_page(width=PDF_PAGE_WIDTH, height=PDF_PAGE_HEIGHT)
        writer = pymupdf.TextWriter(page.rect)
        all_lines = list(lines)
        if header is not None:
            all_lines.append((header, 50.0, _PDF_HEADER_Y, _PDF_MARGIN_SIZE))
        if footer_numbers:
            all_lines.append((str(number), PDF_PAGE_WIDTH / 2, _PDF_FOOTER_Y, _PDF_MARGIN_SIZE))
        for text, x, y, size in all_lines:
            writer.append((x, y), text, font=font, fontsize=size)
        if all_lines:
            writer.write_text(page)
        if not lines:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
            pixmap.clear_with(128)
            page.insert_image(pymupdf.Rect(50, 50, 350, 550), pixmap=pixmap)
    if outline is not None:
        doc.set_toc([list(entry) for entry in outline])
    if metadata is not None:
        doc.set_metadata(metadata)
    if language is not None:
        doc.xref_set_key(doc.pdf_catalog(), "Lang", pymupdf.get_pdf_str(language))

    path = tmp_path / name
    if password is None:
        doc.save(path)
    else:
        doc.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw=password,
        )
    doc.close()
    return path
