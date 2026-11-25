// One way to call the backend from client components. Requests go to /api/v1 on the page's own
// origin (Caddy in Docker, the rewrite in next.config.ts for `npm run dev`), so the browser sends
// the session cookie by itself.

const API_PREFIX = "/api/v1";

/** Fired on `window` when a request is refused with 401, so the protected layout can send the
 *  user to /login wherever the request was made. */
export const UNAUTHENTICATED_EVENT = "bookcrunch:unauthenticated";

/** An error answer in the common format of specification 7.1:
 *  `{ "error": { "code": "BOOK_NOT_FOUND", "message": "…", "details": {} } }`. */
export class ApiError extends Error {
  /** HTTP status; 0 when the server could not be reached at all. */
  readonly status: number;
  /** A stable string such as `BOOK_NOT_FOUND` or `RATE_LIMITED`. */
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type ApiFetchOptions = Omit<RequestInit, "body"> & {
  /** Sent as a JSON body. */
  json?: unknown;
  /** Sent as it is, for example FormData for a file upload. */
  body?: BodyInit;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function errorFrom(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON: an error page from the proxy, for example.
  }
  const error = isRecord(body) ? body.error : null;
  if (isRecord(error) && typeof error.code === "string" && typeof error.message === "string") {
    const details = isRecord(error.details) ? error.details : {};
    return new ApiError(response.status, error.code, error.message, details);
  }
  return new ApiError(response.status, "HTTP_ERROR", `The request failed (${response.status}).`);
}

/**
 * Calls `/api/v1` + `path` and returns the JSON answer as `T` (`undefined` for 204).
 * Every failure is thrown as an ApiError: an error answer from the API with its code and message,
 * and a request that never got an answer as `NETWORK_ERROR`. A cancelled request (AbortSignal)
 * is thrown as the browser's AbortError.
 *
 *   const { books } = await apiFetch<BookList>("/books");
 *   await apiFetch("/auth/login", { method: "POST", json: { email, password } });
 */
export async function apiFetch<T = void>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { json, headers, ...init } = options;
  const requestHeaders = new Headers(headers);
  requestHeaders.set("Accept", "application/json");
  if (json !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
    init.body = JSON.stringify(json);
  }

  let response: Response;
  try {
    response = await fetch(API_PREFIX + path, {
      ...init,
      headers: requestHeaders,
      credentials: "same-origin",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "NETWORK_ERROR", "The server could not be reached.");
  }

  if (!response.ok) {
    const error = await errorFrom(response);
    if (error.status === 401 && error.code === "UNAUTHENTICATED") {
      window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT));
    }
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function waitText(seconds: number): string {
  if (seconds < 90) return `${Math.ceil(seconds)} seconds`;
  return `${Math.ceil(seconds / 60)} minutes`;
}

/** The text to show the user for any thrown value. API messages are already written for the
 *  user; the rate limit and a lost connection get a clearer text. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "RATE_LIMITED") {
      const seconds = error.details.retry_after_seconds;
      return typeof seconds === "number"
        ? `Too many requests. Try again in ${waitText(seconds)}.`
        : "Too many requests. Try again later.";
    }
    if (error.code === "NETWORK_ERROR") {
      return "The server could not be reached. Check your connection and try again.";
    }
    return error.message;
  }
  return "Something went wrong. Try again.";
}
