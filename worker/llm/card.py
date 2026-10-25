"""Turn a filtered CardDraft into the stored StoryCard.

Everything that can be computed instead of generated is computed here, not by
the LLM: the source list (real outlet headlines and links), the representative
headlines in the framing panel, the "AI-generated from N sources" label and the
coverage numbers in `confidence`. That's how rule 3 holds: the model only ever
picks source ids, and an outlet's headline always comes from our database.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import psycopg

from llm.inputs import load_cluster_rows
from llm.schemas import CardDraft, Confidence, SourceRef, StoryCard

DEVELOPING_HOURS = 6  # newest article this recent -> "developing"


def _ref(doc: dict) -> SourceRef:
    return SourceRef(
        article_id=doc["article_id"],
        outlet=doc["outlet"],
        headline=doc["title"],
        url=doc["url"],
        published_at=doc["published_at"],
        allsides_rating=doc["allsides_rating"],
        is_opinion=doc["is_opinion"],
    )


def confidence(rows: list[dict], now: datetime | None = None) -> Confidence:
    """Coverage numbers over *all* the cluster's articles, wire copies included:
    a wire story run by six outlets is six outlets' coverage."""
    now = now or datetime.now(UTC)
    outlets = {r["source_id"]: r["allsides_rating"] for r in rows}
    newest = max((r["published_at"] for r in rows if r["published_at"]), default=None)
    return Confidence(
        outlet_count=len(outlets),
        article_count=len(rows),
        spectrum=dict(Counter(outlets.values())),
        developing=bool(newest and now - newest < timedelta(hours=DEVELOPING_HOURS)),
    )


def story_category(conn: psycopg.Connection, cluster_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("select category::text from clusters where id = %s", (cluster_id,))
        row = cur.fetchone()
    return row[0] if row else None


def _cite(item: dict, ids: dict[str, int]) -> dict:
    item["source_ids"] = [ids[s] for s in item["source_ids"]]
    return item


def assemble_card(
    conn: psycopg.Connection, cluster_id: int, draft: CardDraft, docs: list[dict]
) -> StoryCard:
    ids = {d["local_id"]: d["article_id"] for d in docs}
    by_local = {d["local_id"]: d for d in docs}
    d = draft.model_dump(mode="json")

    def cite_all(items: list[dict]) -> list[dict]:
        return [_cite(i, ids) for i in items]

    framing = d["framing"]
    representative = {
        bucket: [_ref(by_local[s]).model_dump() for s in local_ids]
        for bucket, local_ids in framing.pop("representative").items()
    }
    framing = {k: cite_all(v) for k, v in framing.items()} | {"representative": representative}
    viz = d["viz_data"]
    viz["places"] = cite_all(viz["places"])
    if viz["chart"]:
        viz["chart"]["points"] = cite_all(viz["chart"]["points"])

    rows = load_cluster_rows(conn, cluster_id)
    return StoryCard(
        cluster_id=cluster_id,
        category=story_category(conn, cluster_id),
        headline=_cite(d["headline"], ids),
        ai_label=f"AI-generated from {len(docs)} sources",
        brief=cite_all(d["brief"]),
        why_it_matters=cite_all(d["why_it_matters"]),
        key_facts=cite_all(d["key_facts"]),
        background=cite_all(d["background"]),
        glossary=cite_all(d["glossary"]),
        timeline=cite_all(d["timeline"]),
        framing=framing,
        whats_next=cite_all(d["whats_next"]),
        viz_hint=d["viz_hint"],
        viz_data=viz,
        confidence=confidence(rows),
        sources=[_ref(x) for x in docs],
    )


def one_liner_card(
    conn: psycopg.Connection, cluster_id: int, sentence: dict, docs: list[dict]
) -> dict:
    """Long-tail stories get one cited sentence (from extraction) instead of a full
    card. Same shape as StoryCard's fields the Today list needs."""
    ids = {d["local_id"]: d["article_id"] for d in docs}
    rows = load_cluster_rows(conn, cluster_id)
    return {
        "schema_version": 1,
        "tier": "one_liner",
        "cluster_id": cluster_id,
        "category": story_category(conn, cluster_id),
        "one_liner": _cite(dict(sentence), ids),
        "ai_label": f"AI-generated from {len(docs)} sources",
        "confidence": confidence(rows).model_dump(),
        "sources": [_ref(x).model_dump() for x in docs],
    }
