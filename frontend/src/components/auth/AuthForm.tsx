"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, errorMessage } from "@/lib/api";
import { logIn, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, register } from "@/lib/auth";

const TEXT = {
  login: {
    title: "Log in",
    submit: "Log in",
    pending: "Logging in…",
    otherQuestion: "No account yet?",
    otherLabel: "Create an account",
    otherHref: "/register",
  },
  register: {
    title: "Create an account",
    submit: "Create account",
    pending: "Creating account…",
    otherQuestion: "Already have an account?",
    otherLabel: "Log in",
    otherHref: "/login",
  },
} as const;

function messageFor(error: unknown): string {
  // The API's text for a rejected field is only "Invalid request."
  if (error instanceof ApiError && error.code === "VALIDATION_ERROR") {
    return `Enter a valid email address and a password of ${PASSWORD_MIN_LENGTH} to ${PASSWORD_MAX_LENGTH} characters.`;
  }
  return errorMessage(error);
}

/** The login and registration forms: the same layout, a different endpoint (section 8.1). */
export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const text = TEXT[mode];
  const isRegister = mode === "register";

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await (isRegister ? register(email, password) : logIn(email, password));
      router.replace("/");
    } catch (caught) {
      setError(messageFor(caught));
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">{text.title}</h1>

      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="h-10"
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete={isRegister ? "new-password" : "current-password"}
            required
            minLength={isRegister ? PASSWORD_MIN_LENGTH : undefined}
            maxLength={PASSWORD_MAX_LENGTH}
            aria-describedby={isRegister ? "password-hint" : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="h-10"
          />
          {isRegister && (
            <p id="password-hint" className="text-muted-foreground text-sm">
              At least {PASSWORD_MIN_LENGTH} characters.
            </p>
          )}
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <Button type="submit" disabled={pending} className="h-10">
          {pending ? text.pending : text.submit}
        </Button>
      </form>

      <p className="text-sm">
        {text.otherQuestion}{" "}
        <Link href={text.otherHref} className="text-primary underline underline-offset-4">
          {text.otherLabel}
        </Link>
      </p>
    </div>
  );
}
