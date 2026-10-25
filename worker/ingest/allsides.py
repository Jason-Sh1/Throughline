"""Import AllSides media bias ratings into feeds.yaml.

AllSides ratings are licensed CC BY-NC 4.0: we may use them non-commercially
as long as we attribute them wherever they appear. ATTRIBUTION below is the
text the UI and README show next to any rating.

Input is a CSV you download from AllSides (we don't scrape). Header names are
matched loosely so small export differences don't matter:
    name / news_source / outlet    the outlet name on AllSides
    rating / bias / bias_rating    e.g. "Lean Left", "left-center", "Center"
    url / allsides_url / page      the outlet's AllSides page (optional)

Usage:
    python -m ingest.allsides path/to/allsides.csv            # dry run: show changes
    python -m ingest.allsides path/to/allsides.csv --apply    # write feeds.yaml
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from ingest.feeds_config import DEFAULT_FEEDS_PATH, BiasRating, validate

ATTRIBUTION = (
    "Media bias ratings from AllSides (https://www.allsides.com/media-bias), licensed CC BY-NC 4.0."
)

_RATING_ALIASES = {
    "left": BiasRating.LEFT,
    "lean left": BiasRating.LEAN_LEFT,
    "left center": BiasRating.LEAN_LEFT,
    "center": BiasRating.CENTER,
    "lean right": BiasRating.LEAN_RIGHT,
    "right center": BiasRating.LEAN_RIGHT,
    "right": BiasRating.RIGHT,
    "mixed": BiasRating.MIXED,
    "allsides": BiasRating.MIXED,  # older exports call "Mixed" "allsides"
    "not rated": BiasRating.NOT_RATED,
}

_HEADER_ALIASES = {
    "name": ("name", "news_source", "outlet", "source"),
    "rating": ("rating", "bias", "bias_rating", "media_bias_rating"),
    "url": ("url", "allsides_url", "page", "allsides_page"),
}


def normalize_rating(raw: str) -> BiasRating:
    key = re.sub(r"[^a-z]+", " ", raw.lower()).strip()
    key = key.removeprefix("allsides media bias rating ")
    if key not in _RATING_ALIASES:
        raise ValueError(f"unknown AllSides rating: {raw!r}")
    return _RATING_ALIASES[key]


def normalize_name(name: str) -> str:
    """Lowercase, drop a leading 'the', a '(news)'/'(online)'/'(online news)'/'(opinion)'
    qualifier and punctuation."""
    s = name.lower()
    s = re.sub(r"\((online news|online|news|opinion)\)", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s.removeprefix("the ").strip()


@dataclass(frozen=True)
class AllSidesRow:
    name: str
    rating: BiasRating
    url: str | None


def read_csv(path: Path) -> list[AllSidesRow]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = {f.strip().lower(): f for f in reader.fieldnames or []}
        cols = {}
        for want, aliases in _HEADER_ALIASES.items():
            cols[want] = next((fields[a] for a in aliases if a in fields), None)
        if not cols["name"] or not cols["rating"]:
            raise ValueError(f"CSV needs name and rating columns, got {reader.fieldnames}")
        rows = []
        for raw in reader:
            name = (raw[cols["name"]] or "").strip()
            if not name:
                continue
            url = (raw[cols["url"]] or "").strip() if cols["url"] else ""
            rows.append(AllSidesRow(name, normalize_rating(raw[cols["rating"]]), url or None))
        return rows


@dataclass
class Change:
    slug: str
    field: str
    old: str | None
    new: str | None


def plan_changes(feeds_data, rows: list[AllSidesRow]) -> tuple[list[Change], list[str]]:
    """Compare feeds.yaml outlets to AllSides rows. Returns (changes, unmatched US slugs)."""
    by_name: dict[str, AllSidesRow] = {}
    for row in rows:
        # AllSides often lists an outlet's news and opinion sections separately.
        # We cluster news coverage, so the news rating wins; an "(Opinion)" row
        # is used only when there is no other row for that outlet.
        key = normalize_name(row.name)
        is_opinion = "opinion" in row.name.lower()
        if key not in by_name or (not is_opinion and "opinion" in by_name[key].name.lower()):
            by_name[key] = row

    changes: list[Change] = []
    unmatched: list[str] = []
    for outlet in feeds_data["outlets"]:
        if outlet["region"] != "US":
            continue  # non-US outlets stay not_rated by policy
        lookup = outlet.get("allsides_name") or outlet["name"]
        row = by_name.get(normalize_name(lookup))
        if row is None:
            unmatched.append(outlet["slug"])
            continue
        if outlet["allsides_rating"] != row.rating.value:
            changes.append(
                Change(
                    outlet["slug"], "allsides_rating", outlet["allsides_rating"], row.rating.value
                )
            )
        if row.url and outlet.get("allsides_url") != row.url:
            changes.append(
                Change(outlet["slug"], "allsides_url", outlet.get("allsides_url"), row.url)
            )
    return changes, unmatched


def apply_changes(feeds_data, changes: list[Change]) -> None:
    by_slug = {o["slug"]: o for o in feeds_data["outlets"]}
    for c in changes:
        outlet = by_slug[c.slug]
        if c.field == "allsides_url" and "allsides_url" not in outlet:
            # Insert right after the rating so the file stays readable.
            pos = list(outlet.keys()).index("allsides_rating") + 1
            outlet.insert(pos, "allsides_url", c.new)
        else:
            outlet[c.field] = c.new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import AllSides ratings into feeds.yaml")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--feeds", type=Path, default=DEFAULT_FEEDS_PATH)
    ap.add_argument("--apply", action="store_true", help="write changes to feeds.yaml")
    args = ap.parse_args(argv)

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with args.feeds.open() as fh:
        data = yaml.load(fh)

    rows = read_csv(args.csv)
    changes, unmatched = plan_changes(data, rows)

    print(f"Read {len(rows)} AllSides ratings. {ATTRIBUTION}")
    for c in changes:
        print(f"  {c.slug:<24} {c.field:<16} {c.old!s:>12} -> {c.new}")
    for slug in unmatched:
        print(f"  {slug:<24} no AllSides match (set allsides_name, or leave not_rated)")
    if not changes:
        print("No changes.")
        return 0

    if not args.apply:
        print(f"{len(changes)} changes. Re-run with --apply to write {args.feeds}.")
        return 0

    apply_changes(data, changes)
    _, report = validate(data)
    if not report.ok:
        print("Refusing to write: result fails validation:\n  " + "\n  ".join(report.errors))
        return 1
    with args.feeds.open("w") as fh:
        yaml.dump(data, fh)
    print(f"Wrote {len(changes)} changes to {args.feeds}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
