// The book and job endpoints (specification 7.2) with the shapes the API answers.

import { apiFetch } from "@/lib/api";

export type BookStatus = "uploaded" | "processing" | "ready" | "failed";
export type JobStatus = "queued" | "running" | "done" | "failed";

export type BookListItem = {
  id: string;
  title: string;
  author: string | null;
  file_format: string;
  status: BookStatus;
  error_code: string | null;
  word_count: number | null;
  is_demo: boolean;
  created_at: string;
};

/** The job to poll while a book is being processed. */
export type ActiveJob = {
  id: string;
  type: string;
  status: JobStatus;
  stage: string | null;
  progress: number;
};

export type Usage = {
  input_tokens: number;
  output_tokens: number;
  /** A decimal string such as "0.032000", so that no rounding happens on the way. */
  cost_usd: string;
};

export type Book = {
  id: string;
  title: string;
  author: string | null;
  source_language: string | null;
  file_format: string;
  status: BookStatus;
  error_code: string | null;
  /** Why processing failed, in words for the user; null unless the status is "failed". */
  error_message: string | null;
  chapters_detected: boolean;
  chapter_count: number;
  word_count: number | null;
  is_demo: boolean;
  active_job: ActiveJob | null;
  usage: Usage;
};

export type Chapter = {
  position: number;
  title: string;
  kind: string;
  word_count: number;
  has_notes: boolean;
};

export type StepUsage = Usage & { step: string };
export type UsageReport = { steps: StepUsage[]; total: Usage };

export type Job = ActiveJob & {
  book_id: string;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type UploadResult = {
  book: { id: string; title: string; status: BookStatus };
  job: { id: string; status: JobStatus } | null;
};

/** How often a running job is asked for its status (FR-012: every 2-3 seconds). */
export const POLL_INTERVAL_MS = 2500;

/** The same limits the API enforces (section 6.3); checked here first to save a 50 MB upload. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const UPLOAD_EXTENSIONS = [".epub", ".pdf"];

export function isInProgress(status: BookStatus): boolean {
  return status === "uploaded" || status === "processing";
}

/** Why this file cannot be uploaded, or null if it can. */
export function uploadProblem(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!UPLOAD_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return "Only EPUB and PDF files can be uploaded.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "The file is larger than 50 MB.";
  }
  return null;
}

export function listBooks(): Promise<{ books: BookListItem[] }> {
  return apiFetch("/books");
}

export function getBook(id: string): Promise<Book> {
  return apiFetch(`/books/${id}`);
}

export function listChapters(bookId: string): Promise<{ chapters: Chapter[] }> {
  return apiFetch(`/books/${bookId}/chapters`);
}

export function getUsage(bookId: string): Promise<UsageReport> {
  return apiFetch(`/books/${bookId}/usage`);
}

export function getJob(id: string): Promise<Job> {
  return apiFetch(`/jobs/${id}`);
}

/** Answers the new book, or the existing one if this user already uploaded the same file. */
export function uploadBook(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch("/books", { method: "POST", body: form });
}

export function retryBook(id: string): Promise<UploadResult> {
  return apiFetch(`/books/${id}/retry`, { method: "POST" });
}
