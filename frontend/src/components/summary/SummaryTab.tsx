"use client";

import { useEffect, useState } from "react";

import { stageLabel } from "@/components/book/ProcessingStatus";
import { OptionGroup } from "@/components/summary/OptionGroup";
import { SummaryView } from "@/components/summary/SummaryView";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, errorMessage } from "@/lib/api";
import { type ActiveJob, getJob, POLL_INTERVAL_MS } from "@/lib/books";
import {
  findSummary,
  isQueued,
  requestSummary,
  type Summary,
  type SummaryLanguage,
  type SummaryLength,
} from "@/lib/summaries";

const LENGTHS = [
  { value: "5min", label: "5 minutes" },
  { value: "15min", label: "15 minutes" },
] as const;

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "cnr", label: "Montenegrin" },
] as const;

const GENERATION_FAILED = "The summary could not be generated.";

type Choice = { bookId: string; length: SummaryLength; language: SummaryLanguage };

type View =
  | { kind: "loading" }
  // No summary is stored for this choice; the user decides whether to generate one.
  | { kind: "none" }
  // jobId is null until the API has answered with the job; job is null until its first poll.
  | { kind: "generating"; jobId: string | null; job: ActiveJob | null }
  | { kind: "ready"; summary: Summary }
  // "again" is what the Try again button repeats: reading the stored summary, or generating.
  | { kind: "failed"; title: string; message: string; again: "load" | "generate" };

function loadFailed(error: unknown): View {
  return {
    kind: "failed",
    title: "The summary could not be loaded",
    message: errorMessage(error),
    again: "load",
  };
}

function generationFailed(message: string): View {
  return { kind: "failed", title: "The summary was not generated", message, again: "generate" };
}

/** Get or generate (POST): the stored summary, or the job that is generating it. */
async function requestedView({ bookId, length, language }: Choice): Promise<View> {
  const answer = await requestSummary(bookId, length, language);
  if (isQueued(answer)) return { kind: "generating", jobId: answer.job.id, job: null };
  return { kind: "ready", summary: answer };
}

/** What is stored for this choice, without starting a generation nobody asked for. */
async function storedView(choice: Choice): Promise<View> {
  const found = await findSummary(choice.bookId, choice.length, choice.language);
  if (found === null) return { kind: "none" };
  if (found.status === "ready") return { kind: "ready", summary: found };
  if (found.status === "failed") return generationFailed(GENERATION_FAILED);
  // Queued or running: it was started earlier (the page was reloaded, or in another tab).
  // Asking for it again starts nothing new and answers with the job to follow.
  return requestedView(choice);
}

/** One length and language of a book's summary: looks for a stored one, offers to generate it
 *  if there is none, follows the job while it is generated, and then shows it (UC-002).
 *  The parent gives it a key made of the choice, so a new choice starts from a clean state. */
function SummaryFor(choice: Choice) {
  const { bookId, length, language } = choice;
  const [view, setView] = useState<View>({ kind: "loading" });
  // Raised by "Try again" after a failed read, to read again.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let ignore = false;
    storedView({ bookId, length, language })
      .then((loaded) => {
        if (!ignore) setView(loaded);
      })
      .catch((caught) => {
        if (!ignore) setView(loadFailed(caught));
      });
    return () => {
      ignore = true;
    };
  }, [bookId, length, language, attempt]);

  // Polling, as for the book's own job (FR-012): every 2.5 s until the job is no longer queued
  // or running. The effect ends itself, because every other view has no job to follow.
  const jobId = view.kind === "generating" ? view.jobId : null;
  useEffect(() => {
    if (!jobId) return;
    let ignore = false;

    const poll = async () => {
      try {
        const job = await getJob(jobId);
        if (ignore) return;
        if (job.status === "queued" || job.status === "running") {
          setView({ kind: "generating", jobId, job });
          return;
        }
        if (job.status === "failed") {
          setView(generationFailed(job.error_message ?? GENERATION_FAILED));
          return;
        }
        const found = await findSummary(bookId, length, language);
        if (ignore) return;
        setView(
          found?.status === "ready"
            ? { kind: "ready", summary: found }
            : generationFailed(GENERATION_FAILED),
        );
      } catch (caught) {
        // A lost connection or a busy server: the next round asks again. A job that no longer
        // exists will not come back.
        if (!ignore && caught instanceof ApiError && caught.status === 404) {
          setView(loadFailed(caught));
        }
      }
    };

    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      ignore = true;
      clearInterval(timer);
    };
  }, [bookId, length, language, jobId]);

  function generate() {
    setView({ kind: "generating", jobId: null, job: null });
    requestedView({ bookId, length, language })
      .then(setView)
      .catch((caught) => setView(generationFailed(errorMessage(caught))));
  }

  function tryAgain(again: "load" | "generate") {
    if (again === "generate") {
      generate();
    } else {
      setView({ kind: "loading" });
      setAttempt((count) => count + 1);
    }
  }

  switch (view.kind) {
    case "loading":
      return <Skeleton className="h-40 w-full" aria-label="Loading the summary" />;

    case "none":
      return (
        <div className="bg-surface flex flex-col items-start gap-3 rounded-lg p-4">
          <p className="text-base">
            This book has no {length === "5min" ? "5-minute" : "15-minute"} summary in{" "}
            {language === "en" ? "English" : "Montenegrin"} yet.
          </p>
          <Button className="h-10 px-4" onClick={generate}>
            Generate summary
          </Button>
        </div>
      );

    case "generating":
      return (
        <section
          aria-labelledby="summary-generating"
          className="bg-surface flex flex-col gap-3 rounded-lg p-4"
        >
          <h2 id="summary-generating" className="text-base font-medium">
            Generating the summary
          </h2>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span aria-live="polite">{stageLabel(view.job)}</span>
            <span className="tabular-nums">{view.job?.progress ?? 0}%</span>
          </div>
          <Progress
            value={view.job?.progress ?? 0}
            aria-label="Summary progress"
            className="h-2 bg-white"
          />
          <p className="text-muted-foreground text-sm">
            You can leave this page. The summary is saved when it is finished.
          </p>
        </section>
      );

    case "failed":
      return (
        <Alert variant="destructive">
          <AlertTitle>{view.title}</AlertTitle>
          <AlertDescription>{view.message}</AlertDescription>
          <Button variant="outline" className="mt-2 h-9 w-fit" onClick={() => tryAgain(view.again)}>
            Try again
          </Button>
        </Alert>
      );

    case "ready":
      return <SummaryView summary={view.summary} />;
  }
}

type Props = {
  bookId: string;
  /** Summaries are built from the chapter notes, so only a processed book can have one. */
  bookReady: boolean;
};

/** The Summary tab of the book page (section 8.1, UC-002): the choice of length and language,
 *  and the summary for that choice. */
export function SummaryTab({ bookId, bookReady }: Props) {
  const [length, setLength] = useState<SummaryLength>("5min");
  const [language, setLanguage] = useState<SummaryLanguage>("en");

  if (!bookReady) {
    return (
      <p className="text-muted-foreground text-base">
        A summary can be generated once the book has been processed.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap gap-x-6 gap-y-3">
        <OptionGroup legend="Length" options={LENGTHS} value={length} onChange={setLength} />
        <OptionGroup
          legend="Language"
          options={LANGUAGES}
          value={language}
          onChange={setLanguage}
        />
      </div>
      <SummaryFor
        key={`${length}/${language}`}
        bookId={bookId}
        length={length}
        language={language}
      />
    </div>
  );
}
