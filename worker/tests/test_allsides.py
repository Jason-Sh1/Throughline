from pathlib import Path

import pytest
from ruamel.yaml import YAML

from ingest import allsides
from ingest.allsides import normalize_name, normalize_rating, plan_changes, read_csv
from ingest.feeds_config import BiasRating, load

FIXTURES = Path(__file__).parent / "fixtures"

FEEDS_YAML = """\
# comment that must survive --apply
version: 1
outlets:
  - slug: left-paper
    name: The Left Paper
    homepage: https://left.example
    region: US
    allsides_rating: left
    feeds:
      - url: https://left.example/rss
        category: politics
  - slug: right-paper
    name: Right Paper
    homepage: https://right.example
    region: US
    allsides_rating: right
    feeds:
      - url: https://right.example/rss
        category: politics
  - slug: mid
    name: Middle Times Online
    allsides_name: Middle Times
    homepage: https://mid.example
    region: US
    allsides_rating: not_rated
    feeds:
      - url: https://mid.example/rss
        category: us
  - slug: foreign-daily
    name: Foreign Daily
    homepage: https://foreign.example
    region: GB
    allsides_rating: not_rated
    feeds:
      - url: https://foreign.example/rss
        category: world
  - slug: unknown
    name: Not On AllSides
    homepage: https://unknown.example
    region: US
    allsides_rating: not_rated
    feeds:
      - url: https://unknown.example/rss
        category: tech
"""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Left", BiasRating.LEFT),
        ("Lean Left", BiasRating.LEAN_LEFT),
        ("left-center", BiasRating.LEAN_LEFT),
        ("CENTER", BiasRating.CENTER),
        ("right-center", BiasRating.LEAN_RIGHT),
        ("AllSides Media Bias Rating: Lean Right", BiasRating.LEAN_RIGHT),
        ("Mixed", BiasRating.MIXED),
        ("allsides", BiasRating.MIXED),
    ],
)
def test_normalize_rating(raw, expected):
    assert normalize_rating(raw) == expected


def test_unknown_rating_raises():
    with pytest.raises(ValueError):
        normalize_rating("very left")


def test_normalize_name():
    assert normalize_name("The New York Times (News)") == "new york times"
    assert normalize_name("Fox News (Online News)") == "fox news"
    assert normalize_name("Reason") == "reason"
    assert normalize_name("CBS News (Online)") == "cbs news"


def test_read_csv_and_plan(tmp_path):
    rows = read_csv(FIXTURES / "allsides_sample.csv")
    assert len(rows) == 5

    data = YAML().load(FEEDS_YAML)
    changes, unmatched = plan_changes(data, rows)
    got = {(c.slug, c.field): c.new for c in changes}

    assert got[("left-paper", "allsides_rating")] == "lean_left"  # "The" prefix ignored
    # "Right Paper (Opinion)" is listed first, but the news row wins.
    assert got[("right-paper", "allsides_rating")] == "lean_right"
    assert got[("right-paper", "allsides_url")].endswith("/right-paper")
    assert got[("mid", "allsides_rating")] == "center"  # matched via allsides_name
    assert not any(c.slug == "foreign-daily" for c in changes)  # non-US never rated
    assert unmatched == ["unknown"]


def test_apply_writes_valid_yaml_and_keeps_comments(tmp_path):
    feeds = tmp_path / "feeds.yaml"
    feeds.write_text(FEEDS_YAML)
    rc = allsides.main([str(FIXTURES / "allsides_sample.csv"), "--feeds", str(feeds), "--apply"])
    assert rc == 0
    text = feeds.read_text()
    assert "# comment that must survive --apply" in text
    parsed = load(feeds)
    left = next(o for o in parsed.outlets if o.slug == "left-paper")
    assert left.allsides_rating == BiasRating.LEAN_LEFT
    assert left.allsides_url == "https://www.allsides.com/news-source/left-paper"


def test_dry_run_does_not_write(tmp_path):
    feeds = tmp_path / "feeds.yaml"
    feeds.write_text(FEEDS_YAML)
    allsides.main([str(FIXTURES / "allsides_sample.csv"), "--feeds", str(feeds)])
    assert feeds.read_text() == FEEDS_YAML
