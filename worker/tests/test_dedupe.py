import pytest

from cluster.dedupe import (
    SIMHASH_MAX_DISTANCE,
    canonicalize_url,
    hamming,
    normalize_title,
    simhash,
    simhash_text,
    title_hash,
)


@pytest.mark.parametrize(
    "a,b",
    [
        ("https://www.example.com/news/story?utm_source=rss&utm_medium=feed",
         "https://example.com/news/story"),
        ("http://example.com/news/story/", "https://example.com/news/story"),
        ("https://EXAMPLE.com/news/story#comments", "https://example.com/news/story"),
        ("https://example.com/news/story/amp", "https://example.com/news/story"),
        ("https://example.com/amp/news/story", "https://example.com/news/story"),
        ("https://example.com/a?b=2&a=1&fbclid=xyz", "https://example.com/a?a=1&b=2"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com//a//b", "https://example.com/a/b"),
    ],
)  # fmt: skip
def test_canonicalize_equal(a, b):
    assert canonicalize_url(a) == canonicalize_url(b)


def test_canonicalize_keeps_meaningful_query():
    assert canonicalize_url("https://example.com/article?id=42") != canonicalize_url(
        "https://example.com/article?id=43"
    )


def test_canonicalize_keeps_nonstandard_port_and_root():
    assert canonicalize_url("https://example.com:8443/x") == "https://example.com:8443/x"
    assert canonicalize_url("https://example.com") == "https://example.com/"


def test_normalize_title_strips_outlet_suffix_and_punctuation():
    assert normalize_title("Fed Holds Rates Steady — Reuters") == "fed holds rates steady"
    assert normalize_title("Fed holds rates steady | The Hill") == "fed holds rates steady"
    assert normalize_title("Café “opens” again!") == "cafe opens again"


def test_title_hash_matches_across_outlets():
    assert title_hash("Fed holds rates steady - AP News") == title_hash("Fed Holds Rates Steady")
    assert title_hash("Fed holds rates") != title_hash("Fed cuts rates")


WIRE = (
    "WASHINGTON (AP) The Federal Reserve left its benchmark interest rate unchanged on "
    "Wednesday, saying inflation remains elevated even as hiring has slowed across most "
    "industries and consumer spending has cooled in recent months."
)


def test_simhash_flags_edited_wire_copies():
    for edited in (
        WIRE.replace("Wednesday", "Wednesday afternoon"),
        WIRE.replace("WASHINGTON (AP) ", "") + " Officials signaled cuts could come later.",
        "Federal Reserve leaves rates unchanged. " + WIRE,
    ):
        assert hamming(simhash(WIRE), simhash(edited)) <= SIMHASH_MAX_DISTANCE


def test_simhash_does_not_flag_independent_coverage_or_other_news():
    same_event = (
        "The Federal Reserve held interest rates steady on Wednesday and said inflation is "
        "still too high, while noting that job growth has slowed and consumers are spending "
        "less than earlier this year."
    )
    other = (
        "A powerful storm brought heavy snow and strong winds to the northern plains, closing "
        "highways and schools while utility crews worked to restore power to thousands of homes."
    )
    # Independent write-ups of one event are separate coverage, not duplicates.
    assert hamming(simhash(WIRE), simhash(same_event)) > SIMHASH_MAX_DISTANCE
    assert hamming(simhash(WIRE), simhash(other)) > SIMHASH_MAX_DISTANCE


def test_simhash_identical_and_signed_range():
    h = simhash(WIRE)
    assert hamming(h, simhash(WIRE)) == 0
    assert -(2**63) <= h < 2**63  # fits Postgres bigint


def test_simhash_text_requires_enough_words():
    assert simhash_text("Short headline", None) is None
    assert simhash_text("Headline", WIRE) is not None
