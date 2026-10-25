"""Citation filter: the code-side guarantee behind CLAUDE.md rule 1.

Every cited item (sentence, claim, glossary term, timeline node, chart point)
keeps only the source ids that are really in the source list; an item left with
no valid id is dropped. The model can't publish an uncited sentence, and it
can't launder one by citing an id that doesn't exist.

Whether the cited source actually *supports* the sentence is the verification
pass's job (M4); this filter only checks that the citation points somewhere real.
"""

from __future__ import annotations

from collections import Counter

from llm.schemas import CardDraft, Extraction, Sentence


class CardRejected(Exception):
    """The card lost something it can't do without (headline or all bullets)."""


def _keep(item, valid: set[str], stats: Counter) -> bool:
    ids = list(dict.fromkeys(i.strip() for i in item.source_ids))  # dedupe, keep order
    good = [i for i in ids if i in valid]
    stats["invalid_ids"] += len(ids) - len(good)
    item.source_ids = good
    has_text = all(v.strip() for k, v in item if isinstance(v, str) and k != "attributed_to")
    if not good or not has_text:
        stats["dropped"] += 1
        return False
    stats["kept"] += 1
    return True


def filter_items[T](items: list[T], valid: set[str], stats: Counter) -> list[T]:
    return [it for it in items if _keep(it, valid, stats)]


def filter_ids(ids: list[str], valid: set[str], stats: Counter) -> list[str]:
    good = [i for i in dict.fromkeys(ids) if i in valid]
    stats["invalid_ids"] += len(set(ids)) - len(good)
    return good


def filter_card(draft: CardDraft, valid: set[str]) -> tuple[CardDraft, Counter]:
    stats: Counter = Counter()
    if not _keep(draft.headline, valid, stats):
        raise CardRejected("headline has no valid citation")
    for field in ("brief", "why_it_matters", "key_facts", "background", "whats_next",
                  "glossary", "timeline"):  # fmt: skip
        setattr(draft, field, filter_items(getattr(draft, field), valid, stats))
    f = draft.framing
    f.widely_reported = filter_items(f.widely_reported, valid, stats)
    f.reported_by_some = filter_items(f.reported_by_some, valid, stats)
    f.analysis_opinion = filter_items(f.analysis_opinion, valid, stats)
    rep = f.representative
    rep.widely_reported = filter_ids(rep.widely_reported, valid, stats)
    rep.reported_by_some = filter_ids(rep.reported_by_some, valid, stats)
    rep.analysis_opinion = filter_ids(rep.analysis_opinion, valid, stats)
    draft.viz_data.places = filter_items(draft.viz_data.places, valid, stats)
    if draft.viz_data.chart:
        draft.viz_data.chart.points = filter_items(draft.viz_data.chart.points, valid, stats)
        if len(draft.viz_data.chart.points) < 2:
            draft.viz_data.chart = None
    if not draft.brief:
        raise CardRejected("no brief bullet survived the citation filter")
    return draft, stats


def filter_extraction(ex: Extraction, valid: set[str]) -> tuple[Extraction, Counter]:
    stats: Counter = Counter()
    ex.claims = filter_items(ex.claims, valid, stats)
    ex.dates = filter_items(ex.dates, valid, stats)
    ex.numbers = filter_items(ex.numbers, valid, stats)
    ex.locations = filter_items(ex.locations, valid, stats)
    if not _keep(ex.one_liner, valid, stats):
        ex.one_liner = Sentence(text="", source_ids=[])
    return ex, stats
