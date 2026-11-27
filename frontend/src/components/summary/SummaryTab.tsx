// Stub of the summary tab (Track A). It takes the props BookView passes, so BookView only changes
// its import from the placeholder to this file. The real tab replaces the body.

export function SummaryTab({ bookId }: { bookId: string }) {
  return (
    <p className="reading-text text-muted-foreground" data-book-id={bookId}>
      Summary is coming.
    </p>
  );
}
