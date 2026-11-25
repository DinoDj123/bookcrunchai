// The auth endpoints (specification 7.3). The session itself is an httpOnly cookie that only the
// browser and the API see.

import { apiFetch } from "@/lib/api";

export type User = { id: string; email: string };

export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;

export function getCurrentUser(): Promise<User> {
  return apiFetch<User>("/auth/me");
}

export function logIn(email: string, password: string): Promise<User> {
  return apiFetch<User>("/auth/login", { method: "POST", json: { email, password } });
}

/** Creates the account and logs the new user in. */
export function register(email: string, password: string): Promise<User> {
  return apiFetch<User>("/auth/register", { method: "POST", json: { email, password } });
}

export function logOut(): Promise<void> {
  return apiFetch("/auth/logout", { method: "POST" });
}
