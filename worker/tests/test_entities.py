from cluster.entities import extract


def test_extracts_names_and_acronyms():
    got = extract(
        "Fed's Jerome Powell says the Federal Reserve will hold rates. "
        "The EU and U.S. officials met in Brussels on Monday."
    )
    assert {"jerome powell", "federal reserve", "eu", "us", "brussels", "fed"} <= set(got)
    assert "monday" not in got and "the" not in got


def test_ignores_headline_filler():
    got = extract("Breaking: Israel and Hamas agree to Gaza ceasefire, Qatar says")
    assert got == ["gaza", "hamas", "israel", "qatar"]


def test_most_frequent_first_and_capped():
    text = "Gaza. Gaza aid. Gaza talks. " + " ".join(f"Name{i} spoke." for i in range(30))
    got = extract(text, max_entities=5)
    assert got[0] == "gaza" and len(got) == 5
