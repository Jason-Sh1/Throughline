// Small server-rendered pieces shared by the Today screen and the Story view.

import { Cited } from "./cited";
import { categoryLabel, formatPartialDate } from "@/lib/text";
import type { Segment } from "@/lib/text";
import type { Card, Sentence, TimelineNode } from "@/lib/types";

/**
 * The line under a headline: developing flag, topic, whose headline it is, and the AI
 * label. Each part stays whole when the line wraps, and the separator stays with the
 * part before it, so a line never starts with a dot or ends with half a label.
 */
export function StoryMeta({ card, headlineLabel }: { card: Card; headlineLabel?: string }) {
  const parts = [
    card.confidence.developing ? "Developing" : null,
    categoryLabel(card.category),
    headlineLabel,
    card.ai_label,
  ].filter((p): p is string => Boolean(p));
  return (
    <p className="label text-[0.8125rem] leading-relaxed text-ink-2">
      {parts.map((part, i) => (
        <span key={part} className="whitespace-nowrap">
          {i === 0 && card.confidence.developing ? (
            <span className="font-semibold text-ink">{part}</span>
          ) : (
            part
          )}
          {i < parts.length - 1 && <span aria-hidden="true">{" \u00b7"}</span>}
          {i < parts.length - 1 && <span className="whitespace-normal"> </span>}
        </span>
      ))}
    </p>
  );
}

/**
 * A list of cited sentences. `segments[i]` is sentence i pre-split around glossary
 * terms (see markGlossary in lib/text); omit it where terms are not underlined.
 */
export function SentenceList({
  idPrefix,
  sentences,
  segments,
  ordered = false,
}: {
  idPrefix: string;
  sentences: Sentence[];
  segments?: Segment[][];
  ordered?: boolean;
}) {
  if (sentences.length === 0) return null;
  const List = ordered ? "ol" : "ul";
  return (
    <List className="space-y-3">
      {sentences.map((s, i) => (
        <li key={i} className="max-w-[38rem]">
          <Cited
            id={`${idPrefix}-${i}`}
            text={s.text}
            sourceIds={s.source_ids}
            segments={segments?.[i]}
          />
        </li>
      ))}
    </List>
  );
}

export function Timeline({ nodes, idPrefix }: { nodes: TimelineNode[]; idPrefix: string }) {
  if (nodes.length === 0) return null;
  return (
    <ol className="relative ml-1 border-l border-ink pl-5">
      {nodes.map((n, i) => (
        <li key={i} className="relative pb-5 last:pb-0">
          <span
            aria-hidden="true"
            className="absolute -left-[calc(1.25rem+4px)] top-[0.55em] size-[7px] rounded-full bg-ink"
          />
          <p className="label text-[0.8125rem] font-semibold text-ink-2">
            <time dateTime={n.date}>{formatPartialDate(n.date)}</time>
          </p>
          <p className="max-w-[36rem]">
            <Cited id={`${idPrefix}-${i}`} text={n.event} sourceIds={n.source_ids} />
          </p>
        </li>
      ))}
    </ol>
  );
}
