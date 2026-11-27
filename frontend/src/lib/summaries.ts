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
  /** Always null for a ready summary; the API sends the field in every answer. */
  job: null;
};

/** The latest job of a summary that is not ready: what to poll, or why it failed. */
export type SummaryJob = {
  id: string;
  status: JobStatus;
  stage: string | null;
  progress: number;
  error_code: string | null;
  error_message: string | null;
};

/** A stored summary that cannot be shown: it is being generated, or its generation failed.
 *  The API sends the fields of Summary too, empty; only what the page uses is named here. */
export type UnfinishedSummary = {
  id: string;
  status: "queued" | "running" | "failed";
  job: SummaryJob | null;
};

/** The answer of POST /summaries when the summary has to be generated first (202). */
export type QueuedSummary = {
  summary: { id: string; status: string };
  job: { id: string; status: JobStatus };
};

/** The reading speed the lengths are based on (FR-008: 1,100 words for 5 minutes). */
export const WORDS_PER_MINUTE = 220;

/**
 * The stored summary for this length and language in whatever state it is, or null if it was
 * never requested (404 SUMMARY_NOT_FOUND). Never starts a generation, so it is safe to call when
 * the user only looks at the options.
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
    if (error instanceof ApiError && error.code === "SUMMARY_NOT_FOUND") return null;
    throw error;
  }
}

/**
 * Get or generate (FR-014): answers the summary if it is ready, otherwise queues its generation
 * and answers with the job to follow. For a summary that is already queued or running it answers
 * with that job and starts nothing new; a failed summary gets a new job.
 */
export function requestSummary(
  bookId: string,
  length: SummaryLength,
  language: SummaryLanguage,
): Promise<Summary | QueuedSummary> {
  return apiFetch(`/books/${bookId}/summaries`, { method: "POST", json: { length, language } });
}

/** apiFetch does not pass on the HTTP status (200 or 202), so the two answers of POST are told
 *  apart by their shape: only the queued one wraps the summary in `summary`. (Both have `job`:
 *  it is null in the ready one.) */
export function isQueued(answer: Summary | QueuedSummary): answer is QueuedSummary {
  return "summary" in answer;
}
