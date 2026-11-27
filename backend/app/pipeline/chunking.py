"""chunk_chapter(): the real chunker (Track A). Skeleton until the implementation is merged; set
BOOKCRUNCH_CHUNKER=fake meanwhile. Rules: docs/contracts/pipeline.md, section 3."""

from app.parsing import Chapter

from .contract import ChunkDraft


def chunk_chapter(chapter: Chapter) -> tuple[ChunkDraft, ...]:
    raise NotImplementedError("the real chunker is not merged yet; set BOOKCRUNCH_CHUNKER=fake")
