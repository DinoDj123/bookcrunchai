// The pages a visitor sees without a session: login and registration.
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-8 px-4 py-12">
      <p className="text-lg font-semibold">bookcrunch.ai</p>
      {children}
    </main>
  );
}
