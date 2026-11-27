import { chapterAnchor } from "@/lib/chapter-anchor";
import { type Summary, WORDS_PER_MINUTE } from "@/lib/summaries";

/** Text from the model as paragraphs: a blank line starts a new one. */
function Paragraphs({ text }: { text: string }) {
  return (
    <div className="reading-text flex flex-col gap-4">
      {text
        .split(/\n\s*\n/)
        .filter((paragraph) => paragraph.trim() !== "")
        .map((paragraph, index) => (
          <p key={index} className="whitespace-pre-line">
            {paragraph.trim()}
          </p>
        ))}
    </div>
  );
}

/**
 * A finished summary (FR-008): the overview, the summary of every chapter and, next to them, the
 * key ideas (FR-009). On a narrow screen the key ideas come between the overview and the
 * chapters.
 *
 * The interface is in English (specification 4.4), so only the generated text is marked with the
 * language of the summary: screen readers then read Montenegrin text with the right voice.
 */
export function SummaryView({ summary }: { summary: Summary }) {
  const lang = summary.language === "cnr" ? "cnr-Latn" : "en";
  const titles = new Map(
    summary.chapters.map((chapter) => [chapter.chapter_position, chapter.title]),
  );
  const minutes = Math.max(1, Math.round(summary.word_count / WORDS_PER_MINUTE));

  return (
    <div className="grid gap-x-8 gap-y-8 lg:grid-cols-[minmax(0,1fr)_14rem]">
      <section aria-labelledby="summary-overview" className="flex flex-col gap-3">
        <h2 id="summary-overview" className="text-lg font-semibold">
          Overview
        </h2>
        <div lang={lang}>
          <Paragraphs text={summary.overview} />
        </div>
        <p className="text-muted-foreground text-sm">
          {summary.word_count.toLocaleString("en")} words · about {minutes} min to read
        </p>
      </section>

      {summary.key_ideas.length > 0 && (
        <aside
          aria-labelledby="summary-key-ideas"
          className="bg-surface flex h-fit flex-col gap-3 rounded-lg p-4 lg:sticky lg:top-4 lg:col-start-2 lg:row-span-2 lg:row-start-1"
        >
          <h2 id="summary-key-ideas" className="text-base font-semibold">
            Key ideas
          </h2>
          <ol className="flex flex-col gap-4">
            {summary.key_ideas.map((idea, index) => (
              <li key={index} className="flex flex-col gap-1 text-sm leading-relaxed">
                <span lang={lang}>{idea.text}</span>
                {/* A plain link, not next/link: the book page opens the Contents tab on the
                    browser's hashchange event (see lib/chapter-anchor.ts). */}
                <a
                  href={`#${chapterAnchor(idea.chapter_position)}`}
                  className="text-primary w-fit underline underline-offset-4"
                >
                  {titles.get(idea.chapter_position) ?? "Source chapter"}
                </a>
              </li>
            ))}
          </ol>
        </aside>
      )}

      <section aria-labelledby="summary-chapters" className="flex flex-col gap-6">
        <h2 id="summary-chapters" className="text-lg font-semibold">
          Chapter by chapter
        </h2>
        {summary.chapters.map((chapter) => (
          <article key={chapter.chapter_position} className="flex flex-col gap-2">
            <h3 className="text-base font-semibold break-words">{chapter.title}</h3>
            <div lang={lang}>
              <Paragraphs text={chapter.content} />
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
