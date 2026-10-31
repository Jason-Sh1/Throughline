"""Pydantic schemas for what the LLMs return and what we store.

Three layers:

1. `Extraction`: what the extraction model (Gemini Flash-Lite) returns for one
   cluster: entities and atomic claims, each claim citing source ids.
2. `CardDraft`: what the synthesis model (Claude Haiku) returns: the story card
   from CLAUDE.md, every sentence citing source ids.
3. `StoryCard`: what we store in `cards.card` and ship to the web app: the draft
   after the citation filter, with local ids ("S3") swapped for article ids and
   the deterministic parts (sources, framing headlines, confidence) added by code.

The LLMs cite short local ids ("S1".."S12") instead of database ids: they're
shorter, and an id outside the list is easy to spot and drop.

Length rules (3 bullets, <=5 glossary terms, 5-8 timeline nodes) are asked for in
the prompt but not put in the JSON schema: structured outputs don't support
array-length constraints, and a card with 2 good bullets beats a failed card.
`clamp_lengths` trims anything over the limit.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VizHint(StrEnum):
    TIMELINE = "timeline"
    MAP = "map"
    CHART = "chart"
    NONE = "none"


class Sentence(Strict):
    text: str
    source_ids: list[str]


# --- Extraction ---------------------------------------------------------------


class EntityKind(StrEnum):
    PERSON = "person"
    ORG = "org"
    PLACE = "place"
    EVENT = "event"
    OTHER = "other"


class ClaimStatus(StrEnum):
    CONFIRMED = "confirmed"  # officially confirmed or on the record
    REPORTED = "reported"  # reported by outlets, not officially confirmed
    ALLEGED = "alleged"  # an accusation or contested claim


class ExtractedEntity(Strict):
    name: str
    kind: EntityKind


class Claim(Strict):
    text: str
    source_ids: list[str]
    status: ClaimStatus
    attributed_to: str | None = Field(description="Who made the claim, if the sources say")


class DatedEvent(Strict):
    date: str = Field(description="YYYY-MM-DD, YYYY-MM or YYYY")
    event: str
    source_ids: list[str]


class Number(Strict):
    label: str
    value: float
    unit: str
    source_ids: list[str]


class Place(Strict):
    name: str
    source_ids: list[str]


class Extraction(Strict):
    entities: list[ExtractedEntity]
    claims: list[Claim]
    dates: list[DatedEvent]
    numbers: list[Number]
    locations: list[Place]
    viz_hint: VizHint
    one_liner: Sentence


# --- Story card (synthesis output) ----------------------------------------------


class GlossaryTerm(Strict):
    term: str
    definition: str
    source_ids: list[str]


class TimelineNode(Strict):
    date: str
    event: str
    source_ids: list[str]


class RepresentativeSources(Strict):
    """Source ids whose real headlines illustrate each framing bucket. The model
    only picks ids; code fills in the outlet's actual headline."""

    widely_reported: list[str]
    reported_by_some: list[str]
    analysis_opinion: list[str]


class Framing(Strict):
    widely_reported: list[Sentence]
    reported_by_some: list[Sentence]
    analysis_opinion: list[Sentence]
    representative: RepresentativeSources


class ChartPoint(Strict):
    label: str
    value: float
    source_ids: list[str]


class Chart(Strict):
    title: str
    unit: str
    points: list[ChartPoint]


class VizData(Strict):
    places: list[Place]
    chart: Chart | None


class CardDraft(Strict):
    headline: Sentence
    brief: list[Sentence]
    why_it_matters: list[Sentence]
    key_facts: list[Sentence]
    background: list[Sentence]
    glossary: list[GlossaryTerm]
    timeline: list[TimelineNode]
    framing: Framing
    whats_next: list[Sentence]
    viz_hint: VizHint
    viz_data: VizData


MAX_BRIEF = 3
MAX_GLOSSARY = 5
MAX_TIMELINE = 8


def clamp_lengths(draft: CardDraft) -> CardDraft:
    draft.brief = draft.brief[:MAX_BRIEF]
    draft.glossary = draft.glossary[:MAX_GLOSSARY]
    draft.timeline = draft.timeline[:MAX_TIMELINE]
    return draft


# --- Stored card ------------------------------------------------------------------


class SourceRef(BaseModel):
    """An outlet's article as shown in the UI: its real headline, never ours."""

    article_id: int
    outlet: str
    headline: str
    url: str
    published_at: str | None
    allsides_rating: str
    is_opinion: bool


class Confidence(BaseModel):
    outlet_count: int
    article_count: int
    spectrum: dict[str, int]  # allsides rating -> outlets
    developing: bool


class StoryCard(BaseModel):
    """`cards.card`. Text fields mirror CardDraft but cite article ids (ints)."""

    schema_version: int = 1
    cluster_id: int
    category: str | None
    headline: dict  # {text, source_ids}
    headline_label: str = "Throughline headline"
    ai_label: str  # "AI-generated from N sources"
    brief: list[dict]
    why_it_matters: list[dict]
    key_facts: list[dict]
    background: list[dict]
    glossary: list[dict]
    timeline: list[dict]
    framing: dict  # buckets of sentences + representative: {bucket: [SourceRef]}
    whats_next: list[dict]
    viz_hint: VizHint
    viz_data: dict
    confidence: Confidence
    sources: list[SourceRef]
    verified: bool = False  # set by the M4 verification pass
