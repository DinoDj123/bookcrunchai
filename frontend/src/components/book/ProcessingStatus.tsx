"use client";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { type ActiveJob, type Book, isInProgress } from "@/lib/books";

/** "chapter_notes" -> "Chapter notes". The stages are shown as the API names them; the page
 *  keeps no list of its own, so a stage added to the pipeline appears without a change here.
 *  Also used for the job that generates a summary (SummaryTab). */
export function stageLabel(job: ActiveJob | null): string {
  if (job?.stage) {
    const words = job.stage.replaceAll("_", " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  return job?.status === "running" ? "Starting" : "Waiting in the queue";
}

type Props = {
  book: Book;
  /** The job being polled; null if the book has none. */
  job: ActiveJob | null;
  retrying: boolean;
  onRetry: () => void;
};

/** What is happening to a book that is not ready (FR-012): the stage and progress of its job
 *  while it runs, and the reason with a Retry button when it failed. Nothing for a ready book. */
export function ProcessingStatus({ book, job, retrying, onRetry }: Props) {
  if (isInProgress(book.status)) {
    const progress = job?.progress ?? 0;
    return (
      <section
        aria-labelledby="processing"
        className="bg-surface flex flex-col gap-3 rounded-lg p-4"
      >
        <h2 id="processing" className="font-medium">
          Processing the book
        </h2>
        <div className="flex items-baseline justify-between gap-3 text-sm">
          {/* Only the stage is announced; the percentage changes too often to be read out. */}
          <span aria-live="polite">{stageLabel(job)}</span>
          <span className="tabular-nums">{progress}%</span>
        </div>
        <Progress value={progress} aria-label="Processing progress" className="h-2 bg-white" />
      </section>
    );
  }

  if (book.status === "failed") {
    return (
      <Alert variant="destructive">
        <AlertTitle>Processing failed</AlertTitle>
        <AlertDescription>
          {book.error_message ?? "The book could not be processed."}
        </AlertDescription>
        {/* Demo books are read-only: only the owner can start processing again. */}
        {!book.is_demo && (
          <Button
            variant="outline"
            className="mt-2 h-9 w-fit"
            disabled={retrying}
            onClick={onRetry}
          >
            {retrying ? "Retrying…" : "Retry"}
          </Button>
        )}
      </Alert>
    );
  }

  return null;
}
