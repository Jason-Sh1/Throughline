// One publisher article: outlet, their real headline, when it ran, and a link out.
// Publishers' words are always set in the grotesque (.label), never the serif that
// carries Throughline's own text, so the two voices are never confused.

import { ExternalIcon } from "./icons";
import { RATING_LABEL, formatTimestamp, normalizeRating, splitLastWord } from "@/lib/text";
import type { Source } from "@/lib/types";

export function SourceRow({ source, siglum }: { source: Source; siglum?: number }) {
  const when = formatTimestamp(source.published_at);
  const rating = RATING_LABEL[normalizeRating(source.allsides_rating)];
  const [lead, lastWord] = splitLastWord(source.headline);
  return (
    <li className="label flex gap-3 border-b border-rule py-3">
      <span className="w-5 shrink-0 pt-0.5 text-right text-[0.8125rem] font-semibold text-note">
        {siglum ?? ""}
      </span>
      <div className="min-w-0">
        <p className="text-[0.8125rem] text-ink-2">
          <span className="font-semibold text-ink">{source.outlet}</span>
          <span aria-hidden="true"> · </span>
          <span>
            <span className="sr-only">AllSides rating: </span>
            {rating}
          </span>
          {source.is_opinion && (
            <>
              <span aria-hidden="true"> · </span>
              <span>Opinion</span>
            </>
          )}
          {when && (
            <>
              <span aria-hidden="true"> · </span>
              <time dateTime={source.published_at ?? undefined}>{when}</time>
            </>
          )}
        </p>
        <a
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-0.5 inline-block text-[0.9375rem] font-medium leading-snug text-ink underline decoration-rule hover:decoration-note"
        >
          {lead}
          <span className="whitespace-nowrap">
            {lastWord}
            <ExternalIcon className="ml-1 inline-block size-3.5 align-[-0.1em] text-note" />
          </span>
          <span className="sr-only"> (opens {source.outlet} in a new tab)</span>
        </a>
      </div>
    </li>
  );
}
