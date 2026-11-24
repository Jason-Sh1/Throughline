import { afterEach, describe, expect, it, vi } from "vitest";
import sample from "@/data/sample-edition.json";
import {
  MAX_BRIEFINGS,
  MAX_SHORT_ITEMS,
  findStory,
  getLatestEdition,
  getStory,
  isHttpUrl,
  normalizeCard,
  normalizeEdition,
} from "./edition";

const source = {
  article_id: 1,
  outlet: "The Example",
  headline: "A real headline",
  url: "https://example.com/a",
  published_at: "2026-10-25T23:46:52+00:00",
  allsides_rating: "center",
  is_opinion: false,
};

const full = {
  tier: "full",
  rank: 1,
  cluster_id: 10,
  category: "world",
  ai_label: "AI-generated from 1 source",
  headline: { text: "Something happened", source_ids: [1] },
  brief: [{ text: "It happened today.", source_ids: [1] }],
  sources: [source],
  confidence: { outlet_count: 1, article_count: 1, spectrum: { center: 1 }, developing: false },
};

describe("normalizeCard", () => {
  it("fills optional sections with empty lists", () => {
    const card = normalizeCard(full);
    expect(card?.tier).toBe("full");
    if (card?.tier !== "full") throw new Error("expected a full card");
    expect(card.why_it_matters).toEqual([]);
    expect(card.timeline).toEqual([]);
    expect(card.framing.widely_reported).toEqual([]);
    expect(card.headline_label).toBe("Throughline headline");
  });

  it("rejects a card with no usable sources, headline or brief", () => {
    expect(normalizeCard({ ...full, sources: [] })).toBeNull();
    expect(normalizeCard({ ...full, headline: { text: "  ", source_ids: [] } })).toBeNull();
    expect(normalizeCard({ ...full, brief: [] })).toBeNull();
    expect(normalizeCard(null)).toBeNull();
  });

  it("drops sources whose link is not http(s), so stored JSON cannot inject a script URL", () => {
    const bad = { ...source, article_id: 2, url: "javascript:alert(1)" };
    const card = normalizeCard({ ...full, sources: [source, bad] });
    expect(card?.sources.map((s) => s.article_id)).toEqual([1]);
    expect(isHttpUrl("data:text/html,x")).toBe(false);
    expect(isHttpUrl("https://example.com")).toBe(true);
  });

  it("drops blank sentences and caps the glossary at five terms", () => {
    const glossary = Array.from({ length: 7 }, (_, i) => ({
      term: `T${i}`,
      definition: "d",
      source_ids: [1],
    }));
    const card = normalizeCard({
      ...full,
      glossary,
      key_facts: [{ text: "", source_ids: [1] }, { text: "A fact.", source_ids: [1] }],
    });
    if (card?.tier !== "full") throw new Error("expected a full card");
    expect(card.glossary).toHaveLength(5);
    expect(card.key_facts.map((s) => s.text)).toEqual(["A fact."]);
  });

  it("accepts a one-line card and rejects one without its sentence", () => {
    const one = { ...full, tier: "one_liner", one_liner: { text: "Short.", source_ids: [1] } };
    expect(normalizeCard(one)?.tier).toBe("one_liner");
    expect(normalizeCard({ ...one, one_liner: undefined })).toBeNull();
  });
});

describe("normalizeEdition", () => {
  it("sorts cards by rank and skips unusable ones", () => {
    const ed = normalizeEdition(
      {
        id: 3,
        edition_date: "2026-10-26",
        slot: "evening",
        cards: [{ ...full, rank: 2, cluster_id: 11 }, { nonsense: true }, { ...full, rank: 1 }],
      },
      false,
    );
    expect(ed.cards.map((c) => c.cluster_id)).toEqual([10, 11]);
    expect(ed).toMatchObject({ id: 3, date: "2026-10-26", slot: "evening", isSample: false });
  });

  it("loads the bundled sample edition: every card survives and has sources", () => {
    const ed = normalizeEdition(sample as Parameters<typeof normalizeEdition>[0], true);
    expect(ed.cards).toHaveLength(sample.cards.length);
    expect(ed.cards.every((c) => c.sources.length > 0)).toBe(true);
    expect(ed.isSample).toBe(true);
    const lead = ed.cards[0];
    expect(findStory(ed, lead.cluster_id)?.headline.text).toBeTruthy();
  });

  it("findStory only returns full briefings", () => {
    const ed = normalizeEdition(sample as Parameters<typeof normalizeEdition>[0], true);
    const oneLiner = ed.cards.find((c) => c.tier === "one_liner");
    expect(oneLiner).toBeDefined();
    expect(findStory(ed, oneLiner!.cluster_id)).toBeNull();
    expect(findStory(ed, -1)).toBeNull();
  });
});

describe("the reader never shows uncited text", () => {
  it("drops sentences, glossary terms and timeline entries with no known source", () => {
    const card = normalizeCard({
      ...full,
      brief: [
        { text: "Cited.", source_ids: [1] },
        { text: "No sources.", source_ids: [] },
        { text: "Unknown source.", source_ids: [999] },
        { text: "Bad ids.", source_ids: "1" },
      ],
      glossary: [
        { term: "Kept", definition: "d", source_ids: [1] },
        { term: "Dropped", definition: "d" },
      ],
      timeline: [
        { date: "2026-10-23", event: "Kept", source_ids: [1] },
        { date: "2026-10-24", event: "Dropped", source_ids: [] },
      ],
    });
    if (card?.tier !== "full") throw new Error("expected a full card");
    expect(card.brief.map((s) => s.text)).toEqual(["Cited."]);
    expect(card.glossary.map((g) => g.term)).toEqual(["Kept"]);
    expect(card.timeline.map((t) => t.event)).toEqual(["Kept"]);
  });

  it("rejects a card whose headline cites nothing", () => {
    expect(normalizeCard({ ...full, headline: { text: "Something", source_ids: [] } })).toBeNull();
  });

  it("works out the tier when the stored card has none", () => {
    const noTier: Record<string, unknown> = { ...full };
    delete noTier.tier;
    expect(normalizeCard(noTier)?.tier).toBe("full");
    const short: Record<string, unknown> = { ...noTier, one_liner: { text: "Short.", source_ids: [1] } };
    delete short.headline;
    delete short.brief;
    expect(normalizeCard(short)?.tier).toBe("one_liner");
  });
});

describe("edition size", () => {
  it("shows at most 12 briefings and 8 short items, in rank order", () => {
    const briefings = Array.from({ length: 50 }, (_, i) => ({
      ...full,
      rank: i + 1,
      cluster_id: 100 + i,
    }));
    const short = Array.from({ length: 30 }, (_, i) => ({
      tier: "one_liner",
      rank: 51 + i,
      cluster_id: 500 + i,
      category: "us",
      ai_label: "AI-generated from 1 source",
      one_liner: { text: "Short.", source_ids: [1] },
      sources: [source],
      confidence: full.confidence,
    }));
    const ed = normalizeEdition(
      { id: 1, edition_date: "2026-10-26", slot: "morning", cards: [...short, ...briefings] },
      false,
    );
    const fullCards = ed.cards.filter((c) => c.tier === "full");
    expect(fullCards).toHaveLength(MAX_BRIEFINGS);
    expect(fullCards.map((c) => c.rank)).toEqual(Array.from({ length: 12 }, (_, i) => i + 1));
    expect(ed.cards.filter((c) => c.tier === "one_liner")).toHaveLength(MAX_SHORT_ITEMS);
  });
});

describe("loading from Supabase", () => {
  const env = { ...process.env };
  afterEach(() => {
    process.env = { ...env };
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function configure(responses: Record<string, unknown>) {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://db.example/";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon";
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      const key = Object.keys(responses).find((k) => url.includes(k));
      const body = key ? responses[key] : null;
      if (body === "fail") return { ok: false, status: 503, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => body };
    });
    return calls;
  }

  it("falls back to the labelled sample when Supabase is not configured", async () => {
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    const result = await getLatestEdition();
    expect(result.state === "ready" && result.edition.isSample).toBe(true);
  });

  it("asks only for published editions and skips cards hidden by row-level security", async () => {
    const calls = configure({
      "/editions?": [{ id: 7, edition_date: "2026-11-24", slot: "midday" }],
      "/edition_cards?": [
        { rank: 1, tier: "full", cards: { card: full } },
        { rank: 2, tier: "full", cards: null },
      ],
    });
    const result = await getLatestEdition();
    if (result.state !== "ready") throw new Error("expected an edition");
    expect(result.edition).toMatchObject({ id: 7, slot: "midday", isSample: false });
    expect(result.edition.cards).toHaveLength(1);
    expect(calls[0]).toContain("https://db.example/rest/v1/editions?status=eq.published");
    expect(calls[1]).toContain("edition_id=eq.7");
  });

  it("reports an empty store and a failed request as different states", async () => {
    configure({ "/editions?": [] });
    expect((await getLatestEdition()).state).toBe("empty");
    vi.spyOn(console, "error").mockImplementation(() => {});
    configure({ "/editions?": "fail" });
    expect((await getLatestEdition()).state).toBe("error");
  });

  it("finds a story that has left the current edition, and tells missing from failed", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const current = { ...full, cluster_id: 10 };
    const edition = {
      "/editions?": [{ id: 7, edition_date: "2026-11-24", slot: "midday" }],
      "/edition_cards?": [{ rank: 1, tier: "full", cards: { card: current } }],
    };
    // In the current edition: comes with its edition.
    configure({ ...edition });
    const inEdition = await getStory(10);
    expect(inEdition.state === "ready" && inEdition.edition?.id).toBe(7);
    // Moved out: loaded on its own, by cluster id, published cards only.
    const calls = configure({ ...edition, "/cards?": [{ card: { ...full, cluster_id: 55 } }] });
    const earlier = await getStory(55);
    expect(earlier.state === "ready" && earlier.edition).toBeNull();
    expect(calls.at(-1)).toContain("cluster_id=eq.55&status=eq.published");
    // Never published: missing. Store down: error, not "missing".
    configure({ ...edition, "/cards?": [] });
    expect((await getStory(56)).state).toBe("missing");
    configure({ ...edition, "/cards?": "fail" });
    expect((await getStory(56)).state).toBe("error");
    configure({ "/editions?": "fail" });
    expect((await getStory(10)).state).toBe("error");
  });
});
