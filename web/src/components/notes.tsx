"use client";

// The note sheet: the one overlay in the app (CLAUDE.md: never more than one deep).
// It shows either the sources behind one sentence ("receipts") or a glossary term.
// A native <dialog> gives us the focus trap, Escape to close and focus return for free.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { CloseIcon } from "./icons";
import { SourceRow } from "./source-row";
import { plural } from "@/lib/text";
import type { GlossaryTerm, Source } from "@/lib/types";

export type Note =
  | { kind: "sources"; quote: string; sources: Source[]; sigla: Record<number, number> }
  | { kind: "term"; term: GlossaryTerm; sources: Source[]; sigla: Record<number, number> };

interface NotesApi {
  open: (key: string, note: Note) => void;
  activeKey: string | null;
}

const NotesContext = createContext<NotesApi | null>(null);

export function useNotes(): NotesApi {
  const api = useContext(NotesContext);
  if (!api) throw new Error("useNotes must be used inside <NotesProvider>");
  return api;
}

export function NotesProvider({ children }: { children: React.ReactNode }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const titleId = useId();

  const open = useCallback((key: string, next: Note) => {
    setNote(next);
    setActiveKey(key);
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const onClose = () => setActiveKey(null);
    dialog.addEventListener("close", onClose);
    return () => dialog.removeEventListener("close", onClose);
  }, []);

  const api = useMemo(() => ({ open, activeKey }), [open, activeKey]);

  return (
    <NotesContext.Provider value={api}>
      {children}
      <dialog
        ref={dialogRef}
        className="sheet"
        aria-labelledby={titleId}
        onClick={(e) => {
          // A click on the backdrop lands on the dialog element itself.
          if (e.target === e.currentTarget) e.currentTarget.close();
        }}
      >
        {note && (
          <div className="mx-auto max-w-[44rem] px-5 pb-8 pt-4">
            <div className="flex items-start justify-between gap-4">
              <h2 id={titleId} className="label text-[0.8125rem] font-semibold text-ink-2">
                {note.kind === "sources"
                  ? `${plural(note.sources.length, "source")} for this sentence`
                  : "Glossary"}
              </h2>
              <button
                type="button"
                onClick={() => dialogRef.current?.close()}
                className="label -mr-2 -mt-2 inline-flex size-11 shrink-0 items-center justify-center rounded-full text-ink-2 hover:bg-note-wash hover:text-ink"
                aria-label="Close"
              >
                <CloseIcon />
              </button>
            </div>

            {note.kind === "sources" ? (
              <blockquote className="mt-1 text-[1.0625rem] leading-snug">
                <span className="sentence" data-active="true">
                  {note.quote}
                </span>
              </blockquote>
            ) : (
              <div className="mt-1">
                <p className="text-xl font-semibold leading-snug">{note.term.term}</p>
                <p className="mt-2 leading-normal">{note.term.definition}</p>
                {note.sources.length > 0 && (
                  <p className="label mt-5 text-[0.8125rem] font-semibold text-ink-2">
                    Defined from {plural(note.sources.length, "source")}
                  </p>
                )}
              </div>
            )}

            {note.sources.length > 0 ? (
              <ol className="mt-4 border-t border-rule">
                {note.sources.map((s) => (
                  <SourceRow key={s.article_id} source={s} siglum={note.sigla[s.article_id]} />
                ))}
              </ol>
            ) : (
              note.kind === "sources" && (
                <p className="label mt-4 text-sm text-ink-2">
                  The sources for this sentence are not in this edition&apos;s source list.
                </p>
              )
            )}

            <p className="label mt-5 text-[0.75rem] leading-normal text-ink-2">
              Headlines are each outlet&apos;s own words. Throughline&apos;s text is written by AI
              from the articles it cites. Outlet ratings: AllSides.
            </p>
          </div>
        )}
      </dialog>
    </NotesContext.Provider>
  );
}
