import type { Metadata } from "next";
import { Suspense } from "react";

import { BookSkeleton, BookView } from "@/components/book/BookView";

export const metadata: Metadata = { title: "Book – bookcrunch.ai" };

export default function BookPage() {
  return (
    // BookView reads the book ID from the address (useParams), which is known only at request
    // time. With Cache Components such a component must be inside Suspense; the skeleton is
    // what the prerendered page shows until then.
    <Suspense fallback={<BookSkeleton />}>
      <BookView />
    </Suspense>
  );
}
