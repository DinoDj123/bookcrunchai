// The summary endpoints (specification 7.2; FR-008, FR-009) with the shapes the API answers.

import { ApiError, apiFetch } from "@/lib/api";
import type { JobStatus } from "@/lib/books";

export type SummaryLength = "5min" | "15min";
export type SummaryLanguage = "en" | "cnr";

export type ChapterSummary = {
  /** The chapter's place in the book, the same number as in GET /chapters. */
  chapter_position: number;
  title: string;
  content: string;
};

export type KeyIdea = {
  text: string;
  /** The chapter the idea comes from (FR-009). */
  chapter_position: number;
};

export type Summary = {
  id: string;
  status: "ready";
  length: SummaryLength;
  language: SummaryLanguage;
  overview: string;
  chapters: ChapterSummary[];
  key_ideas: KeyIdea[];
  word_count: number;
};

/** A stored summary that cannot be shown: it is being generated, or its generation failed. */
export type UnfinishedSummary = {
  id: string;
  status: "queued" | "running" | "failed";
};

/** The answer of POST /summaries when the summary has to be generated first (202). */
export type QueuedSummary = {
  summary: { id: string; status: string };
  job: { id: string; status: JobStatus };
};

/** The reading speed the lengths are based on (FR-008: 1,100 words for 5 minutes). */
export const WORDS_PER_MINUTE = 220;

/**
 * The stored summary for this length and language, or null if there is none. Never starts a
 * generation, so it is safe to call when the user only looks at the options.
 *
 * ASSUMPTION, to be confirmed with the summary endpoints: the specification lists
 * GET /books/{id}/summaries but does not describe its answers. This expects the summary as
 * POST returns it, `{ id, status }` at least while it is not ready, and 404 with a code other
 * than BOOK_NOT_FOUND when no summary exists for the combination.
 */
export async function findSummary(
  bookId: string,
  length: SummaryLength,
  language: SummaryLanguage,
): Promise<Summary | UnfinishedSummary | null> {
  const query = new URLSearchParams({ length, language });
  try {
    return await apiFetch<Summary | UnfinishedSummary>(`/books/${bookId}/summaries?${query}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.code !== "BOOK_NOT_FOUND") {
      return null;
    }
    throw error;
  }
}

/**
 * Get or generate (FR-014): answers the summary if it is stored, otherwise queues its generation
 * and answers with the job to follow.
 */
export function requestSummary(
  bookId: string,
  length: SummaryLength,
  language: SummaryLanguage,
): Promise<Summary | QueuedSummary> {
  return apiFetch(`/books/${bookId}/summaries`, { method: "POST", json: { length, language } });
}

/** apiFetch does not pass on the HTTP status (200 or 202), so the two answers of POST are told
 *  apart by their shape: only the queued one has a job. */
export function isQueued(answer: Summary | QueuedSummary): answer is QueuedSummary {
  return "job" in answer;
}
