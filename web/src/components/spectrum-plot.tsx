// Coverage plot: one dot per news outlet, placed under its AllSides rating.
//
// Bias is never encoded by color (CLAUDE.md, WCAG): every dot is the same ink.
// Position and a text label carry the rating, counts are printed as numbers, and
// "Not rated" is a separate group with hollow dots. No red or blue anywhere.

import { RATING_LABEL, plural, spectrumCounts } from "@/lib/text";
import type { Confidence } from "@/lib/types";

const MAX_DOTS = 12;

export function SpectrumPlot({ confidence }: { confidence: Confidence }) {
  const counts = spectrumCounts(confidence.spectrum);
  const total = counts.reduce((n, c) => n + c.count, 0);
  if (total === 0) return null;
  const summary = counts.map((c) => `${RATING_LABEL[c.rating]} ${c.count}`).join(", ");

  return (
    <figure className="label">
      <div
        role="img"
        aria-label={`News outlets covering this story, by AllSides rating: ${summary}.`}
        className="grid grid-cols-6 border-b border-rule"
      >
        {counts.map(({ rating, count }) => (
          <div
            key={rating}
            className={`flex min-h-9 flex-wrap content-end justify-center gap-[3px] px-1 pb-1.5 ${
              rating === "not_rated" ? "border-l border-dashed border-rule" : ""
            }`}
          >
            {Array.from({ length: Math.min(count, MAX_DOTS) }, (_, i) => (
              <span
                key={i}
                className={`size-[7px] rounded-full ${
                  rating === "not_rated" ? "border border-ink-2" : "bg-ink"
                }`}
              />
            ))}
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="grid grid-cols-6 pt-1 text-center text-[0.6875rem] leading-tight text-ink-2">
        {counts.map(({ rating, count }) => (
          <div key={rating} className="px-0.5">
            <span className="block font-semibold text-ink">{count}</span>
            {RATING_LABEL[rating]}
          </div>
        ))}
      </div>
      <figcaption className="mt-2 text-[0.75rem] text-ink-2">
        {plural(confidence.outlet_count || total, "news outlet")} covered this.
        {confidence.single_source ? " Single source so far." : ""} Ratings by{" "}
        <a
          href="https://www.allsides.com/media-bias/ratings"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-ink"
        >
          AllSides
        </a>{" "}
        (CC BY-NC 4.0); non-U.S. outlets are not rated.
      </figcaption>
    </figure>
  );
}
