// Loads the edition the reader sees.
//
// With NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY set, this reads the
// newest *published* edition through Supabase's REST API. Row-level security only
// exposes published editions and cards to the anon key, so nothing half-built can
// leak. Without those variables (local dev, CI, a fresh clone) it falls back to the
// sample edition in the repo and says so on screen.
//
// Plain fetch instead of supabase-js: two GET requests do not need a client library.
// M6 (accounts) adds supabase-js where auth needs it.

import sample from "@/data/sample-edition.json";
import type { Card, Edition, FullCard, OneLinerCard, Sentence, Source } from "./types";

const REVALIDATE_SECONDS = 300;

interface RawEdition {
  edition_id?: number;
  id?: number;
  date?: string;
  edition_date?: string;
  slot: string;
  cards: unknown[];
}

function isSentence(v: unknown): v is Sentence {
  return (
    typeof v === "object" && v !== null &&
    typeof (v as Sentence).text === "string" &&
    (v as Sentence).text.trim().length > 0
  ); // prettier-ignore
}

function sentences(v: unknown): Sentence[] {
  if (!Array.isArray(v)) return [];
  return v.filter(isSentence).map((s) => ({
    text: s.text,
    source_ids: Array.isArray(s.source_ids) ? s.source_ids : [],
  }));
}

function sources(v: unknown): Source[] {
  if (!Array.isArray(v)) return [];
  return v.filter(
    (s): s is Source =>
      typeof s === "object" && s !== null &&
      typeof (s as Source).article_id === "number" &&
      typeof (s as Source).outlet === "string" &&
      typeof (s as Source).headline === "string" &&
      isHttpUrl((s as Source).url),
  ); // prettier-ignore
}

/** Links out go to publishers; never render a javascript: or data: URL from stored JSON. */
export function isHttpUrl(v: unknown): v is string {
  if (typeof v !== "string") return false;
  try {
    const u = new URL(v);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

/**
 * Turn one stored card into the shape the UI renders, or null if it is unusable
 * (no headline, no sources). The worker validates cards before publishing; this is
 * the reader's own guard, so one malformed row cannot take the page down.
 */
export function normalizeCard(raw: unknown): Card | null {
  if (typeof raw !== "object" || raw === null) return null;
  const c = raw as Record<string, unknown>;
  const src = sources(c.sources);
  if (typeof c.cluster_id !== "number" || src.length === 0) return null;
  const conf = (c.confidence ?? {}) as Record<string, unknown>;
  const base = {
    cluster_id: c.cluster_id,
    category: typeof c.category === "string" ? c.category : null,
    ai_label:
      typeof c.ai_label === "string" ? c.ai_label : `AI-generated from ${src.length} sources`,
    confidence: {
      outlet_count: Number(conf.outlet_count) || 0,
      article_count: Number(conf.article_count) || 0,
      spectrum: (conf.spectrum ?? {}) as Record<string, number>,
      developing: Boolean(conf.developing),
      single_source: Boolean(conf.single_source),
      other_sources: Number(conf.other_sources) || 0,
    },
    sources: src,
    rank: Number(c.rank) || 0,
  };
  if (c.tier === "one_liner") {
    if (!isSentence(c.one_liner)) return null;
    const card: OneLinerCard = { ...base, tier: "one_liner", one_liner: sentences([c.one_liner])[0] };
    return card;
  }
  if (!isSentence(c.headline)) return null;
  const brief = sentences(c.brief);
  if (brief.length === 0) return null;
  const framing = (c.framing ?? {}) as Record<string, unknown>;
  const rep = (framing.representative ?? {}) as Record<string, unknown>;
  const card: FullCard = {
    ...base,
    tier: "full",
    headline: sentences([c.headline])[0],
    headline_label:
      typeof c.headline_label === "string" ? c.headline_label : "Throughline headline",
    brief,
    why_it_matters: sentences(c.why_it_matters),
    key_facts: sentences(c.key_facts),
    background: sentences(c.background),
    glossary: Array.isArray(c.glossary)
      ? (c.glossary as FullCard["glossary"])
          .filter((g) => g && typeof g.term === "string" && typeof g.definition === "string")
          .slice(0, 5)
      : [],
    timeline: Array.isArray(c.timeline)
      ? (c.timeline as FullCard["timeline"]).filter(
          (t) => t && typeof t.date === "string" && typeof t.event === "string",
        )
      : [],
    framing: {
      widely_reported: sentences(framing.widely_reported),
      reported_by_some: sentences(framing.reported_by_some),
      analysis_opinion: sentences(framing.analysis_opinion),
      representative: {
        widely_reported: sources(rep.widely_reported),
        reported_by_some: sources(rep.reported_by_some),
        analysis_opinion: sources(rep.analysis_opinion),
      },
    },
    whats_next: sentences(c.whats_next),
  };
  return card;
}

export function normalizeEdition(raw: RawEdition, isSample: boolean): Edition {
  const slot = raw.slot === "midday" || raw.slot === "evening" ? raw.slot : "morning";
  const cards = raw.cards
    .map(normalizeCard)
    .filter((c): c is Card => c !== null)
    .sort((a, b) => a.rank - b.rank);
  return {
    id: Number(raw.edition_id ?? raw.id) || 0,
    date: String(raw.date ?? raw.edition_date ?? ""),
    slot,
    cards,
    isSample,
  };
}

async function fetchPublished(url: string, key: string): Promise<Edition | null> {
  const headers = { apikey: key, Authorization: `Bearer ${key}` };
  const opts = { headers, next: { revalidate: REVALIDATE_SECONDS } };
  const base = `${url.replace(/\/$/, "")}/rest/v1`;
  const edRes = await fetch(
    `${base}/editions?status=eq.published&select=id,edition_date,slot&order=published_at.desc.nullslast,created_at.desc&limit=1`,
    opts,
  );
  if (!edRes.ok) throw new Error(`editions request failed: ${edRes.status}`);
  const [ed] = (await edRes.json()) as { id: number; edition_date: string; slot: string }[];
  if (!ed) return null;
  const cardRes = await fetch(
    `${base}/edition_cards?edition_id=eq.${ed.id}&select=rank,tier,cards(card)&order=rank.asc`,
    opts,
  );
  if (!cardRes.ok) throw new Error(`edition_cards request failed: ${cardRes.status}`);
  const rows = (await cardRes.json()) as {
    rank: number;
    tier: string;
    cards: { card: Record<string, unknown> } | null;
  }[];
  const cards = rows
    .filter((r) => r.cards) // a card that is not published is hidden by RLS and comes back null
    .map((r) => ({ ...r.cards!.card, rank: r.rank, tier: r.tier }));
  return normalizeEdition({ id: ed.id, edition_date: ed.edition_date, slot: ed.slot, cards }, false);
}

export type EditionResult =
  | { state: "ready"; edition: Edition }
  | { state: "empty" } // connected, but nothing has been published yet
  | { state: "error" }; // the edition could not be loaded

export async function getLatestEdition(): Promise<EditionResult> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) {
    return { state: "ready", edition: normalizeEdition(sample as RawEdition, true) };
  }
  try {
    const edition = await fetchPublished(url, key);
    if (!edition || edition.cards.length === 0) return { state: "empty" };
    return { state: "ready", edition };
  } catch (err) {
    console.error("Could not load the latest edition", err);
    return { state: "error" };
  }
}

export function findStory(edition: Edition, clusterId: number): FullCard | null {
  const card = edition.cards.find((c) => c.cluster_id === clusterId);
  return card && card.tier === "full" ? card : null;
}
