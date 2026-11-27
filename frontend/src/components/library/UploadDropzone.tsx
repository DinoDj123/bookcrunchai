"use client";

import { UploadIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { errorMessage } from "@/lib/api";
import { UPLOAD_EXTENSIONS, uploadBook, uploadProblem } from "@/lib/books";
import { cn } from "@/lib/utils";

/**
 * Upload by dropping a file or choosing one (FR-002). The extension and the size are checked
 * before anything is sent; the API checks them again, and the first bytes of the file too.
 *
 * The whole area is the label of a file input, so it works with the mouse, the keyboard and a
 * screen reader without extra code.
 */
export function UploadDropzone({ onUploaded }: { onUploaded: (bookId: string) => void }) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function upload(file: File) {
    const found = uploadProblem(file);
    setProblem(found);
    if (found) return;

    setUploading(true);
    try {
      const { book } = await uploadBook(file);
      onUploaded(book.id);
    } catch (caught) {
      setUploading(false);
      toast.error(errorMessage(caught), {
        action: { label: "Retry", onClick: () => void upload(file) },
      });
    }
  }

  function handleDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file && !uploading) void upload(file);
  }

  function handleChoose(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // so that choosing the same file again is a change
    if (file) void upload(file);
  }

  return (
    <div className="flex flex-col gap-2">
      <label
        onDragOver={(event) => {
          event.preventDefault(); // without this the browser opens the file instead
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={cn(
          "bg-surface flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center",
          "border-input focus-within:border-ring focus-within:ring-ring/50 focus-within:ring-3",
          dragging && "border-primary bg-primary/5",
          uploading && "cursor-progress opacity-70",
        )}
      >
        <UploadIcon aria-hidden="true" className="text-subtle size-6" />
        <span className="font-medium">
          {uploading ? "Uploading…" : "Drop a book here or choose a file"}
        </span>
        <span className="text-muted-foreground text-sm">EPUB or PDF, up to 50 MB</span>
        <input
          type="file"
          accept={UPLOAD_EXTENSIONS.join(",")}
          disabled={uploading}
          onChange={handleChoose}
          aria-describedby={problem ? "upload-problem" : undefined}
          className="sr-only"
        />
      </label>
      {/* Always in the page, so that a screen reader announces the text when it appears. */}
      <p id="upload-problem" role="alert" className="text-destructive text-sm empty:hidden">
        {problem}
      </p>
    </div>
  );
}
