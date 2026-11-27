"use client";

import { useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/api";
import { getUsage, type Usage, type UsageReport } from "@/lib/books";

// The pipeline steps of GET /usage (specification 7.2). An unknown step is shown as it is sent.
const STEP_LABEL: Record<string, string> = {
  map: "Chapter notes",
  reduce: "Summary",
  embed: "Embeddings",
  qa: "Questions",
};

// cost_usd arrives as a decimal string with six places. Costs of one call are fractions of a
// cent, so up to four places are shown; a number is precise enough for showing them.
const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

function UsageCells({ usage }: { usage: Usage }) {
  return (
    <>
      <td className="px-3 py-2 text-right tabular-nums">
        {usage.input_tokens.toLocaleString("en")}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {usage.output_tokens.toLocaleString("en")}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{usd.format(Number(usage.cost_usd))}</td>
    </>
  );
}

/** Tokens and estimated cost of the book by pipeline step and in total (FR-013). */
export function CostTab({ bookId }: { bookId: string }) {
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    getUsage(bookId)
      .then((answer) => {
        if (!ignore) setUsage(answer);
      })
      .catch((caught) => {
        if (!ignore) setError(errorMessage(caught));
      });
    return () => {
      ignore = true;
    };
  }, [bookId]);

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!usage) return <Skeleton className="h-32 w-full" />;
  if (usage.steps.length === 0) {
    return (
      <p className="text-muted-foreground">
        No AI calls have been made for this book yet, so it has cost nothing so far.
      </p>
    );
  }

  return (
    // The table scrolls sideways by itself on a narrow screen; the page never does.
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <caption className="text-muted-foreground caption-bottom px-3 py-2 text-left">
          Estimated cost of the AI calls made for this book.
        </caption>
        <thead>
          <tr className="bg-surface text-left">
            <th scope="col" className="px-3 py-2 font-medium">
              Step
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Input tokens
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Output tokens
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Cost
            </th>
          </tr>
        </thead>
        <tbody className="divide-y border-y">
          {usage.steps.map((step) => (
            <tr key={step.step}>
              <th scope="row" className="px-3 py-2 text-left font-normal">
                {STEP_LABEL[step.step] ?? step.step}
              </th>
              <UsageCells usage={step} />
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="font-medium">
            <th scope="row" className="px-3 py-2 text-left font-medium">
              Total
            </th>
            <UsageCells usage={usage.total} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
