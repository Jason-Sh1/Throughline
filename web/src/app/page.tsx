// Today: the whole edition on one page. A numbered list of briefings, a short tail of
// one-line stories, and an end. No infinite scroll (CLAUDE.md, UX rules).

import Link from "next/link";
import { Cited, StorySources } from "@/components/cited";
import { ChevronIcon } from "@/components/icons";
import { Masthead } from "@/components/masthead";
import { NotesProvider } from "@/components/notes";
import { SpectrumPlot } from "@/components/spectrum-plot";
import { EditionEnd } from "@/components/spine";
import { SentenceList, StoryMeta } from "@/components/story-bits";
import { getLatestEdition } from "@/lib/edition";
import { capitalize, categoryLabel, formatEditionDate, plural } from "@/lib/text";
import type { FullCard, OneLinerCard } from "@/lib/types";

export default async function Today() {
  const result = await getLatestEdition();

  if (result.state !== "ready") {
    return (
      <>
        <Masthead />
        <main id="main" className="page py-12">
          <h1 className="text-[2rem] font-semibold leading-tight">
            {result.state === "empty" ? "No edition yet" : "The edition didn’t load"}
          </h1>
          <p className="mt-3 max-w-[34rem] text-ink-2">
            {result.state === "empty"
              ? "Nothing has been published so far. Editions come out in the morning, at midday and in the evening."
              : "Throughline could not reach its edition store. Reload the page in a minute; if it keeps happening, the pipeline may be down."}
          </p>
        </main>
      </>
    );
  }

  const { edition } = result;
  const stories = edition.cards.filter((c): c is FullCard => c.tier === "full");
  const tail = edition.cards.filter((c): c is OneLinerCard => c.tier === "one_liner");
  const spine = stories.map((s, i) => ({ id: s.cluster_id, rank: i + 1, title: s.headline.text }));

  return (
    <NotesProvider>
      <Masthead editionId={edition.id} stories={spine} />
      <main id="main" className="page pb-24">
        <div className="pt-8">
          <h1 className="text-[2rem] font-semibold leading-[1.1] tracking-[-0.015em]">
            {capitalize(edition.slot)} edition
          </h1>
          <p className="label mt-1.5 text-[0.875rem] text-ink-2">
            <time dateTime={edition.date}>{formatEditionDate(edition.date)}</time>
            <span aria-hidden="true"> · </span>
            {plural(stories.length, "briefing")}
            {tail.length > 0 && `, ${plural(tail.length, "short item")}`}
          </p>
          {edition.isSample && (
            <p className="label mt-4 border-l border-ink py-0.5 pl-3 text-[0.8125rem] leading-normal text-ink-2">
              <strong className="font-semibold text-ink">Sample edition.</strong> No live edition
              is connected, so this is saved output from a real pipeline run on{" "}
              {formatEditionDate(edition.date)}. It predates the verification pass.
            </p>
          )}
        </div>

        <ol className="mt-8">
          {stories.map((card, i) => (
            <li
              key={card.cluster_id}
              id={`story-${card.cluster_id}`}
              className="border-t border-rule py-7 first:border-t-2 first:border-ink"
            >
              <StorySources storyId={card.cluster_id} sources={card.sources}>
                <article className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2">
                  <span
                    aria-hidden="true"
                    className={`label pt-1 font-semibold text-ink-2 ${
                      i === 0 ? "text-[1.0625rem] leading-[1.9]" : "text-[0.9375rem] leading-[1.6]"
                    }`}
                  >
                    {i + 1}
                  </span>
                  <div className="min-w-0">
                    <h2
                      className={`text-balance font-semibold tracking-[-0.01em] ${
                        i === 0 ? "text-[1.75rem] leading-[1.15]" : "text-[1.3125rem] leading-[1.22]"
                      }`}
                    >
                      <Link
                        href={`/story/${card.cluster_id}`}
                        className="no-underline hover:underline hover:decoration-note"
                      >
                        <span className="sr-only">Story {i + 1}: </span>
                        {card.headline.text}
                      </Link>
                    </h2>
                    <div className="mt-2">
                      <StoryMeta card={card} />
                    </div>
                    <div className="mt-4">
                      <SentenceList
                        idPrefix="brief"
                        sentences={i === 0 ? card.brief : card.brief.slice(0, 1)}
                      />
                    </div>
                    {i === 0 && (
                      <div className="mt-6 max-w-[30rem]">
                        <SpectrumPlot confidence={card.confidence} />
                      </div>
                    )}
                    <p className="label mt-4 text-[0.875rem]">
                      <Link
                        href={`/story/${card.cluster_id}`}
                        className="inline-flex min-h-11 items-center gap-1 font-semibold text-note no-underline hover:underline"
                      >
                        Full briefing
                        <span className="sr-only">: {card.headline.text}</span>
                        <ChevronIcon className="size-3.5" />
                      </Link>
                      {i > 0 && (
                        <span className="ml-3 text-ink-2">
                          {plural(card.confidence.outlet_count, "outlet")}
                        </span>
                      )}
                    </p>
                  </div>
                </article>
              </StorySources>
            </li>
          ))}
        </ol>

        {tail.length > 0 && (
          <section aria-labelledby="also" className="border-t-2 border-ink pt-6">
            <h2 id="also" className="label text-[0.9375rem] font-semibold">
              Also in this edition
            </h2>
            <ul className="mt-2">
              {tail.map((card) => (
                <li key={card.cluster_id} className="border-b border-rule py-4 last:border-b-0">
                  <StorySources storyId={card.cluster_id} sources={card.sources}>
                    <p className="max-w-[38rem]">
                      <Cited
                        id="one-liner"
                        text={card.one_liner.text}
                        sourceIds={card.one_liner.source_ids}
                      />
                    </p>
                    <p className="label mt-1.5 text-[0.8125rem] text-ink-2">
                      {[
                        categoryLabel(card.category),
                        card.confidence.developing ? "Developing" : null,
                        plural(card.confidence.outlet_count, "outlet"),
                        card.ai_label,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </StorySources>
                </li>
              ))}
            </ul>
          </section>
        )}

        <EditionEnd
          editionId={edition.id}
          storyIds={stories.map((s) => s.cluster_id)}
          slot={edition.slot}
        />

        <footer className="label mt-12 max-w-[36rem] space-y-2 text-[0.8125rem] leading-normal text-ink-2">
          <p>
            Throughline groups coverage of the same event across outlets and writes a short
            briefing with AI. Every sentence cites the articles it came from: tap a sentence, or
            the small numbers after it, to see them.
          </p>
          <p>
            Outlet ratings are from{" "}
            <a
              href="https://www.allsides.com/media-bias/ratings"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-ink"
            >
              AllSides Media Bias Ratings
            </a>{" "}
            (CC BY-NC 4.0). A non-commercial student project: no ads, no paid tier.
          </p>
        </footer>
      </main>
    </NotesProvider>
  );
}
