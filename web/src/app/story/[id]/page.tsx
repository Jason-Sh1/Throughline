// Story view: one briefing, in the order of the card schema. The brief is the lede;
// everything after it is a collapsible section (native <details>, so it works without
// JavaScript and is announced correctly by screen readers).

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { StorySources } from "@/components/cited";
import { ArrowLeftIcon, ChevronIcon } from "@/components/icons";
import { Masthead } from "@/components/masthead";
import { NotesProvider } from "@/components/notes";
import { SiteFooter } from "@/components/site-footer";
import { SourceRow } from "@/components/source-row";
import { SpectrumPlot } from "@/components/spectrum-plot";
import { MarkRead } from "@/components/spine";
import { SentenceList, StoryMeta, Timeline } from "@/components/story-bits";
import { findStory, getLatestEdition } from "@/lib/edition";
import { markGlossary, plural, siglaFor } from "@/lib/text";
import type { FramingBucket, FullCard } from "@/lib/types";

type Props = { params: Promise<{ id: string }> };

async function load(idParam: string) {
  const result = await getLatestEdition();
  if (result.state !== "ready" || !/^\d+$/.test(idParam)) return null;
  const story = findStory(result.edition, Number(idParam));
  return story ? { edition: result.edition, story } : null;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const found = await load((await params).id);
  return found ? { title: found.story.headline.text } : { title: "Story not found" };
}

function Section({
  title,
  note,
  open = false,
  children,
}: {
  title: string;
  note?: string;
  open?: boolean;
  children: React.ReactNode;
}) {
  return (
    <details className="section border-t border-rule" open={open}>
      <summary className="label flex min-h-12 items-center gap-2 py-3">
        <ChevronIcon className="chevron size-3.5 shrink-0 text-ink-2" />
        <h2 className="text-[0.9375rem] font-semibold">{title}</h2>
        {note && <span className="text-[0.8125rem] text-ink-2">{note}</span>}
      </summary>
      <div className="pb-7 pl-[1.375rem]">{children}</div>
    </details>
  );
}

const FRAMING: { key: FramingBucket; title: string; blurb: string }[] = [
  {
    key: "widely_reported",
    title: "Widely reported",
    blurb: "Stated by most of the outlets covering the story.",
  },
  {
    key: "reported_by_some",
    title: "Reported by some",
    blurb: "Stated by only a few outlets.",
  },
  {
    key: "analysis_opinion",
    title: "Analysis and opinion",
    blurb: "Interpretation, not reporting. Attributed to whoever said it.",
  },
];

function hasFraming(story: FullCard): boolean {
  return FRAMING.some((b) => story.framing[b.key].length > 0);
}

export default async function StoryPage({ params }: Props) {
  const found = await load((await params).id);
  if (!found) notFound();
  const { edition, story } = found;

  const stories = edition.cards.filter((c): c is FullCard => c.tier === "full");
  const index = stories.findIndex((s) => s.cluster_id === story.cluster_id);
  const next = stories[index + 1];
  const spine = stories.map((s, i) => ({ id: s.cluster_id, rank: i + 1, title: s.headline.text }));
  const sigla = siglaFor(story.sources);
  const g = story.glossary;
  // Each glossary term is underlined once, where it first appears in reading order.
  const marked = markGlossary(
    {
      brief: story.brief,
      why_it_matters: story.why_it_matters,
      key_facts: story.key_facts,
      background: story.background,
      whats_next: story.whats_next,
    },
    ["brief", "why_it_matters", "key_facts", "background", "whats_next"],
    g,
  );

  return (
    <NotesProvider>
      <MarkRead editionId={edition.id} storyId={story.cluster_id} />
      <Masthead editionId={edition.id} stories={spine} />
      <main id="main" className="page pb-24">
        <StorySources storyId={story.cluster_id} sources={story.sources}>
          <article>
            <p className="label pt-5 text-[0.8125rem]">
              <Link
                href={`/#story-${story.cluster_id}`}
                className="inline-flex min-h-11 items-center gap-1.5 text-ink-2 no-underline hover:text-ink"
              >
                <ArrowLeftIcon className="size-3.5" />
                Story {index + 1} of {stories.length}
              </Link>
            </p>
            <h1 className="text-balance text-[1.875rem] font-semibold leading-[1.13] tracking-[-0.015em]">
              {story.headline.text}
            </h1>
            <div className="mt-3">
              <StoryMeta card={story} headlineLabel={story.headline_label} />
            </div>

            <div className="mt-6 text-[1.125rem] leading-[1.5]">
              <SentenceList idPrefix="brief" sentences={story.brief} segments={marked.brief} />
            </div>

            <div className="mb-8 mt-8 max-w-[30rem]">
              <SpectrumPlot confidence={story.confidence} />
            </div>

            {story.why_it_matters.length > 0 && (
              <Section title="Why it matters" open>
                <SentenceList
                  idPrefix="why"
                  sentences={story.why_it_matters}
                  segments={marked.why_it_matters}
                />
              </Section>
            )}

            {story.key_facts.length > 0 && (
              <Section title="Key facts" open>
                <SentenceList
                  idPrefix="facts"
                  sentences={story.key_facts}
                  segments={marked.key_facts}
                />
              </Section>
            )}

            {story.background.length > 0 && (
              <Section title="Background">
                <SentenceList
                  idPrefix="background"
                  sentences={story.background}
                  segments={marked.background}
                />
              </Section>
            )}

            {g.length > 0 && (
              <Section title="Glossary" note={plural(g.length, "term")}>
                <dl className="space-y-4">
                  {g.map((t) => (
                    <div key={t.term} className="max-w-[38rem]">
                      <dt className="font-semibold">{t.term}</dt>
                      <dd>{t.definition}</dd>
                    </div>
                  ))}
                </dl>
              </Section>
            )}

            {story.timeline.length > 0 && (
              <Section title="Timeline" note={plural(story.timeline.length, "date")}>
                <Timeline nodes={story.timeline} idPrefix="timeline" />
              </Section>
            )}

            {hasFraming(story) && (
              <Section title="How outlets frame it">
                <div className="space-y-7">
                  {FRAMING.filter((b) => story.framing[b.key].length > 0).map((b) => {
                    const reps = story.framing.representative?.[b.key] ?? [];
                    return (
                      <section key={b.key}>
                        <h3 className="label text-[0.875rem] font-semibold">{b.title}</h3>
                        <p className="label mb-2 text-[0.8125rem] text-ink-2">{b.blurb}</p>
                        <SentenceList idPrefix={b.key} sentences={story.framing[b.key]} />
                        {reps.length > 0 && (
                          <>
                            <p className="label mt-4 text-[0.8125rem] text-ink-2">
                              Headlines from outlets in this group, in their own words:
                            </p>
                            <ol className="mt-1 border-t border-rule">
                              {reps.map((s) => (
                                <SourceRow
                                  key={s.article_id}
                                  source={s}
                                  siglum={sigla.get(s.article_id)}
                                />
                              ))}
                            </ol>
                          </>
                        )}
                      </section>
                    );
                  })}
                </div>
              </Section>
            )}

            {story.whats_next.length > 0 && (
              <Section title={"What’s next"}>
                <SentenceList
                  idPrefix="next"
                  sentences={story.whats_next}
                  segments={marked.whats_next}
                />
              </Section>
            )}

            <Section title="Sources" note={plural(story.sources.length, "article")} open>
              <p className="label mb-1 max-w-[36rem] text-[0.8125rem] leading-normal text-ink-2">
                The numbers match the small numbers after each sentence. Headlines are the
                outlets&apos; own; follow a link to read the article at its publisher.
                {story.confidence.article_count > story.sources.length &&
                  ` The briefing was written from these ${story.sources.length} of the ${story.confidence.article_count} articles in this story.`}
              </p>
              <ol className="border-t border-rule">
                {story.sources.map((s) => (
                  <SourceRow key={s.article_id} source={s} siglum={sigla.get(s.article_id)} />
                ))}
              </ol>
            </Section>
          </article>
        </StorySources>

        <nav aria-label="More in this edition" className="label mt-10 border-t-2 border-ink pt-5">
          {next ? (
            <Link href={`/story/${next.cluster_id}`} className="group block no-underline">
              <span className="text-[0.8125rem] text-ink-2">
                Next, story {index + 2} of {stories.length}
              </span>
              <span className="mt-1 flex items-start gap-2 font-serif text-[1.25rem] font-semibold leading-snug text-ink group-hover:underline group-hover:decoration-note">
                {next.headline.text}
              </span>
            </Link>
          ) : (
            <p className="font-serif text-[1.25rem] font-semibold leading-snug">
              That was the last briefing in this edition.
            </p>
          )}
          <p className="mt-4 text-[0.875rem]">
            <Link
              href="/"
              className="inline-flex min-h-11 items-center gap-1.5 font-semibold text-note no-underline hover:underline"
            >
              <ArrowLeftIcon className="size-3.5" />
              Back to the edition
            </Link>
          </p>
        </nav>
        <SiteFooter />
      </main>
    </NotesProvider>
  );
}
