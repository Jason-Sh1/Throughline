# Product

<!-- impeccable:product-schema 1 -->

> Source note (2026-11-24): written from the repo's own brief (`CLAUDE.md`, `ROADMAP.md`,
> `docs/decisions.md`) without a fresh interview, because the developer asked for the
> visual design to be chosen for him and reviewed afterwards. Everything below restates
> those files; items marked *(inferred)* are reasonable readings, not confirmed answers.

## Platform

web

## Users

- People who want to understand the day's news in a few minutes without living in a
  feed: they open the app once or a few times a day, on a phone first, read the edition
  and stop. *(inferred from the "finite daily editions" and "design at 375px first" rules)*
- They care where a claim comes from and how different outlets cover the same event.

## Product Purpose

Throughline clusters coverage of each news event across many outlets and gives every
story a short, source-cited briefing: a 60-second brief, why it matters, key facts,
background and glossary, a timeline, how outlets frame it, and what happens next.
It is published as finite editions (morning, midday, evening), not an infinite feed.
Success: a reader finishes the edition, understands the main stories, and can check any
sentence against the articles it came from.

## Positioning

Every AI-written sentence carries citations to specific articles, a verification pass
removes sentences its sources do not support, and the coverage behind each story is
shown across the political spectrum using AllSides ratings. The edition ends.

## Operating Context

- A scheduled pipeline (GitHub Actions) ingests publisher RSS feeds, GDELT and the
  Guardian API, clusters articles into events, and writes cards with Claude Haiku via
  the Batch API. The web app reads published editions from Supabase.
- Three depths only: Today screen, Story view, bottom-sheet overlays (never more than
  one overlay deep).
- Non-commercial portfolio project by one student. No ads, no paid tier.

## Capabilities and Constraints

- Today shows 8-12 cards and a "You're caught up" end state. No infinite scroll,
  streaks or dark patterns.
- Card content (see `worker/llm/schemas.py`): headline, brief, why it matters, key
  facts, background, glossary (at most 5 terms), timeline, framing buckets
  (widely reported / reported by some / analysis and opinion), what's next, confidence
  (outlet count, spectrum, developing flag). Long-tail stories get a one-line card.
- Never show AI-generated text under a publisher's name or logo. Outlet headlines shown
  are the outlet's real headline; Throughline's headline is labeled as Throughline's.
- Label all synthesized content "AI-generated from N sources".
- Never display article text. Show our synthesis plus headline, outlet, timestamp and a
  prominent link out.
- Never infer a user's political leaning; the framing panel and the outlets inside a
  story are never personalized.
- At most one visual per story above the fold; no autoplay; no mid-story carousels.
- AllSides ratings are CC BY-NC 4.0 and must be attributed wherever they appear.
  Non-U.S. outlets are "Not rated".
- Undecided: accounts and personalization (M6), push notifications (v2), maps and
  charts from `viz_hint` (v2).

## Brand Commitments

- Name: Throughline.
- Neutral palette; red and blue are not brand colors.
- Plain, attributed language at about an 8th-grade reading level; no judgment
  adjectives; contested claims are attributed ("X said").

## Evidence on Hand

- Real sample edition and five full cards from a live pipeline run on 2026-10-26:
  `docs/samples/`. These were generated before the verification pass existed.
- No logo, illustration or photography assets. No user testimonials or usage numbers;
  none may be invented.
- Eval numbers are not recorded yet (gold set pending human labels).

## Product Principles

1. Receipts over assertion: any sentence can be traced to its sources in one tap.
2. The edition ends. Finishing is the goal, not time spent.
3. Show the coverage, not a verdict: readers see who reported what, without Throughline
   scoring anyone's politics.
4. Our words are labeled as ours; publishers' words stay theirs.
5. Calm by default: no urgency devices, no notification pressure.

## Accessibility & Inclusion

WCAG 2.2 AA: keyboard and screen-reader support for overlays, 4.5:1 contrast, bias never
encoded by color alone, text alternatives for charts, `prefers-reduced-motion` respected.
