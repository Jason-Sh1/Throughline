# Design

The visual system of the Throughline reader, as built in `web/` (M5). Product facts
are in `PRODUCT.md`; this file records how the interface looks and why. The source of
truth for values is `web/src/app/globals.css`.

## The idea: the annotated edition

A news briefing set like a scholarly text with notes. The citations are the interface:
every sentence Throughline writes ends in small source numbers, and tapping the
sentence opens the articles behind it. Nothing looks like a feed of image cards,
because the product is not one.

## Two voices, two typefaces

| Voice | Typeface | Used for |
|---|---|---|
| Throughline's own words | **STIX Two Text** (serif) | headlines we write, every briefing sentence, glossary definitions, end-of-edition text |
| Publishers' words and the interface | **Schibsted Grotesk** (sans, `.label`) | outlet names, outlets' real headlines, ratings, timestamps, section titles, buttons, captions |

A reader can tell who is speaking from the letterforms alone. This is how "never show
AI text under a publisher's name" is made visible. Both faces are self-hosted by
`next/font`.

- Body 17px / 1.55, old-style figures in running text. Story lede 18px.
- Headlines: 32px (edition title), 30px (story), 28px (lead story on Today), 21px
  (other stories). Weight 600, tracking -0.01 to -0.015em, `text-wrap: balance`.
- Labels 12 to 15px, lining figures. Not tabular: this sans widens commas and periods
  under `tnum`.
- Reading measure is capped at 38rem inside a 44rem column.

## Color

Restrained: tinted neutrals plus two colors that each mean one thing.

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `--paper` | `oklch(0.972 0.006 165)` | `oklch(0.19 0.012 170)` | page ground (cool, slightly green) |
| `--paper-2` | `oklch(0.945 0.009 165)` | `oklch(0.235 0.014 170)` | the note sheet |
| `--ink` | `oklch(0.22 0.02 170)` | `oklch(0.93 0.008 165)` | text, rules that matter, plot dots |
| `--ink-2` | `oklch(0.42 0.02 170)` | `oklch(0.74 0.012 165)` | secondary text |
| `--rule` | `oklch(0.85 0.012 165)` | `oklch(0.36 0.014 170)` | hairlines |
| `--note` | `oklch(0.42 0.1 162)` | `oklch(0.81 0.11 160)` | annotation green: anything that cites or links |
| `--note-wash` | `oklch(0.925 0.035 162)` | `oklch(0.3 0.04 162)` | hover ground for those controls |
| `--marker` | `oklch(0.925 0.13 102)` | `oklch(0.44 0.09 100)` | marker yellow: the sentence being checked, and text selection |

- No red and no blue anywhere (brand rule, and they read as party colors).
- Light or dark follows the system setting: the reader is used in daylight on a phone
  and at night in bed.
- Focus rings and the selection color come from the palette.

## Components

- **Masthead and spine.** Wordmark, then a line running to the right with one stitch
  per briefing. A stitch fills when that story has been opened; "2 of 5 read" sits at
  the end. It shows what is left. It is not a streak and it is not navigation.
- **Story entry (Today).** A ruled, numbered list, not cards. Rank numeral in the
  gutter, headline, meta line, brief (all three sentences for the lead, one for the
  rest), a "Full briefing" link. No kicker above headlines: topic and labels sit in
  the meta line below.
- **Cited sentence.** Serif text followed by source numbers in annotation green. The
  last word and the numbers never separate across lines. Active state: a marker-yellow
  swipe across the sentence (200ms, none under reduced motion).
- **Note sheet.** The only overlay. Native `<dialog>`: bottom sheet below 72rem, a
  panel in the right margin from 72rem up, where the page column moves left so the
  panel never covers the text. Holds either a sentence's sources or a glossary term.
- **Source row.** Number, outlet (bold sans), AllSides rating as text, time, "Opinion"
  tag when it applies, then the outlet's real headline as a link out with an arrow.
- **Coverage plot.** One dot per news outlet under its AllSides rating. All dots are
  ink; position and a printed label carry the rating; "Not rated" is set apart with
  hollow dots. Always captioned with the AllSides attribution.
- **Sections.** Native `<details>` with a chevron and a sans title; hairline above.
- **Timeline.** A vertical ink rule with dots; date in the sans, event in the serif.
- **Developing tag.** Small outlined label in ink. Not colored: it is information,
  not an alarm.
- **Icons.** Four authored 16px SVGs, 1.5px stroke (`components/icons.tsx`).

## Layout and motion

- One column, 44rem, 20px side padding, designed at 375px first. From 72rem the
  column sits left of a reserved 26rem margin (sidenote layout).
- Elevation is never used on content. Structure comes from rules: a 2px ink rule
  opens the list and the end of the edition, 1px `--rule` separates entries.
- Motion: the sheet entering and the marker swipe, 200ms ease-out. Nothing animates
  on page load. `prefers-reduced-motion` turns both off.
- Tap targets are at least 44px high for links and buttons; source numbers are small
  to look at but carry an enlarged hit area.

## Rules for new screens

1. If Throughline wrote it, set it in the serif and give it source numbers. If a
   publisher wrote it, set it in the sans and link out.
2. Green means "this cites or links". Yellow means "this is the sentence in question".
   Do not use either for decoration.
3. Never encode a rating, a side or a verdict by color.
4. Lists and rules before cards and shadows.
5. One overlay deep. If a second layer seems needed, the content belongs on the page.
