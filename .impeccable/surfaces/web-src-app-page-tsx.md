---
version: 1
slug: "web-src-app-page-tsx"
primary_target: "web/src/app/page.tsx"
related_targets: ["web/src/app/story/[id]/page.tsx"]
---

# Reader: Today screen and Story view

Scope: `web/src/app/page.tsx` (Today), `web/src/app/story/[id]/page.tsx` (Story view) and
the receipts / glossary sheet they share. Visitor mode: **Read**.

Audience and job: someone catching up on the day's news on a phone, in daylight, in a
few minutes. They read the edition top to bottom, open the stories they care about,
check a sentence's sources when a claim surprises them, and stop at the end.

Content: the edition JSON the worker exports (headline, brief, why it matters, key
facts, background, glossary, timeline, framing, what's next, sources, confidence).
Constraints: the non-negotiable rules in `CLAUDE.md` (labels, no article text, no
publisher branding on our words, bias never by color alone, one overlay deep).

Decision record: chosen unattended on 2026-11-24. The developer delegated the visual
design ("the thread picks it, I review afterwards"), so no decision page was shown; the
rolled direction was taken as assigned. Challengers weighed and declined: cutting-bench
rail, hand-drawn zine, green lobes, raked timetable, tensegrity (each loses on product
clarity for a news reader); exposed binding declined but raised the direction (below).

## Direction contract

THESIS: The annotated edition. A news briefing set like a scholarly critical text, where
the citation apparatus is the interface: every sentence ends in source marks and the
"footnote" opens in place. It refuses the news-app arrangement of image-topped cards in
an endless feed.

OWN-WORLD: Cool paper ground, green-black ink, one annotation green for anything that
cites or links, one marker yellow that only ever means "the sentence you are checking".
Throughline's words are set in a scholarly serif; publishers' words (real headlines,
outlet names) are always set in the grotesque, so the two voices never share a face.
Hairline rules, superscript sigla, tabular numerals. No cards, no shadows on content.

STORY: The reader sees a finite numbered edition, reads each brief, trusts it because
the sources are one tap away, and finishes.

FIRST VIEWPORT: Masthead with the wordmark and the edition line (date, slot). Under it
the spine: one stitch per story, filled as stories are read. Then story 1 at full
measure: headline, its three-sentence brief with source marks, the coverage dot plot,
and the AI label. Story 2's headline is visible at the fold.

FORM: Scholarly apparatus (critical edition with sigla and notes), candidate 3 of 7 on
the grounded list; seed:a71bf67d377e8eac. Raises: from the exposed binding, the visible
count of what remains (the spine); from the timetable, rank carried by weight, case and
rule inside a narrow type scale. Signature interaction: tapping a sentence swipes it
with marker yellow and opens its sources as a note (bottom sheet on phones, margin
panel on wide screens). Motion grammar: the sheet and the marker swipe only, 200 ms
ease-out, none under reduced motion.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
