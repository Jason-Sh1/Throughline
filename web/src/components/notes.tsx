"use client";

// The note sheet: the one overlay in the app (CLAUDE.md: never more than one deep).
// It shows either the sources behind one sentence ("receipts") or a glossary term.
//
// It is a native <dialog>. On phones and tablets it opens as a modal bottom sheet:
// the browser traps focus, closes on Escape and returns focus to the control that
// opened it. From 72rem up it opens non-modally in the page's right margin, like a
// sidenote: the text stays readable and clickable, so choosing another sentence swaps
// the note instead of making the reader close it first.

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
import { flushSync } from "react-dom";
import { CloseIcon } from "./icons";
import { SourceRow } from "./source-row";
import { plural } from "@/lib/text";
import type { GlossaryTerm, Source } from "@/lib/types";

export type Note =
  | { kind: "sources"; quote: string; sources: Source[]; sigla: Record<number, number> }
  | { kind: "term"; term: GlossaryTerm; sources: Source[]; sigla: Record<number, number> };

interface NotesApi {
  open: (key: string, note: Note) => void;
  /** Key of the sentence whose note is showing, and which kind of note it is. */
  activeKey: string | null;
  activeKind: Note["kind"] | null;
}

const NotesContext = createContext<NotesApi | null>(null);

export function useNotes(): NotesApi {
  const api = useContext(NotesContext);
  if (!api) throw new Error("useNotes must be used inside <NotesProvider>");
  return api;
}

const WIDE = "(min-width: 72rem)"; // keep in step with the media query in globals.css

export function NotesProvider({ children }: { children: React.ReactNode }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const titleId = useId();

  const open = useCallback((key: string, next: Note) => {
    // Render the note first. Opening the dialog moves focus into it and reads out its
    // label, so the content has to be in the page before that happens.
    flushSync(() => {
      setNote(next);
      setActiveKey(key);
    });
    const dialog = dialogRef.current;
    if (!dialog || dialog.open) return;
    if (window.matchMedia(WIDE).matches) dialog.show();
    else dialog.showModal();
  }, []);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const onClose = () => setActiveKey(null);
    // A non-modal dialog does not close on Escape by itself.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && dialog.open && !dialog.matches(":modal")) dialog.close();
    };
    dialog.addEventListener("close", onClose);
    document.addEventListener("keydown", onKey);
    return () => {
      dialog.removeEventListener("close", onClose);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const api = useMemo(
    () => ({ open, activeKey, activeKind: activeKey ? (note?.kind ?? null) : null }),
    [open, activeKey, note],
  );

  return (
    <NotesContext.Provider value={api}>
      {children}
      <dialog
        ref={dialogRef}
        className="sheet"
        aria-labelledby={titleId}
        onClick={(e) => {
          // Close a modal sheet when the backdrop is clicked. A backdrop click targets
          // the dialog element itself, but so does a click on the sheet's own padding,
          // so the pointer position decides.
          const dialog = e.currentTarget;
          if (e.target !== dialog || !dialog.matches(":modal")) return;
          const box = dialog.getBoundingClientRect();
          const inside =
            e.clientX >= box.left && e.clientX <= box.right &&
            e.clientY >= box.top && e.clientY <= box.bottom; // prettier-ignore
          if (!inside) dialog.close();
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

            {note.sources.length > 0 && (
              <ol className="mt-4 border-t border-rule">
                {note.sources.map((s) => (
                  <SourceRow key={s.article_id} source={s} siglum={note.sigla[s.article_id]} />
                ))}
              </ol>
            )}

            {/* Our own explanation, so it is set in our typeface, not the publishers'. */}
            <p className="mt-5 text-[0.875rem] leading-normal text-ink-2">
              The headlines above are each outlet&rsquo;s own words. Throughline&rsquo;s text is
              written by AI from the articles it cites. Outlet ratings are from AllSides.
            </p>
          </div>
        )}
      </dialog>
    </NotesContext.Provider>
  );
}
