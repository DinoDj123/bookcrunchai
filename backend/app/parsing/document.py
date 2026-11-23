"""Internal document model shared by the format readers and chapter detection.

Not part of the parse_book contract: only Track A code uses these types. Readers (epub.py,
pdf.py) produce a RawDocument; chapters.py turns its blocks into contract Chapters.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import FileFormat


@dataclass(frozen=True, slots=True)
class Block:
    text: str  # already passed through normalize_text(), non-empty
    page: int | None  # PDF: 1-based page where the block starts; EPUB: None
    heading_level: int | None  # 1..6 when the block looks like a heading, else None
    anchors: tuple[str, ...] = ()  # EPUB: "path/in/zip.xhtml#id" targets that land on this block


@dataclass(frozen=True, slots=True)
class TocEntry:
    title: str  # normalized
    level: int  # 1 = top level
    target: str | int  # EPUB: "path/in/zip.xhtml" or "...#id"; PDF: 1-based page


@dataclass(frozen=True, slots=True)
class RawDocument:
    title: str | None
    author: str | None
    language: str | None
    file_format: FileFormat
    blocks: tuple[Block, ...]  # whole book in reading order, boilerplate already removed
    toc: tuple[TocEntry, ...]  # EPUB nav/NCX or PDF outline; may be empty
