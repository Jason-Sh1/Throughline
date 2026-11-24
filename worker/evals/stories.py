"""Eval stories (frozen inputs) and gold labels (human answers).

- `worker/evals/stories/<id>.json`: a snapshot of one cluster, made by
  `make eval-snapshot`. Inputs only; safe to regenerate.
- `worker/evals/gold/<id>.json`: the human labels for that story, made with
  `make label`. **Hand-labeled by the developer only; never by an LLM** (CLAUDE.md).

The repo is public, so a snapshot stores only what publishers syndicate in
their feeds: headline, outlet, link, time and the RSS summary (trimmed). Never
`raw_text`. That means eval inputs are a little thinner than production inputs
for the few sources (the Guardian) where we hold body text.

Story file:
  {id, cluster_id, category, snapshot_at,
   articles: [{ref, outlet, allsides_rating, kind, title, url, published_at,
               summary, is_opinion, in_cluster, used}]}
  `ref` is "S1".."S12" for the sources the model sees (`used`, the same pick as
  production) and "C1".. for near-miss articles outside the cluster, which exist
  so the clustering labels can catch stories that were wrongly split off.

Gold file:
  {story_id, labeled_by, labeled_at,
   membership: {ref: "same" | "different"},   # same event as the story?
   key_facts: [str],                            # facts a good briefing must state
   notes}
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from llm.inputs import SourceDoc

EVALS_DIR = Path(__file__).parent
STORIES_DIR = EVALS_DIR / "stories"
GOLD_DIR = EVALS_DIR / "gold"
SUMMARY_CHARS = 600


def load_stories(directory: Path = STORIES_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def load_gold(directory: Path = GOLD_DIR) -> dict[str, dict]:
    gold = {}
    for p in sorted(directory.glob("*.json")):
        g = json.loads(p.read_text(encoding="utf-8"))
        gold[g["story_id"]] = g
    return gold


def save_gold(g: dict, directory: Path = GOLD_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{g['story_id']}.json"
    path.write_text(json.dumps(g, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def story_docs(story: dict) -> list[SourceDoc]:
    """The source list the model sees, rebuilt from the snapshot."""
    docs = []
    for a in story["articles"]:
        if not a["used"]:
            continue
        published = datetime.fromisoformat(a["published_at"]) if a["published_at"] else None
        docs.append(
            SourceDoc(
                local_id=a["ref"],
                article_id=0,  # no database ids in a snapshot
                outlet=a["outlet"],
                allsides_rating=a["allsides_rating"],
                title=a["title"],
                url=a["url"],
                published_at=published,
                excerpt=a["summary"],
                is_opinion=a["is_opinion"],
            )
        )
    return docs


def select_suite(stories: list[dict], gold: dict, suite: str) -> list[dict]:
    """quick = 30 stories, labeled ones first, in a stable order; full = all."""
    ordered = sorted(stories, key=lambda s: (s["id"] not in gold, s["id"]))
    return ordered[:30] if suite == "quick" else ordered
