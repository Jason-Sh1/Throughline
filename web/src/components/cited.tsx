"use client";

// A sentence Throughline wrote, with its source numbers. Tapping the sentence or its
// numbers opens the note sheet with the articles it cites (ROADMAP M5 "Receipts").
// The numbers are a real button: that is the keyboard and screen-reader control. The
// sentence itself is an extra, larger pointer target.

import { createContext, useContext, useMemo } from "react";
import { useNotes } from "./notes";
import { citedSources, sentenceRuns, siglaFor } from "@/lib/text";
import type { Run, Segment } from "@/lib/text";
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
  /** Unique within the story; marks which sentence is highlighted while its note is open. */
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
  // Kept together on one line: a glossary term with the punctuation after it, and the
  // last word of the sentence with its source numbers.
  const { lead, tail } = useMemo(
    () => sentenceRuns(segments ?? [{ kind: "text", text }]),
    [segments, text],
  );

  const showSources = () => {
    if (cited.length > 0) open(key, { kind: "sources", quote: text, sources: cited, sigla });
  };

  const term = (run: Extract<Run, { kind: "term" }>) => (
    <button
      type="button"
      className="term"
      aria-haspopup="dialog"
      aria-label={`${run.text}: show definition`}
      onClick={(e) => {
        e.stopPropagation();
        open(key, {
          kind: "term",
          term: run.term,
          sources: citedSources(run.term.source_ids, sources),
          sigla,
        });
      }}
    >
      {run.text}
    </button>
  );

  return (
    <span
      className={`sentence ${cited.length > 0 ? "cursor-pointer" : ""}`}
      data-active={active}
      onClick={() => {
        // Do not hijack a drag that selected text.
        if (window.getSelection()?.toString()) return;
        showSources();
      }}
    >
      {lead.map((run, i) =>
        run.kind === "text" ? (
          run.text
        ) : (
          <span key={i} className="whitespace-nowrap">
            {term(run)}
            {run.after}
          </span>
        ),
      )}
      <span className="whitespace-nowrap">
        {tail.kind === "text" ? (
          tail.text
        ) : (
          <>
            {term(tail)}
            {tail.after}
          </>
        )}
        {cited.length > 0 && (
          <button
            type="button"
            className="marks"
            aria-haspopup="dialog"
            // The name starts with the numbers the button shows (WCAG 2.5.3).
            aria-label={`Sources ${numbers.join(", ")}: ${outletList(cited)}`}
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
