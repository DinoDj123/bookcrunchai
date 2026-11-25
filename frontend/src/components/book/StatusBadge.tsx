import { cn } from "@/lib/utils";
import type { BookStatus } from "@/lib/books";

const STATUS: Record<BookStatus, { label: string; dot: string }> = {
  uploaded: { label: "Waiting", dot: "bg-subtle" },
  processing: { label: "Processing", dot: "bg-warning" },
  ready: { label: "Ready", dot: "bg-success" },
  failed: { label: "Failed", dot: "bg-destructive" },
};

/** The status as a coloured dot and a word. The word carries the meaning and is in the text
 *  colour: the success and warning colours of 8.3 are too light for small text on white. */
export function StatusBadge({ status, className }: { status: BookStatus; className?: string }) {
  const { label, dot } = STATUS[status] ?? { label: status, dot: "bg-subtle" };
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", className)}>
      <span aria-hidden="true" className={cn("size-2 rounded-full", dot)} />
      {label}
    </span>
  );
}
