// TEMPORARY. Stands in for Track A's summary tab (@/components/summary/SummaryTab), which does
// not exist yet. It takes the same props the real one is expected to take, so that BookView
// changes by one import line when the real file arrives. Delete this file then.

export function SummaryTab({ bookId }: { bookId: string }) {
  return (
    <p className="reading-text text-muted-foreground" data-book-id={bookId}>
      Summary is coming.
    </p>
  );
}
