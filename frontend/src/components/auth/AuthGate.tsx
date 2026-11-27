"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, errorMessage, UNAUTHENTICATED_EVENT } from "@/lib/api";
import { getCurrentUser, logOut, type User } from "@/lib/auth";

const UserContext = createContext<User | null>(null);

/** The logged-in user, in any component under the protected layout. */
export function useUser(): User {
  const user = useContext(UserContext);
  if (!user) throw new Error("useUser must be used inside AuthGate");
  return user;
}

/**
 * Wraps every protected page (section 8.2): asks GET /auth/me who is logged in and shows the
 * page only once the answer is a user. Without a session the visitor goes to /login.
 *
 * This decides only what the browser shows. The data is protected by the API, which checks the
 * session on every request.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Any request refused with 401 ends here: /auth/me below, or a later one when the session
    // has expired (24 hours, no refresh token).
    const toLogin = () => router.replace("/login");
    window.addEventListener(UNAUTHENTICATED_EVENT, toLogin);

    let ignore = false;
    getCurrentUser()
      .then((current) => {
        if (!ignore) setUser(current);
      })
      .catch((caught) => {
        const unauthenticated = caught instanceof ApiError && caught.status === 401;
        if (!ignore && !unauthenticated) setError(errorMessage(caught));
      });

    return () => {
      ignore = true;
      window.removeEventListener(UNAUTHENTICATED_EVENT, toLogin);
    };
  }, [router]);

  async function handleLogOut() {
    try {
      await logOut();
    } catch {
      // Whatever went wrong, the login page is where the user wants to be.
    }
    router.replace("/login");
  }

  return (
    <>
      <a
        href="#content"
        className="bg-background sr-only z-10 px-3 py-2 focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="border-b">
        <div className="mx-auto flex w-full max-w-4xl items-center justify-between gap-3 px-4 py-3">
          <Link href="/" className="text-lg font-semibold">
            bookcrunch.ai
          </Link>
          {user && (
            <div className="flex min-w-0 items-center gap-3">
              <span className="text-muted-foreground hidden truncate text-sm sm:inline">
                {user.email}
              </span>
              <Button variant="outline" onClick={handleLogOut}>
                Log out
              </Button>
            </div>
          )}
        </div>
      </header>

      <main id="content" className="mx-auto w-full max-w-4xl flex-1 px-4 py-8">
        {user ? (
          <UserContext.Provider value={user}>{children}</UserContext.Provider>
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
            <Button variant="outline" className="mt-2 w-fit" onClick={() => location.reload()}>
              Try again
            </Button>
          </Alert>
        ) : (
          <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-24 w-full" />
          </div>
        )}
      </main>
    </>
  );
}
