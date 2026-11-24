// Masthead: the wordmark, and the line that runs through it. On the Today screen and
// in a story that line is the spine (one stitch per story); elsewhere it is a plain rule.

import Link from "next/link";
import { Spine } from "./spine";
import type { SpineStory } from "./spine";

export function Masthead({
  editionId,
  stories,
  currentId,
}: {
  editionId?: number;
  stories?: SpineStory[];
  /** The story on screen, marked on the spine. */
  currentId?: number;
}) {
  return (
    <header className="sticky top-0 z-10 border-b border-rule bg-paper">
      <div className="page flex h-14 items-center gap-3">
        <Link
          href="/"
          className="label shrink-0 text-[1.0625rem] font-bold tracking-[-0.02em] text-ink no-underline"
        >
          Throughline
        </Link>
        {editionId !== undefined && stories && stories.length > 0 ? (
          <Spine editionId={editionId} stories={stories} currentId={currentId} />
        ) : (
          <span aria-hidden="true" className="h-px flex-1 bg-ink" />
        )}
      </div>
    </header>
  );
}
