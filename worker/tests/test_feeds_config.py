import copy

import pytest

from ingest.feeds_config import DEFAULT_FEEDS_PATH, load_raw, validate

BASE = {
    "version": 1,
    "outlets": [
        {
            "slug": "left-paper",
            "name": "Left Paper",
            "homepage": "https://left.example",
            "region": "US",
            "allsides_rating": "lean_left",
            "allsides_url": "https://www.allsides.com/news-source/left-paper",
            "feeds": [{"url": "https://left.example/rss", "category": "politics"}],
        },
        {
            "slug": "right-paper",
            "name": "Right Paper",
            "homepage": "https://right.example",
            "region": "US",
            "allsides_rating": "right",
            "allsides_url": "https://www.allsides.com/news-source/right-paper",
            "feeds": [{"url": "https://right.example/rss", "category": "politics"}],
        },
    ],
}


def data(**outlet_overrides):
    d = copy.deepcopy(BASE)
    d["outlets"][0].update(outlet_overrides)
    return d


def test_repo_feeds_file_is_valid():
    parsed, report = validate(load_raw(DEFAULT_FEEDS_PATH))
    assert report.ok, report.errors
    assert parsed is not None and len(parsed.outlets) >= 10


def test_valid_minimal_file():
    parsed, report = validate(data())
    assert report.ok
    assert report.by_category["politics"] == 2


@pytest.mark.parametrize(
    "override",
    [
        {"allsides_rating": "far_left"},
        {"region": "usa"},
        {"slug": "Bad Slug"},
        {"homepage": "not a url"},
        {"feeds": []},
        {"unexpected": "field"},
    ],
)
def test_schema_errors(override):
    parsed, report = validate(data(**override))
    assert parsed is None
    assert not report.ok


def test_bad_category_is_schema_error():
    d = data()
    d["outlets"][0]["feeds"][0]["category"] = "gossip"
    _, report = validate(d)
    assert not report.ok


def test_duplicate_slug_and_url():
    d = data()
    d["outlets"][1]["slug"] = "left-paper"
    d["outlets"][1]["feeds"][0]["url"] = "https://left.example/rss/"  # trailing slash still dup
    _, report = validate(d)
    assert any("duplicate outlet slug" in e for e in report.errors)
    assert any("duplicate feed url" in e for e in report.errors)


def test_non_us_outlet_must_be_not_rated():
    _, report = validate(data(region="GB"))
    assert any("non-US outlets must be not_rated" in e for e in report.errors)
    _, report = validate(data(region="GB", allsides_rating="not_rated"))
    assert report.ok


def test_coverage_targets_warn_by_default_and_fail_when_strict():
    _, report = validate(data())
    assert report.ok
    assert any("target >= 25" in w for w in report.warnings)
    _, strict = validate(data(), strict=True)
    assert not strict.ok


def test_spectrum_imbalance_in_strict_mode():
    d = data()
    d["outlets"][1]["allsides_rating"] = "lean_left"  # now 2 left, 0 right
    _, report = validate(d, strict=True)
    assert any("spectrum imbalance" in e for e in report.errors)


def test_rated_outlet_without_allsides_url_warns():
    d = data()
    del d["outlets"][0]["allsides_url"]
    _, report = validate(d)
    assert report.ok
    assert any("no allsides_url" in w for w in report.warnings)
