// Chapters are addressed inside the book page as #chapter-<position>, for example
// /books/<id>#chapter-3. The Contents tab gives every chapter that id; a link to it from anywhere
// on the page (a key idea in the summary, FR-009) opens the Contents tab at that chapter.
//
// Use a plain <a href="#chapter-3"> for such links: the page reacts to the browser's hashchange
// event, which a history.pushState navigation does not fire.

import { useSyncExternalStore } from "react";

const CHAPTER_ANCHOR = /^chapter-\d+$/;

export function chapterAnchor(position: number): string {
  return `chapter-${position}`;
}

/** The chapter anchor in the page address ("chapter-3"), or null if there is none. */
export function currentChapterAnchor(): string | null {
  const anchor = window.location.hash.slice(1);
  return CHAPTER_ANCHOR.test(anchor) ? anchor : null;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

/** currentChapterAnchor() as a value that re-renders the component when the address changes. */
export function useChapterAnchor(): string | null {
  return useSyncExternalStore(subscribe, currentChapterAnchor, () => null);
}
