"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/book/StatusBadge";
import { UploadDropzone } from "@/components/library/UploadDropzone";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/api";
import { type BookListItem, isInProgress, listBooks, POLL_INTERVAL_MS } from "@/lib/books";

function BookCard({ book }: { book: BookListItem }) {
  return (
    <li>
      <Link
        href={`/books/${book.id}`}
        className="hover:bg-surface flex h-full flex-col gap-2 rounded-lg border p-4"
      >
        <span className="font-medium break-words">{book.title}</span>
        <span className="text-muted-foreground text-sm break-words">
          {book.author ?? "Unknown author"}
        </span>
        <span className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 pt-2 text-sm">
          <StatusBadge status={book.status} className="text-foreground" />
          <span className="uppercase">{book.file_format}</span>
          {book.word_count !== null && <span>{book.word_count.toLocaleString("en")} words</span>}
        </span>
      </Link>
    </li>
  );
}

function BookGrid({ books, empty }: { books: BookListItem[]; empty: string }) {
  if (books.length === 0) return <p className="text-muted-foreground">{empty}</p>;
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {books.map((book) => (
        <BookCard key={book.id} book={book} />
      ))}
    </ul>
  );
}

/** The library (section 8.1, FR-015): the user's own books with the upload area, and the demo
 *  books. The API sends one list; `is_demo` tells the two apart. */
export function Library() {
  const router = useRouter();
  const [books, setBooks] = useState<BookListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // While a book is being processed the list is read again, so its status does not go stale.
  const refreshing = books?.some((book) => isInProgress(book.status)) ?? false;

  useEffect(() => {
    let ignore = false;
    const load = () =>
      listBooks()
        .then((answer) => {
          if (ignore) return;
          setBooks(answer.books);
          setError(null);
        })
        .catch((caught) => {
          if (!ignore) setError(errorMessage(caught));
        });

    void load();
    const timer = refreshing ? setInterval(load, POLL_INTERVAL_MS) : undefined;
    return () => {
      ignore = true;
      clearInterval(timer);
    };
  }, [refreshing]);

  const myBooks = books?.filter((book) => !book.is_demo);
  const demoBooks = books?.filter((book) => book.is_demo);

  return (
    <div className="flex flex-col gap-10">
      <h1 className="sr-only">Library</h1>

      {/* A failed refresh keeps the list that is already shown. */}
      {error && !books && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <section aria-labelledby="my-books" className="flex flex-col gap-4">
        <h2 id="my-books" className="text-xl font-semibold">
          My books
        </h2>
        <UploadDropzone onUploaded={(bookId) => router.push(`/books/${bookId}`)} />
        {myBooks ? (
          <BookGrid books={myBooks} empty="You have not uploaded a book yet." />
        ) : (
          !error && <Skeleton className="h-24 w-full" />
        )}
      </section>

      <section aria-labelledby="demo-library" className="flex flex-col gap-4">
        <h2 id="demo-library" className="text-xl font-semibold">
          Demo library
        </h2>
        {demoBooks ? (
          <BookGrid books={demoBooks} empty="There are no demo books yet." />
        ) : (
          !error && <Skeleton className="h-24 w-full" />
        )}
      </section>
    </div>
  );
}
