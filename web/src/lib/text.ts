// Pure helpers for the reader UI. No React, no I/O, so they are unit-tested directly.

import type { GlossaryTerm, Rating, Source } from "./types";

/** Order used wherever outlets are grouped by AllSides rating. */
export const RATINGS: Rating[] = [
  "left",
  "lean_left",
  "center",
  "lean_right",
  "right",
  "not_rated",
];

export const RATING_LABEL: Record<Rating, string> = {
  left: "Left",
  lean_left: "Lean left",
  center: "Center",
  lean_right: "Lean right",
  right: "Right",
  not_rated: "Not rated",
};

/** Anything we do not recognise (null, a new label) is shown as "Not rated". */
export function normalizeRating(value: string | null | undefined): Rating {
  return RATINGS.includes(value as Rating) ? (value as Rating) : "not_rated";
}

/** Outlet counts per rating, in display order, with unknown keys folded into Not rated. */
export function spectrumCounts(
  spectrum: Record<string, number>,
): { rating: Rating; count: number }[] {
  const totals = new Map<Rating, number>(RATINGS.map((r) => [r, 0]));
  for (const [key, n] of Object.entries(spectrum)) {
    const rating = normalizeRating(key);
    totals.set(rating, (totals.get(rating) ?? 0) + (Number.isFinite(n) ? n : 0));
  }
  return RATINGS.map((rating) => ({ rating, count: totals.get(rating) ?? 0 }));
}

/**
 * Source numbers ("sigla") for a story: source 1 is the first article in the
 * card's source list, and so on. Sentences cite articles by these numbers.
 */
export function siglaFor(sources: Source[]): Map<number, number> {
  return new Map(sources.map((s, i) => [s.article_id, i + 1]));
}

/** The sources a sentence cites, in story order. Ids we cannot resolve are skipped. */
export function citedSources(sourceIds: number[], sources: Source[]): Source[] {
  const wanted = new Set(sourceIds);
  return sources.filter((s) => wanted.has(s.article_id));
}

export type Segment =
  | { kind: "text"; text: string }
  | { kind: "term"; text: string; term: GlossaryTerm };

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Split a sentence around the first whole-word occurrence of each glossary term
 * that has not been marked yet. `seen` carries the terms already underlined
 * earlier on the page, so each term is underlined once per story, not every time.
 */
export function splitGlossary(
  text: string,
  glossary: GlossaryTerm[],
  seen: Set<string> = new Set(),
): Segment[] {
  const hits: { start: number; end: number; term: GlossaryTerm }[] = [];
  // Longest first, so "runoff election" wins over "election" where they overlap.
  const byLength = [...glossary].sort((a, b) => b.term.length - a.term.length);
  for (const term of byLength) {
    const key = term.term.toLowerCase();
    if (!term.term.trim() || seen.has(key)) continue;
    const re = new RegExp(
      `(?<![\\p{L}\\p{N}])${escapeRegExp(term.term)}(?![\\p{L}\\p{N}])`,
      "iu",
    );
    const m = re.exec(text);
    if (!m) continue;
    const start = m.index;
    const end = start + m[0].length;
    if (hits.some((h) => start < h.end && end > h.start)) continue; // overlaps a longer hit
    hits.push({ start, end, term });
    seen.add(key);
  }
  if (hits.length === 0) return [{ kind: "text", text }];
  hits.sort((a, b) => a.start - b.start);
  const out: Segment[] = [];
  let at = 0;
  for (const h of hits) {
    if (h.start > at) out.push({ kind: "text", text: text.slice(at, h.start) });
    out.push({ kind: "term", text: text.slice(h.start, h.end), term: h.term });
    at = h.end;
  }
  if (at < text.length) out.push({ kind: "text", text: text.slice(at) });
  return out;
}

/**
 * Pre-split a story's sections around its glossary terms, in reading order, so each
 * term is underlined once: where the reader first meets it. Returns one Segment[][]
 * per section, or undefined entries when the story has no glossary.
 */
export function markGlossary<K extends string>(
  sections: Record<K, { text: string }[]>,
  order: K[],
  glossary: GlossaryTerm[],
): Partial<Record<K, Segment[][]>> {
  if (glossary.length === 0) return {};
  const seen = new Set<string>();
  const out: Partial<Record<K, Segment[][]>> = {};
  for (const key of order) {
    out[key] = sections[key].map((s) => splitGlossary(s.text, glossary, seen));
  }
  return out;
}

/**
 * Split off the last word of a sentence, so the UI can keep it on the same line as
 * whatever follows it (source marks, a link icon). Returns [everything before, last word].
 */
export function splitLastWord(text: string): [string, string] {
  const trimmed = text.trimEnd();
  const at = trimmed.lastIndexOf(" ");
  return at === -1 ? ["", trimmed] : [trimmed.slice(0, at + 1), trimmed.slice(at + 1)];
}

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
]; // prettier-ignore

/**
 * Timeline dates arrive as "2026-10-23", "2026-04" or "2025". Format without
 * going through Date, so a date never shifts across a timezone boundary.
 */
export function formatPartialDate(value: string): string {
  const m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$/.exec(value.trim());
  if (!m) return value;
  const [, y, mo, d] = m;
  const month = mo ? MONTHS[Number(mo) - 1] : undefined;
  if (mo && !month) return value;
  if (d) return `${Number(d)} ${month} ${y}`;
  if (month) return `${month} ${y}`;
  return y;
}

const WEEKDAYS = [
  "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
]; // prettier-ignore

/** "Monday 26 October 2026" for an edition date like "2026-10-26". */
export function formatEditionDate(isoDate: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!m) return isoDate;
  const [, y, mo, d] = m.map(Number) as unknown as [number, number, number, number];
  const weekday = WEEKDAYS[new Date(Date.UTC(y, mo - 1, d)).getUTCDay()];
  return `${weekday} ${d} ${MONTHS[mo - 1]} ${y}`;
}

/** "25 Oct, 23:46 UTC". Publisher timestamps are shown in UTC so server and browser agree. */
export function formatTimestamp(iso: string | null): string | null {
  if (!iso) return null;
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return null;
  const day = t.getUTCDate();
  const month = MONTHS[t.getUTCMonth()].slice(0, 3);
  const hh = String(t.getUTCHours()).padStart(2, "0");
  const mm = String(t.getUTCMinutes()).padStart(2, "0");
  return `${day} ${month}, ${hh}:${mm} UTC`;
}

export function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

/** Category slugs from the worker ("us", "world", "politics") as reader-facing labels. */
export function categoryLabel(category: string | null): string | null {
  if (!category) return null;
  if (category.toLowerCase() === "us") return "U.S.";
  return capitalize(category.replace(/_/g, " "));
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
