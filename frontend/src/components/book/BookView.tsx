"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ContentsTab } from "@/components/book/ContentsTab";
import { CostTab } from "@/components/book/CostTab";
import { ProcessingStatus } from "@/components/book/ProcessingStatus";
import { StatusBadge } from "@/components/book/StatusBadge";
// TEMPORARY: when Track A's summary tab arrives, change this one line to
//   import { SummaryTab } from "@/components/summary/SummaryTab";
// and delete SummaryTabPlaceholder.tsx. Assumed props: <SummaryTab bookId={id} />.
import { SummaryTab } from "@/components/book/SummaryTabPlaceholder";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, errorMessage } from "@/lib/api";
import {
  type ActiveJob,
  type Book,
  getBook,
  getJob,
  isInProgress,
  POLL_INTERVAL_MS,
  retryBook,
} from "@/lib/books";
import { currentChapterAnchor } from "@/lib/chapter-anchor";

export function BookSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading the book">
      <Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-5 w-1/3" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

/** The book page (section 8.1): what the book is, what is happening to it, and its tabs. */
export function BookView() {
  const { id } = useParams<{ id: string }>();
  const [book, setBook] = useState<Book | null>(null);
  // The job being polled. It starts as the book's active_job and is then replaced by every
  // answer of GET /jobs/{id}.
  const [job, setJob] = useState<ActiveJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  // An address with a chapter anchor (#chapter-3) opens on the Contents tab. On the server
  // there is no address to read; that is harmless, because the tabs are rendered only after the
  // book has been loaded in the browser.
  const [tab, setTab] = useState(() =>
    typeof window !== "undefined" && currentChapterAnchor() ? "contents" : "summary",
  );

  useEffect(() => {
    let ignore = false;
    getBook(id)
      .then((loaded) => {
        if (ignore) return;
        setBook(loaded);
        setJob(loaded.active_job);
      })
      .catch((caught) => {
        if (!ignore) setError(errorMessage(caught));
      });
    return () => {
      ignore = true;
    };
  }, [id]);

  // Polling (FR-012). While the book is being processed, its job is asked for the stage and
  // progress every 2.5 s. When the job is no longer queued or running, the book is read again:
  // it is then ready, or failed with the message to show. The effect ends itself, because a
  // book that is ready or failed is not "in progress".
  const inProgress = book !== null && isInProgress(book.status);
  const jobId = job?.id ?? null;
  useEffect(() => {
    if (!inProgress) return;
    let ignore = false;

    async function poll() {
      try {
        if (jobId) {
          const current = await getJob(jobId);
          if (ignore) return;
          setJob(current);
          if (current.status === "queued" || current.status === "running") return;
        }
        const loaded = await getBook(id);
        if (ignore) return;
        setBook(loaded);
        setJob(loaded.active_job);
      } catch (caught) {
        // A lost connection or a busy server: the next round asks again. A book that no
        // longer exists will not come back.
        if (!ignore && caught instanceof ApiError && caught.status === 404) {
          setBook(null);
          setError(errorMessage(caught));
        }
      }
    }

    const timer = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      ignore = true;
      clearInterval(timer);
    };
  }, [id, inProgress, jobId]);

  // A link to a chapter clicked anywhere on the page switches to the Contents tab.
  useEffect(() => {
    const openContents = () => {
      if (currentChapterAnchor()) setTab("contents");
    };
    window.addEventListener("hashchange", openContents);
    return () => window.removeEventListener("hashchange", openContents);
  }, []);

  async function retry() {
    setRetrying(true);
    try {
      try {
        await retryBook(id);
      } catch (caught) {
        // 409: the book is not failed any more (a retry from another tab, for example).
        // Reading it again below shows what it really is.
        if (!(caught instanceof ApiError && caught.code === "BOOK_NOT_FAILED")) throw caught;
      }
      const loaded = await getBook(id);
      setBook(loaded);
      setJob(loaded.active_job);
    } catch (caught) {
      toast.error(errorMessage(caught), {
        action: { label: "Retry", onClick: () => void retry() },
      });
    } finally {
      setRetrying(false);
    }
  }

  if (!book) {
    if (!error) return <BookSkeleton />;
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
        <Link href="/" className="text-primary w-fit underline underline-offset-4">
          Back to the library
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Link href="/" className="text-primary w-fit text-sm underline underline-offset-4">
        ← Library
      </Link>

      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold break-words">{book.title}</h1>
        <p className="text-muted-foreground break-words">{book.author ?? "Unknown author"}</p>
        <p className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <StatusBadge status={book.status} className="text-foreground" />
          <span className="uppercase">{book.file_format}</span>
          {book.word_count !== null && <span>{book.word_count.toLocaleString("en")} words</span>}
          {book.chapter_count > 0 && <span>{book.chapter_count} chapters</span>}
          {book.is_demo && <span>Demo book</span>}
        </p>
      </header>

      <ProcessingStatus book={book} job={job} retrying={retrying} onRetry={retry} />

      <Tabs value={tab} onValueChange={setTab} className="gap-4">
        <TabsList className="h-10! w-full sm:w-fit">
          <TabsTrigger value="summary" className="px-3">
            Summary
          </TabsTrigger>
          <TabsTrigger value="contents" className="px-3">
            Contents
          </TabsTrigger>
          <TabsTrigger value="cost" className="px-3">
            Cost
          </TabsTrigger>
          {/* Questions arrive in week 3 (FR-011); until then the tab is shown but cannot be opened. */}
          <TabsTrigger value="ask" disabled title="Available soon" className="px-3">
            Ask
          </TabsTrigger>
        </TabsList>
        <TabsContent value="summary">
          <SummaryTab bookId={book.id} />
        </TabsContent>
        <TabsContent value="contents">
          <ContentsTab bookId={book.id} />
        </TabsContent>
        <TabsContent value="cost">
          <CostTab bookId={book.id} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
