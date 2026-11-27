import { AuthGate } from "@/components/auth/AuthGate";

// Every page in this folder needs a logged-in user (marked [auth] in section 8.2).
export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return <AuthGate>{children}</AuthGate>;
}
