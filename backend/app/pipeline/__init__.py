"""Public entry point of the pipeline package (chunking and chapter notes).

The worker imports ONLY from here:

    from app.pipeline import get_chunk_chapter, get_write_chapter_notes, validate_chunks, ...

Which implementation is used is set by environment variables, like BOOKCRUNCH_PARSER:
    BOOKCRUNCH_CHUNKER  fake -> app.pipeline.fake.fake_chunk_chapter
                        real -> app.pipeline.chunking.chunk_chapter         (default)
    BOOKCRUNCH_NOTES    fake -> app.pipeline.fake.fake_write_chapter_notes
                        real -> app.pipeline.notes.write_chapter_notes      (default)
"""

import os

from .contract import (
    CONTRACT_VERSION,
    DEFAULT_NOTES_BUDGET_TOKENS,
    MAX_CHUNK_WORDS,
    MAX_NOTES_WORDS,
    NOTES_KINDS,
    PARAGRAPH_SEPARATOR,
    TARGET_CHUNK_WORDS,
    ChapterForNotes,
    ChunkChapterFn,
    ChunkDraft,
    PipelineContractViolation,
    WriteChapterNotesFn,
    estimate_tokens,
    needs_notes,
    validate_chapter_notes,
    validate_chunks,
)


def _implementation(variable: str) -> str:
    impl = os.getenv(variable, "real").strip().lower()
    if impl not in ("fake", "real"):
        raise ValueError(f"{variable} must be 'fake' or 'real', got {impl!r}")
    return impl


def get_chunk_chapter() -> ChunkChapterFn:
    if _implementation("BOOKCRUNCH_CHUNKER") == "fake":
        from .fake import fake_chunk_chapter

        return fake_chunk_chapter
    from .chunking import chunk_chapter

    return chunk_chapter


def get_write_chapter_notes() -> WriteChapterNotesFn:
    if _implementation("BOOKCRUNCH_NOTES") == "fake":
        from .fake import fake_write_chapter_notes

        return fake_write_chapter_notes
    from .notes import write_chapter_notes

    return write_chapter_notes


__all__ = [
    "CONTRACT_VERSION",
    "DEFAULT_NOTES_BUDGET_TOKENS",
    "MAX_CHUNK_WORDS",
    "MAX_NOTES_WORDS",
    "NOTES_KINDS",
    "PARAGRAPH_SEPARATOR",
    "TARGET_CHUNK_WORDS",
    "ChapterForNotes",
    "ChunkChapterFn",
    "ChunkDraft",
    "PipelineContractViolation",
    "WriteChapterNotesFn",
    "estimate_tokens",
    "get_chunk_chapter",
    "get_write_chapter_notes",
    "needs_notes",
    "validate_chapter_notes",
    "validate_chunks",
]
