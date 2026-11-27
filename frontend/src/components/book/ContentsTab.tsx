"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/api";
import { type Chapter, listChapters } from "@/lib/books";
import { chapterAnchor, useChapterAnchor } from "@/lib/chapter-anchor";
import { cn } from "@/lib/utils";

const KIND_LABEL: Record<string, string> = {
  front_matter: "Front matter",
  back_matter: "Back matter",
};

/** The table of contents (FR-005). Every chapter has the id `chapter-<position>`, so
 *  /books/<id>#chapter-3 leads to the third entry (see lib/chapter-anchor.ts). */
export function ContentsTab({ bookId }: { bookId: string }) {
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const anchor = useChapterAnchor();

  useEffect(() => {
    let ignore = false;
    listChapters(bookId)
      .then((answer) => {
        if (!ignore) setChapters(answer.chapters);
      })
      .catch((caught) => {
        if (!ignore) setError(errorMessage(caught));
      });
    return () => {
      ignore = true;
    };
  }, [bookId]);

  // The browser cannot scroll to an anchor that is not in the page yet, so it is done here once
  // the chapters have arrived, and again when the anchor changes.
  useEffect(() => {
    if (chapters && anchor) {
      document.getElementById(anchor)?.scrollIntoView({ block: "center" });
    }
  }, [chapters, anchor]);

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!chapters) return <Skeleton className="h-40 w-full" />;
  if (chapters.length === 0) {
    return (
      <p className="text-muted-foreground">
        The chapters appear here once the book has been processed.
      </p>
    );
  }

  return (
    <ol className="divide-y rounded-lg border">
      {chapters.map((chapter) => {
        const id = chapterAnchor(chapter.position);
        return (
          <li
            key={chapter.position}
            id={id}
            aria-current={anchor === id ? "location" : undefined}
            className={cn(
              "flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-3",
              anchor === id && "bg-surface",
            )}
          >
            <span className="min-w-0 text-base break-words">
              <span className="text-muted-foreground mr-2 tabular-nums">{chapter.position}.</span>
              {chapter.title}
            </span>
            <span className="text-muted-foreground text-sm">
              {KIND_LABEL[chapter.kind] && `${KIND_LABEL[chapter.kind]} · `}
              {chapter.word_count.toLocaleString("en")} words
            </span>
          </li>
        );
      })}
    </ol>
  );
}
