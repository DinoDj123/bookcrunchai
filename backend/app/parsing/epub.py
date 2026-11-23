"""EPUB reader: file -> RawDocument (design §3).

Uses stdlib zipfile + BeautifulSoup (lxml) instead of EbookLib (Decision D1): we need full
control over which exception becomes which ParseError, and element-level anchors for chapter
splitting.

Only extracts and cleans text. It never decides what a chapter is (that is chapters.py).
"""

from __future__ import annotations

import html
import html.entities
import posixpath
import re
import warnings
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning
from bs4.element import PreformattedString
from lxml import etree

from .boilerplate import GUTENBERG_CLASS, GUTENBERG_IDS, is_gutenberg_end, is_gutenberg_start
from .contract import FileFormat, ParseError, ParseErrorCode, normalize_text
from .document import Block, RawDocument, TocEntry
from .text import join_hyphenated, strip_footnote_markers

# Font obfuscation (IDPF and Adobe) only scrambles embedded fonts. It is not DRM, so an EPUB that
# uses only these algorithms in META-INF/encryption.xml is read normally.
FONT_OBFUSCATION_ALGORITHMS = frozenset(
    {
        "http://www.idpf.org/2008/embedding",
        "http://ns.adobe.com/pdf/enc#RC",
    }
)

_XHTML_TYPES = frozenset({"application/xhtml+xml", "text/html"})
_NCX_TYPE = "application/x-dtbncx+xml"

# Elements whose text is never part of the book text. Ids inside them still move to the next
# block, so a TOC target on an image or page-break marker is not lost.
_SKIPPED_TAGS = frozenset(
    {"script", "style", "nav", "img", "svg", "figure", "figcaption", "head", "title", "noscript"}
)
_SKIPPED_EPUB_TYPES = frozenset({"footnote", "endnote", "rearnote", "pagebreak"})
# Illustration captions (Gutenberg puts them even inside chapter headings:
# <h2><span class="caption">I hope Mr. Bingley will like it.</span><br/>CHAPTER II.</h2>).
_SKIPPED_CLASSES = frozenset({"caption"})

# Named HTML entities are not defined in XML; they are replaced before XML parsing.
_XML_ENTITIES = frozenset({"amp", "lt", "gt", "quot", "apos"})
_NAMED_ENTITY = re.compile(rb"&([A-Za-z][A-Za-z0-9]*);")
# Only used to check well-formedness. No DTDs, no external entities, no network.
_STRICT_XML = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)

# Elements that always produce exactly one block with all of their text.
_LEAF_BLOCK_TAGS = frozenset({"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "tr", "dt", "dd"})
# Elements that start a new block. Their inline content becomes a block; nested block elements
# become blocks of their own.
_BLOCK_TAGS = _LEAF_BLOCK_TAGS | frozenset(
    {
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "blockquote",
        "li",
        "ol",
        "ul",
        "dl",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "td",
        "th",
        "body",
        "hr",
        "address",
        "center",
    }
)
_HEADING_LEVELS = {f"h{n}": n for n in range(1, 7)}

# Exceptions from zipfile/zlib that mean "the archive is damaged".
_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError)
# General purpose flag bit 0: the zip entry is password protected (zipfile would raise
# RuntimeError on read).
_ZIP_ENCRYPTED_FLAG = 0x1


def read_epub(path: Path) -> RawDocument:
    """Read an EPUB file. Raises ParseError(CORRUPT_FILE | ENCRYPTED_FILE)."""
    try:
        archive = zipfile.ZipFile(path)
    except _ZIP_ERRORS as exc:
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"not a valid zip archive: {exc}") from exc
    except OSError as exc:
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"cannot open file: {exc}") from exc

    with archive:
        _check_drm(archive)
        opf_path = _find_opf(archive)
        package = _read_package(archive, opf_path)
        toc = _read_toc(archive, package)

        builder = _BlockBuilder()
        for doc_path in package.spine:
            builder.start_document(doc_path)
            _walk(_parse_xhtml(_read(archive, doc_path)).body, builder)
            builder.end_document()

    blocks = _strip_marker_boilerplate(builder.blocks)
    known_targets = {a for b in blocks for a in b.anchors}
    return RawDocument(
        title=package.title,
        author=package.author,
        language=package.language,
        file_format=FileFormat.EPUB,
        blocks=tuple(blocks),
        toc=tuple(e for e in toc if e.target in known_targets),
    )


# --------------------------------------------------------------------------- zip helpers


def _read(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"missing file in EPUB: {name}") from exc
    if info.flag_bits & _ZIP_ENCRYPTED_FLAG:
        raise ParseError(ParseErrorCode.ENCRYPTED_FILE, f"{name} is password protected")
    try:
        return archive.read(info)
    except _ZIP_ERRORS as exc:
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"cannot read {name}: {exc}") from exc
    except NotImplementedError as exc:  # compression method zipfile does not support
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"cannot read {name}: {exc}") from exc


def _parse_xml(data: bytes) -> BeautifulSoup:
    return BeautifulSoup(data, "xml")


def _replace_entity(match: re.Match[bytes]) -> bytes:
    name = match.group(1).decode("ascii")
    if name in _XML_ENTITIES:
        return match.group(0)
    char = html.entities.html5.get(f"{name};")
    return char.encode("utf-8") if char else match.group(0)


def _parse_xhtml(data: bytes) -> BeautifulSoup:
    """Parse a content document.

    EPUB content documents are XHTML, so a well-formed file is parsed as XML: the HTML parser
    would treat self-closing tags like <a id="x"/> and <div/> as open elements and nest the rest
    of the document inside them. A file that is not well-formed XML goes to the lenient HTML
    parser (the XML parser would "recover" by nesting elements wrongly).
    """
    xml_data = _NAMED_ENTITY.sub(_replace_entity, data)
    try:
        etree.fromstring(xml_data, parser=_STRICT_XML)
        soup = BeautifulSoup(xml_data, "xml")
    except etree.XMLSyntaxError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(data, "lxml")
    if soup.find("body") is None:
        soup = BeautifulSoup(b"<html><body></body></html>", "lxml")
    return soup


def _resolve(base_file: str, href: str) -> str:
    """Resolve an href relative to the file containing it into a zip path ("OEBPS/x.xhtml#id")."""
    href = unquote(href.strip())
    path, _, fragment = href.partition("#")
    if path:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), path))
    else:
        resolved = base_file
    return f"{resolved}#{fragment}" if fragment else resolved


# --------------------------------------------------------------------------- DRM


def _check_drm(archive: zipfile.ZipFile) -> None:
    names = set(archive.namelist())
    if "META-INF/rights.xml" in names:
        raise ParseError(ParseErrorCode.ENCRYPTED_FILE, "META-INF/rights.xml present (DRM)")
    if "META-INF/encryption.xml" not in names:
        return
    soup = _parse_xml(_read(archive, "META-INF/encryption.xml"))
    for method in soup.find_all("EncryptionMethod"):
        algorithm = str(method.get("Algorithm", "")).strip()
        if algorithm not in FONT_OBFUSCATION_ALGORITHMS:
            raise ParseError(ParseErrorCode.ENCRYPTED_FILE, f"content encrypted with {algorithm}")


# --------------------------------------------------------------------------- package (OPF)


@dataclass
class _Package:
    opf_path: str
    title: str | None = None
    author: str | None = None
    language: str | None = None
    spine: list[str] = field(default_factory=list)
    nav_path: str | None = None
    ncx_path: str | None = None


def _find_opf(archive: zipfile.ZipFile) -> str:
    container = _parse_xml(_read(archive, "META-INF/container.xml"))
    rootfile = container.find("rootfile")
    full_path = rootfile.get("full-path") if isinstance(rootfile, Tag) else None
    if not full_path:
        raise ParseError(ParseErrorCode.CORRUPT_FILE, "container.xml has no rootfile")
    return unquote(str(full_path))


def _text_of(tag: Tag | NavigableString | None) -> str | None:
    if not isinstance(tag, Tag):
        return None
    text = normalize_text(tag.get_text(" "))
    return text or None


def _read_package(archive: zipfile.ZipFile, opf_path: str) -> _Package:
    opf = _parse_xml(_read(archive, opf_path))
    package_tag = opf.find("package")
    if not isinstance(package_tag, Tag):
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"{opf_path} is not an OPF package")

    package = _Package(opf_path=opf_path)
    metadata = opf.find("metadata")
    if isinstance(metadata, Tag):
        package.title = _text_of(metadata.find("title"))
        package.author = _pick_author(metadata)
        package.language = _text_of(metadata.find("language"))

    # manifest: id -> (zip path, media type, properties)
    manifest: dict[str, tuple[str, str, str]] = {}
    manifest_tag = opf.find("manifest")
    if isinstance(manifest_tag, Tag):
        for item in manifest_tag.find_all("item"):
            item_id, href = item.get("id"), item.get("href")
            if not item_id or not href:
                continue
            manifest[str(item_id)] = (
                _resolve(opf_path, str(href)),
                str(item.get("media-type", "")),
                str(item.get("properties", "")),
            )
            if "nav" in str(item.get("properties", "")).split():
                package.nav_path = manifest[str(item_id)][0]

    spine_tag = opf.find("spine")
    if not isinstance(spine_tag, Tag):
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"{opf_path} has no spine")
    ncx_id = spine_tag.get("toc")
    if ncx_id and str(ncx_id) in manifest:
        package.ncx_path = manifest[str(ncx_id)][0]
    elif package.nav_path is None:
        for zip_path, media_type, _ in manifest.values():
            if media_type == _NCX_TYPE:
                package.ncx_path = zip_path
                break

    for itemref in spine_tag.find_all("itemref"):
        idref = str(itemref.get("idref", ""))
        if idref not in manifest:
            continue
        zip_path, media_type, _ = manifest[idref]
        if media_type not in _XHTML_TYPES:
            continue
        is_cover = "cover" in idref.lower() or "cover" in posixpath.basename(zip_path).lower()
        if itemref.get("linear") == "no" and is_cover:
            continue
        if zip_path not in package.spine:
            package.spine.append(zip_path)
    return package


def _pick_author(metadata: Tag) -> str | None:
    """First dc:creator, preferring role "aut" (EPUB2 opf:role attribute or EPUB3 refines)."""
    creators = metadata.find_all("creator")
    roles = _refinements(metadata, "role")
    file_as = _refinements(metadata, "file-as")
    first: str | None = None
    for creator in creators:
        name = _text_of(creator)
        if name is None:
            continue
        creator_id = str(creator.get("id"))
        name = _name_from_file_as(
            name, creator.get("opf:file-as") or creator.get("file-as") or file_as.get(creator_id)
        )
        role = creator.get("opf:role") or creator.get("role") or roles.get(creator_id)
        if role == "aut":
            return name
        if first is None:
            first = name
    return first


def _refinements(metadata: Tag, prop: str) -> dict[str, str]:
    """EPUB3 `<meta property="…" refines="#id">value</meta>` as {id: value}."""
    values: dict[str, str] = {}
    for meta in metadata.find_all("meta", attrs={"property": prop}):
        values[str(meta.get("refines", "")).lstrip("#")] = normalize_text(meta.get_text())
    return values


def _name_from_file_as(name: str, file_as: object) -> str:
    """Prefer the name part of `file-as` when dc:creator has words in front of the name.

    Gutenberg writes `dc:creator` "Emperor of Rome Marcus Aurelius" with `file-as`
    "Marcus Aurelius, Emperor of Rome": the part before the comma is the name. "Austen, Jane"
    (surname, given name) has a one-word first part, so "Jane Austen" is kept.
    """
    sort_name = normalize_text(str(file_as)) if file_as else ""
    if not sort_name or sort_name.split()[0].lower() == name.split()[0].lower():
        return name
    before_comma = sort_name.split(",", 1)[0].strip()
    return before_comma if len(before_comma.split()) >= 2 else name


# --------------------------------------------------------------------------- navigation


def _read_toc(archive: zipfile.ZipFile, package: _Package) -> list[TocEntry]:
    if package.nav_path is not None:
        entries = _read_nav(_read(archive, package.nav_path), package.nav_path)
        if entries:
            return entries
    if package.ncx_path is not None:
        return _read_ncx(_read(archive, package.ncx_path), package.ncx_path)
    return []


def _read_nav(data: bytes, nav_path: str) -> list[TocEntry]:
    soup = _parse_xhtml(data)
    navs = soup.find_all("nav")
    toc_nav = next((n for n in navs if "toc" in str(n.get("epub:type", "")).split()), None)
    if toc_nav is None:
        toc_nav = navs[0] if navs else None
    if toc_nav is None:
        return []
    top_list = toc_nav.find(["ol", "ul"])
    entries: list[TocEntry] = []
    if isinstance(top_list, Tag):
        _walk_nav_list(top_list, 1, nav_path, entries)
    return entries


def _walk_nav_list(list_tag: Tag, level: int, nav_path: str, out: list[TocEntry]) -> None:
    for li in list_tag.find_all("li", recursive=False):
        link = li.find(["a", "span"], recursive=False)
        if isinstance(link, Tag) and link.name == "a" and link.get("href"):
            title = normalize_text(strip_footnote_markers(link.get_text(" ")))
            if title:
                out.append(TocEntry(title, level, _resolve(nav_path, str(link["href"]))))
        sub = li.find(["ol", "ul"], recursive=False)
        if isinstance(sub, Tag):
            _walk_nav_list(sub, level + 1, nav_path, out)


def _read_ncx(data: bytes, ncx_path: str) -> list[TocEntry]:
    soup = _parse_xml(data)
    nav_map = soup.find("navMap")
    entries: list[TocEntry] = []
    if isinstance(nav_map, Tag):
        _walk_nav_points(nav_map, 1, ncx_path, entries)
    return entries


def _clean_label(raw: str) -> str:
    return normalize_text(strip_footnote_markers(raw))


def _walk_nav_points(parent: Tag, level: int, ncx_path: str, out: list[TocEntry]) -> None:
    for point in parent.find_all("navPoint", recursive=False):
        label = point.find("navLabel")
        content = point.find("content", recursive=False)
        title = _clean_label(label.get_text(" ")) if isinstance(label, Tag) else ""
        src = content.get("src") if isinstance(content, Tag) else None
        if title and src:
            out.append(TocEntry(title, level, _resolve(ncx_path, str(src))))
        _walk_nav_points(point, level + 1, ncx_path, out)


# --------------------------------------------------------------------------- XHTML -> blocks


class _BlockBuilder:
    """Collects blocks for the whole book and keeps track of anchors waiting for a block."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self._doc_path = ""
        self._pending: list[str] = []
        self._doc_has_block = False

    def start_document(self, doc_path: str) -> None:
        self._doc_path = doc_path
        self._pending = []
        self._doc_has_block = False

    def end_document(self) -> None:
        # Anchors after the last text of a document have nothing to land on.
        self._pending = []

    def add_ids(self, ids: list[str]) -> None:
        for element_id in ids:
            anchor = f"{self._doc_path}#{element_id}"
            if anchor not in self._pending:
                self._pending.append(anchor)

    def emit(self, raw_text: str, heading_level: int | None, ids: list[str]) -> None:
        self.add_ids(ids)
        text = normalize_text(strip_footnote_markers(join_hyphenated(raw_text)))
        if not text:
            return  # anchors stay pending for the next block
        anchors = self._pending
        if not self._doc_has_block:
            anchors = [self._doc_path, *anchors]
            self._doc_has_block = True
        self.blocks.append(Block(text, None, heading_level, tuple(anchors)))
        self._pending = []


def _classes(tag: Tag) -> set[str]:
    # The HTML parser gives a list, the XML parser a plain string.
    value = tag.get("class") or ""
    return set(value.split()) if isinstance(value, str) else set(value)


def _is_boilerplate(tag: Tag) -> bool:
    if tag.get("id") in GUTENBERG_IDS:
        return True
    return GUTENBERG_CLASS in _classes(tag)


def _is_skipped(tag: Tag) -> bool:
    if tag.name in _SKIPPED_TAGS or _classes(tag) & _SKIPPED_CLASSES:
        return True
    epub_types = set(str(tag.get("epub:type", "")).split())
    if epub_types & _SKIPPED_EPUB_TYPES:
        return True
    return tag.get("role") == "doc-pagebreak"


def _ids_in(tag: Tag) -> list[str]:
    ids = [str(tag["id"])] if tag.get("id") else []
    ids.extend(str(t["id"]) for t in tag.find_all(id=True))
    return ids


def _has_block_child(tag: Tag) -> bool:
    return tag.find(_BLOCK_TAGS) is not None


def _inline_text(tag: Tag, ids: list[str]) -> str:
    """All text of an element, skipping unwanted elements; collects ids on the way."""
    parts: list[str] = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            if not isinstance(child, PreformattedString):  # comments, CDATA, doctype
                parts.append(str(child))
        elif isinstance(child, Tag):
            if _is_boilerplate(child):
                continue
            if child.name == "br":
                parts.append(" ")
                continue
            if _is_skipped(child):
                ids.extend(_ids_in(child))
                continue
            if child.get("id"):
                ids.append(str(child["id"]))
            # Cells and nested blocks inside a leaf block are separated by a space.
            separator = " " if child.name in _BLOCK_TAGS else ""
            parts.append(separator + _inline_text(child, ids) + separator)
    return "".join(parts)


def _walk(container: Tag | None, builder: _BlockBuilder) -> None:
    """Emit one block per run of inline content and per leaf block element, in document order."""
    if container is None:
        return
    inline_parts: list[str] = []
    inline_ids: list[str] = []

    def flush() -> None:
        if inline_parts or inline_ids:
            builder.emit("".join(inline_parts), None, list(inline_ids))
        inline_parts.clear()
        inline_ids.clear()

    for child in container.children:
        if isinstance(child, NavigableString):
            if not isinstance(child, PreformattedString):
                inline_parts.append(str(child))
            continue
        if not isinstance(child, Tag) or _is_boilerplate(child):
            continue
        if child.name == "br":
            inline_parts.append(" ")
            continue
        if _is_skipped(child):
            inline_ids.extend(_ids_in(child))
            continue
        if child.name in _LEAF_BLOCK_TAGS:
            flush()
            ids = [str(child["id"])] if child.get("id") else []
            text = _inline_text(child, ids)
            builder.emit(text, _HEADING_LEVELS.get(child.name), ids)
        elif child.name in _BLOCK_TAGS or _has_block_child(child):
            flush()
            if child.get("id"):
                builder.add_ids([str(child["id"])])
            _walk(child, builder)
        else:
            if child.get("id"):
                inline_ids.append(str(child["id"]))
            inline_parts.append(_inline_text(child, inline_ids))
    flush()


def _strip_marker_boilerplate(blocks: list[Block]) -> list[Block]:
    """Fallback for files without pg-header/pg-footer ids: cut at the START/END markers."""
    start = next((i for i, b in enumerate(blocks) if is_gutenberg_start(b.text)), None)
    if start is not None:
        blocks = blocks[start + 1 :]
    end = next((i for i, b in enumerate(blocks) if is_gutenberg_end(b.text)), None)
    if end is not None:
        blocks = blocks[:end]
    return blocks
