// Placeholder home page for the skeleton. Replaced by the library page (section 8.1).
export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center gap-4 px-6 py-16">
      <h1 className="text-3xl font-semibold">bookcrunch.ai</h1>
      <p className="text-muted-foreground text-lg leading-relaxed">
        Upload an EPUB or PDF, get a faithful summary and ask questions with answers that cite the
        exact passage.
      </p>
      <a href="/api/v1/docs" className="text-primary underline-offset-4 hover:underline">
        API documentation
      </a>
    </main>
  );
}
