import { describe, expect, it } from "vitest";
import sample from "@/data/sample-edition.json";
import { findStory, isHttpUrl, normalizeCard, normalizeEdition } from "./edition";

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
      source_ids: [],
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
