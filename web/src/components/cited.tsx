"use client";

// A sentence Throughline wrote, with its source marks. Tapping the sentence or its
// marks opens the note sheet with the articles it cites (ROADMAP M5 "Receipts").
// The marks button is the keyboard and screen-reader control; the sentence itself
// is an extra, larger pointer target.

import { createContext, useContext, useMemo } from "react";
import { useNotes } from "./notes";
import { citedSources, siglaFor, splitLastWord } from "@/lib/text";
import type { Segment } from "@/lib/text";
import type { Source } from "@/lib/types";

interface StoryScope {
  storyId: number;
  sources: Source[];
  sigla: Record<number, number>;
}

const StoryContext = createContext<StoryScope | null>(null);

/** Holds one story's source list once, so each sentence does not carry its own copy. */
export function StorySources({
  storyId,
  sources,
  children,
}: {
  storyId: number;
  sources: Source[];
  children: React.ReactNode;
}) {
  const scope = useMemo(
    () => ({ storyId, sources, sigla: Object.fromEntries(siglaFor(sources)) }),
    [storyId, sources],
  );
  return <StoryContext.Provider value={scope}>{children}</StoryContext.Provider>;
}

function useStory(): StoryScope {
  const scope = useContext(StoryContext);
  if (!scope) throw new Error("<Cited> must be rendered inside <StorySources>");
  return scope;
}

const MAX_MARKS = 4;

function outletList(sources: Source[]): string {
  const names = [...new Set(sources.map((s) => s.outlet))];
  if (names.length <= 3) return names.join(", ");
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`;
}

export function Cited({
  id,
  text,
  sourceIds,
  segments,
}: {
  /** Unique within the page; marks which sentence is highlighted while the sheet is open. */
  id: string;
  text: string;
  sourceIds: number[];
  /** Pre-split text with glossary terms, when the story has a glossary. */
  segments?: Segment[];
}) {
  const { storyId, sources, sigla } = useStory();
  const { open, activeKey } = useNotes();
  const key = `${storyId}:${id}`;
  const cited = useMemo(() => citedSources(sourceIds, sources), [sourceIds, sources]);
  const numbers = cited.map((s) => sigla[s.article_id]);
  const active = activeKey === key;

  const showSources = () => open(key, { kind: "sources", quote: text, sources: cited, sigla });

  const renderSegment = (seg: Segment, i: number) =>
    seg.kind === "text" ? (
      seg.text
    ) : (
      <button
        key={i}
        type="button"
        className="term"
        aria-haspopup="dialog"
        aria-label={`${seg.text}: show definition`}
        onClick={(e) => {
          e.stopPropagation();
          open(key, {
            kind: "term",
            term: seg.term,
            sources: citedSources(seg.term.source_ids, sources),
            sigla,
          });
        }}
      >
        {seg.text}
      </button>
    );

  // The last word and the source marks are kept together, so the marks never wrap
  // onto a line of their own.
  const parts: Segment[] = segments ?? [{ kind: "text", text }];
  const last = parts[parts.length - 1];
  const [lead, lastWord] = last.kind === "text" ? splitLastWord(last.text) : ["", ""];

  return (
    <span
      className="sentence cursor-pointer"
      data-active={active}
      onClick={() => {
        // Do not hijack a drag that selected text.
        if (window.getSelection()?.toString()) return;
        showSources();
      }}
    >
      {parts.slice(0, -1).map(renderSegment)}
      {lead}
      <span className="whitespace-nowrap">
        {last.kind === "text" ? lastWord : renderSegment(last, parts.length - 1)}
        {cited.length > 0 && (
          <button
            type="button"
            className="marks"
            aria-haspopup="dialog"
            aria-expanded={active}
            aria-label={`Sources for this sentence: ${outletList(cited)}`}
            onClick={(e) => {
              e.stopPropagation();
              showSources();
            }}
          >
            {numbers.slice(0, MAX_MARKS).map((n) => (
              <span key={n}>{n}</span>
            ))}
            {numbers.length > MAX_MARKS && <span>+{numbers.length - MAX_MARKS}</span>}
          </button>
        )}
      </span>
    </span>
  );
}
