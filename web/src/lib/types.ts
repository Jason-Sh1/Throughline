// Shapes of the edition JSON the worker writes (worker/llm/schemas.py, StoryCard).
// Every AI-written sentence carries the ids of the articles it cites.

export type Rating =
  | "left"
  | "lean_left"
  | "center"
  | "lean_right"
  | "right"
  | "not_rated";

export interface Sentence {
  text: string;
  source_ids: number[];
}

export interface GlossaryTerm {
  term: string;
  definition: string;
  source_ids: number[];
}

export interface TimelineNode {
  date: string; // "2026-10-23", "2026-04" or "2025"
  event: string;
  source_ids: number[];
}

/** A publisher's article: their real headline, never our text (CLAUDE.md rule 3). */
export interface Source {
  article_id: number;
  outlet: string;
  headline: string;
  url: string;
  published_at: string | null;
  allsides_rating: string | null;
  is_opinion: boolean;
}

export interface Confidence {
  outlet_count: number;
  article_count: number;
  spectrum: Record<string, number>;
  developing: boolean;
  spectrum_spread?: number;
  single_source?: boolean;
  other_sources?: number;
}

export type FramingBucket =
  | "widely_reported"
  | "reported_by_some"
  | "analysis_opinion";

export interface Framing {
  widely_reported: Sentence[];
  reported_by_some: Sentence[];
  analysis_opinion: Sentence[];
  representative?: Partial<Record<FramingBucket, Source[]>>;
}

interface CardBase {
  cluster_id: number;
  category: string | null;
  ai_label: string;
  confidence: Confidence;
  sources: Source[];
  rank: number;
}

export interface FullCard extends CardBase {
  tier: "full";
  headline: Sentence;
  headline_label: string;
  brief: Sentence[];
  why_it_matters: Sentence[];
  key_facts: Sentence[];
  background: Sentence[];
  glossary: GlossaryTerm[];
  timeline: TimelineNode[];
  framing: Framing;
  whats_next: Sentence[];
}

export interface OneLinerCard extends CardBase {
  tier: "one_liner";
  one_liner: Sentence;
}

export type Card = FullCard | OneLinerCard;

export interface Edition {
  id: number;
  date: string; // ISO date
  slot: "morning" | "midday" | "evening";
  cards: Card[];
  /** True when the app is showing the bundled sample instead of a live edition. */
  isSample: boolean;
}
