"use client";

// The spine: the line that runs through the masthead, with one stitch per story.
// A stitch fills in once that story has been opened, so the reader can see how much
// of the edition is left. It is a count of what remains, not a streak or a score.
//
// Read state lives in localStorage only (no account, nothing sent to a server).

import { useEffect, useSyncExternalStore } from "react";

const EVENT = "throughline:read-changed";

function storageKey(editionId: number) {
  return `throughline:read:${editionId}`;
}

function readIds(editionId: number): number[] {
  try {
    const raw = window.localStorage.getItem(storageKey(editionId));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((n): n is number => typeof n === "number") : [];
  } catch {
    return []; // private mode, blocked storage, or a corrupted value
  }
}

export function markRead(editionId: number, storyId: number) {
  try {
    const ids = readIds(editionId);
    if (ids.includes(storyId)) return;
    window.localStorage.setItem(storageKey(editionId), JSON.stringify([...ids, storyId]));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // Reading still works without the progress line.
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** Ids of the stories already opened in this edition. Empty on the server. */
export function useReadIds(editionId: number): number[] {
  const snapshot = useSyncExternalStore(
    subscribe,
    () => JSON.stringify(readIds(editionId)),
    () => "[]",
  );
  return JSON.parse(snapshot) as number[];
}

export interface SpineStory {
  id: number;
  rank: number;
  title: string;
}

export function Spine({ editionId, stories }: { editionId: number; stories: SpineStory[] }) {
  const read = new Set(useReadIds(editionId));
  const done = stories.filter((s) => read.has(s.id)).length;
  // A progress display, not navigation: with a dozen stories on a phone the stitches
  // would be far smaller than a fair tap target. The page itself is the way around.
  return (
    <div className="label flex min-w-0 flex-1 items-center gap-3">
      <div
        role="img"
        aria-label={`${done} of ${stories.length} briefings in this edition opened`}
        className="relative flex h-6 min-w-0 flex-1 items-center justify-between before:absolute before:inset-x-0 before:top-1/2 before:h-px before:bg-ink"
      >
        {stories.map((s) => (
          <span
            key={s.id}
            className={`relative block h-3.5 w-[5px] rounded-[1px] border border-ink ${
              read.has(s.id) ? "bg-ink" : "bg-paper"
            }`}
          />
        ))}
      </div>
      <p aria-hidden="true" className="shrink-0 text-[0.75rem] text-ink-2">
        {done} of {stories.length} read
      </p>
    </div>
  );
}

/** "You're caught up" when every story has been opened; otherwise how many are left. */
export function EditionEnd({
  editionId,
  storyIds,
  slot,
}: {
  editionId: number;
  storyIds: number[];
  slot: string;
}) {
  const read = new Set(useReadIds(editionId));
  const left = storyIds.filter((id) => !read.has(id)).length;
  return (
    <section aria-labelledby="edition-end" className="mt-14 border-t-2 border-ink pt-6">
      <h2 id="edition-end" className="text-[1.5rem] font-semibold leading-tight">
        {left === 0 ? "You’re caught up." : "That’s the whole edition."}
      </h2>
      <p className="mt-2 max-w-[34rem] text-ink-2">
        {left === 0
          ? `You have opened every story in the ${slot} edition.`
          : `${left} ${left === 1 ? "story" : "stories"} above ${left === 1 ? "is" : "are"} still unopened.`}{" "}
        There is nothing more to scroll to. New editions come out in the morning, at midday
        and in the evening.
      </p>
    </section>
  );
}

/** Marks a story as read when its page is opened. Renders nothing. */
export function MarkRead({ editionId, storyId }: { editionId: number; storyId: number }) {
  useEffect(() => {
    markRead(editionId, storyId);
  }, [editionId, storyId]);
  return null;
}
