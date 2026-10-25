"""feeds.yaml schema, loader and validator.

feeds.yaml is grouped by outlet rather than one row per feed: an outlet's
region and AllSides rating are properties of the outlet, so writing them once
avoids two feeds from the same outlet disagreeing.

Run `python -m ingest.feeds_config` (or `make validate-feeds`) to validate.
Add `--strict` to also enforce the launch targets (>=25 feeds per category,
spectrum balance) once the full list is curated.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEEDS_PATH = REPO_ROOT / "feeds" / "feeds.yaml"

MIN_FEEDS_PER_CATEGORY = 25
# In strict mode, neither side of the spectrum may have more than this many
# times the feeds of the other (counting lean_* toward its side).
MAX_SIDE_RATIO = 1.5


class Category(StrEnum):
    US = "us"
    WORLD = "world"
    POLITICS = "politics"
    BUSINESS = "business"
    TECH = "tech"
    SCIENCE = "science"
    HEALTH = "health"
    CLIMATE = "climate"
    SPORTS = "sports"
    CULTURE = "culture"


class BiasRating(StrEnum):
    LEFT = "left"
    LEAN_LEFT = "lean_left"
    CENTER = "center"
    LEAN_RIGHT = "lean_right"
    RIGHT = "right"
    MIXED = "mixed"
    NOT_RATED = "not_rated"


LEFT_SIDE = {BiasRating.LEFT, BiasRating.LEAN_LEFT}
RIGHT_SIDE = {BiasRating.RIGHT, BiasRating.LEAN_RIGHT}

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Region = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]


def _check_http_url(v: str) -> str:
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"not an http(s) URL: {v!r}")
    return v


class Feed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    category: Category

    _url = field_validator("url")(_check_http_url)


class Outlet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: Slug
    name: str = Field(min_length=1)
    homepage: str
    region: Region
    allsides_rating: BiasRating
    # Set by the AllSides import; the per-outlet page we link to for attribution.
    allsides_url: str | None = None
    # Use when the outlet's name on AllSides differs from `name`.
    allsides_name: str | None = None
    feeds: list[Feed] = Field(min_length=1)

    _homepage = field_validator("homepage")(_check_http_url)


class FeedsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    outlets: list[Outlet]


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    by_category: Counter = field(default_factory=Counter)
    by_rating: Counter = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_raw(path: Path = DEFAULT_FEEDS_PATH):
    yaml = YAML()
    with path.open() as fh:
        return yaml.load(fh)


def load(path: Path = DEFAULT_FEEDS_PATH) -> FeedsFile:
    return FeedsFile.model_validate(load_raw(path))


def validate(data: object, strict: bool = False) -> tuple[FeedsFile | None, Report]:
    """Validate parsed YAML. Schema errors stop early; policy checks collect all problems."""
    report = Report()
    try:
        parsed = FeedsFile.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            report.errors.append(f"{loc}: {err['msg']}")
        return None, report

    slugs = Counter(o.slug for o in parsed.outlets)
    for slug, n in slugs.items():
        if n > 1:
            report.errors.append(f"duplicate outlet slug: {slug}")

    urls = Counter(f.url.rstrip("/") for o in parsed.outlets for f in o.feeds)
    for url, n in urls.items():
        if n > 1:
            report.errors.append(f"duplicate feed url: {url}")

    for outlet in parsed.outlets:
        # CLAUDE.md: non-U.S. outlets are shown as "Not rated" even if AllSides
        # has a page for them, because its ratings are calibrated to U.S. politics.
        if outlet.region != "US" and outlet.allsides_rating != BiasRating.NOT_RATED:
            report.errors.append(
                f"{outlet.slug}: non-US outlets must be not_rated (got {outlet.allsides_rating})"
            )
        if outlet.allsides_rating not in (BiasRating.NOT_RATED,) and not outlet.allsides_url:
            report.warnings.append(
                f"{outlet.slug}: rated but no allsides_url yet (run the AllSides import)"
            )
        for feed in outlet.feeds:
            if urlparse(feed.url).scheme != "https":
                report.warnings.append(f"{outlet.slug}: feed is not https: {feed.url}")
            report.by_category[feed.category] += 1
            report.by_rating[outlet.allsides_rating] += 1

    for cat in Category:
        n = report.by_category[cat]
        if n < MIN_FEEDS_PER_CATEGORY:
            msg = f"category {cat}: {n} feeds (target >= {MIN_FEEDS_PER_CATEGORY})"
            (report.errors if strict else report.warnings).append(msg)

    left = sum(report.by_rating[r] for r in LEFT_SIDE)
    right = sum(report.by_rating[r] for r in RIGHT_SIDE)
    if left and right:
        ratio = max(left, right) / min(left, right)
        if ratio > MAX_SIDE_RATIO:
            msg = f"spectrum imbalance: left={left} right={right} (ratio {ratio:.2f})"
            (report.errors if strict else report.warnings).append(msg)
    elif left or right:
        msg = f"spectrum imbalance: left={left} right={right}"
        (report.errors if strict else report.warnings).append(msg)

    return parsed, report


def format_report(report: Report) -> str:
    lines = []
    if report.by_category:
        lines.append("Feeds per category:")
        for cat in Category:
            lines.append(f"  {cat.value:<9} {report.by_category[cat]}")
        lines.append("Feeds per AllSides rating:")
        for r in BiasRating:
            lines.append(f"  {r.value:<10} {report.by_rating[r]}")
    for w in report.warnings:
        lines.append(f"WARN  {w}")
    for e in report.errors:
        lines.append(f"ERROR {e}")
    lines.append("OK" if report.ok else f"FAILED ({len(report.errors)} errors)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate feeds.yaml")
    ap.add_argument("path", nargs="?", type=Path, default=DEFAULT_FEEDS_PATH)
    ap.add_argument("--strict", action="store_true", help="enforce launch coverage targets")
    args = ap.parse_args(argv)

    _, report = validate(load_raw(args.path), strict=args.strict)
    print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
