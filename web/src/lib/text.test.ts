import { describe, expect, it } from "vitest";
import {
  citedSources,
  formatEditionDate,
  formatPartialDate,
  formatTimestamp,
  markGlossary,
  normalizeRating,
  sentenceRuns,
  siglaFor,
  spectrumCounts,
  splitGlossary,
  splitLastWord,
} from "./text";
import type { GlossaryTerm, Source } from "./types";

const src = (id: number, outlet = `Outlet ${id}`): Source => ({
  article_id: id,
  outlet,
  headline: `Headline ${id}`,
  url: `https://example.com/${id}`,
  published_at: null,
  allsides_rating: null,
  is_opinion: false,
});

const term = (t: string): GlossaryTerm => ({ term: t, definition: `${t} defined`, source_ids: [] });

describe("spectrumCounts", () => {
  it("returns every rating in display order, zero-filled", () => {
    const out = spectrumCounts({ center: 3, left: 1 });
    expect(out.map((c) => c.rating)).toEqual([
      "left", "lean_left", "center", "lean_right", "right", "not_rated",
    ]); // prettier-ignore
    expect(out.map((c) => c.count)).toEqual([1, 0, 3, 0, 0, 0]);
  });

  it("folds unknown labels into Not rated instead of dropping outlets", () => {
    const out = spectrumCounts({ not_rated: 2, mixed: 4, right: 1 });
    expect(out.find((c) => c.rating === "not_rated")?.count).toBe(6);
    expect(out.reduce((n, c) => n + c.count, 0)).toBe(7);
  });

  it("treats null and unexpected ratings as not rated", () => {
    expect(normalizeRating(null)).toBe("not_rated");
    expect(normalizeRating("far_left")).toBe("not_rated");
    expect(normalizeRating("lean_right")).toBe("lean_right");
  });
});

describe("sigla and cited sources", () => {
  const sources = [src(40), src(7), src(19)];

  it("numbers sources from 1 in story order", () => {
    expect([...siglaFor(sources)]).toEqual([[40, 1], [7, 2], [19, 3]]); // prettier-ignore
  });

  it("returns cited sources in story order and skips ids it cannot resolve", () => {
    expect(citedSources([19, 999, 40], sources).map((s) => s.article_id)).toEqual([40, 19]);
    expect(citedSources([], sources)).toEqual([]);
  });
});

describe("splitGlossary", () => {
  it("marks the first whole-word occurrence, case-insensitively, keeping the original text", () => {
    const out = splitGlossary("The runoff was close. A second runoff is unlikely.", [term("Runoff")]);
    expect(out).toEqual([
      { kind: "text", text: "The " },
      { kind: "term", text: "runoff", term: term("Runoff") },
      { kind: "text", text: " was close. A second runoff is unlikely." },
    ]);
    expect(out.map((s) => s.text).join("")).toBe("The runoff was close. A second runoff is unlikely.");
  });

  it("does not match inside a longer word", () => {
    expect(splitGlossary("Certifications were delayed.", [term("Certification")])).toEqual([
      { kind: "text", text: "Certifications were delayed." },
    ]);
  });

  it("treats regex characters in a term literally", () => {
    const out = splitGlossary("Costs rose under the C.P.I. measure.", [term("C.P.I.")]);
    expect(out.some((s) => s.kind === "term" && s.text === "C.P.I.")).toBe(true);
    expect(splitGlossary("Costs rose under the CxPxIx measure.", [term("C.P.I.")])).toHaveLength(1);
  });

  it("underlines a term once across sentences that share a seen set", () => {
    const seen = new Set<string>();
    const first = splitGlossary("Starshield is a satellite service.", [term("Starshield")], seen);
    const second = splitGlossary("Starshield serves governments.", [term("Starshield")], seen);
    expect(first.filter((s) => s.kind === "term")).toHaveLength(1);
    expect(second).toEqual([{ kind: "text", text: "Starshield serves governments." }]);
  });

  it("keeps the longer hit when two terms overlap", () => {
    const out = splitGlossary("A runoff election follows.", [term("runoff election"), term("election")]);
    expect(out.filter((s) => s.kind === "term").map((s) => s.text)).toEqual(["runoff election"]);
  });

  it("ignores blank terms", () => {
    expect(splitGlossary("Nothing to mark.", [term("  ")])).toEqual([
      { kind: "text", text: "Nothing to mark." },
    ]);
  });
});

describe("markGlossary", () => {
  it("marks a term in the first section that mentions it, in the given order", () => {
    const sections = {
      later: [{ text: "The runoff is next week." }],
      first: [{ text: "No mention here." }, { text: "A runoff was called." }],
    };
    const out = markGlossary(sections, ["first", "later"], [term("runoff")]);
    expect(out.first?.[1].some((s) => s.kind === "term")).toBe(true);
    expect(out.later?.[0].some((s) => s.kind === "term")).toBe(false);
  });

  it("returns nothing to mark when the story has no glossary", () => {
    expect(markGlossary({ a: [{ text: "x" }] }, ["a"], [])).toEqual({});
  });
});

describe("splitLastWord", () => {
  it("splits off the last word and keeps the space with the lead", () => {
    expect(splitLastWord("The Fed held rates.")).toEqual(["The Fed held ", "rates."]);
    expect(splitLastWord("Word")).toEqual(["", "Word"]);
    expect(splitLastWord("Trailing space ")).toEqual(["Trailing ", "space"]);
    expect(splitLastWord("")).toEqual(["", ""]);
  });

  it("loses no text", () => {
    const text = "Musk says SpaceX restricted Ukraine's Starlink connection";
    expect(splitLastWord(text).join("")).toBe(text);
  });
});

describe("dates", () => {
  it("formats full, month and year-only timeline dates without a timezone shift", () => {
    expect(formatPartialDate("2026-10-23")).toBe("23 October 2026");
    expect(formatPartialDate("2026-04")).toBe("April 2026");
    expect(formatPartialDate("2025")).toBe("2025");
    expect(formatPartialDate("2026-01-01")).toBe("1 January 2026");
  });

  it("returns text it cannot parse unchanged", () => {
    expect(formatPartialDate("late October")).toBe("late October");
    expect(formatPartialDate("2026-13")).toBe("2026-13");
  });

  it("formats the edition date with its weekday", () => {
    expect(formatEditionDate("2026-10-26")).toBe("Monday 26 October 2026");
    expect(formatEditionDate("not a date")).toBe("not a date");
    expect(formatEditionDate("2026-13-01")).toBe("2026-13-01");
    expect(formatEditionDate("2026-02-31")).toBe("2026-02-31");
  });

  it("formats publisher timestamps in UTC and tolerates bad input", () => {
    expect(formatTimestamp("2026-10-25T23:46:52+00:00")).toBe("25 Oct, 23:46 UTC");
    expect(formatTimestamp("2026-10-25T20:46:52-04:00")).toBe("26 Oct, 00:46 UTC");
    expect(formatTimestamp(null)).toBeNull();
    expect(formatTimestamp("yesterday")).toBeNull();
  });
});

describe("sentenceRuns", () => {
  const flat = (r: ReturnType<typeof sentenceRuns>) =>
    [...r.lead, r.tail].map((run) => run.text + (run.kind === "term" ? run.after : "")).join("");

  it("splits off the last word so the source numbers can stay with it", () => {
    const r = sentenceRuns([{ kind: "text", text: "The Fed held rates." }]);
    expect(r).toEqual({
      lead: [{ kind: "text", text: "The Fed held " }],
      tail: { kind: "text", text: "rates." },
    });
  });

  it("keeps the punctuation after a glossary term with the term", () => {
    const r = sentenceRuns(splitGlossary("Access to Starshield, and more, was restored.", [term("Starshield")]));
    const t = r.lead.find((run) => run.kind === "term");
    expect(t).toMatchObject({ text: "Starshield", after: "," });
    expect(flat(r)).toBe("Access to Starshield, and more, was restored.");
  });

  it("makes the term the tail when the sentence ends with it", () => {
    const r = sentenceRuns(splitGlossary("Access was restored to Starshield.", [term("Starshield")]));
    expect(r.tail).toMatchObject({ kind: "term", text: "Starshield", after: "." });
    expect(flat(r)).toBe("Access was restored to Starshield.");
  });

  it("loses no text in awkward cases", () => {
    for (const text of ["Runoff", "Runoff.", "A runoff ", "(the runoff) follows", "", "One"]) {
      expect(flat(sentenceRuns(splitGlossary(text, [term("runoff")])))).toBe(text.trimEnd());
    }
  });
});
