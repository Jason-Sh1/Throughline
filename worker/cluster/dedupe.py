"""Duplicate detection: URL canonicalization, title hashing, SimHash.

Three layers, cheapest first:
1. canonical URL  - the same article fetched twice (from RSS and GDELT, or
                    with different tracking parameters) is one row.
2. title hash     - exact same headline from a different outlet, usually a
                    wire story (AP/Reuters) republished verbatim.
3. SimHash        - near-identical text (a republished wire story with a
                    tweaked headline or an extra sentence).

Layers 2 and 3 don't drop the article: they mark it `duplicate_of` the first
copy, so we can still say "republished by N outlets".
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track where a click came from.
_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid",
    "cmpid", "cmp", "ncid", "ocid", "ref", "ref_src", "referrer", "src",
    "smid", "smtyp", "taid", "utm", "at_medium", "at_campaign", "partner",
    "rss", "feed", "output", "cid", "sh", "itm_source", "itm_medium", "itm_campaign",
}  # fmt: skip
_TRACKING_PREFIXES = ("utm_", "at_", "itm_", "pk_", "mtm_")


def canonicalize_url(url: str) -> str:
    """Normalize a URL so trivially different links to one article compare equal.

    Lowercases scheme/host, drops `www.`, default ports, fragments, tracking
    params and trailing slashes, upgrades http to https, and sorts the query.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("amp.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    # Many outlets serve AMP copies at .../amp or /amp/...; treat them as the original.
    path = re.sub(r"(/amp)+/?$", "", path)
    path = re.sub(r"^/amp/", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    query.sort()
    return urlunsplit(("https", host, path, urlencode(query), ""))


def normalize_title(title: str) -> str:
    """Lowercase, strip accents/punctuation, and drop a trailing ' - Outlet' suffix."""
    t = unicodedata.normalize("NFKD", title)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    # "Headline - The Outlet" / "Headline | Outlet": GDELT and some feeds add these.
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,40}$", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def title_hash(title: str) -> str:
    return hashlib.sha1(normalize_title(title).encode()).hexdigest()


def _shingles(text: str, n: int = 1) -> list[str]:
    words = normalize_title(text).split()
    if len(words) < n:
        return [" ".join(words)] if words else []
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]


def simhash(text: str, bits: int = 64) -> int:
    """64-bit SimHash over words.

    Each shingle hashes to 64 bits; every bit position gets +1 if the shingle's
    bit is set, -1 otherwise; the fingerprint keeps the positive positions.
    Similar texts share most shingles, so their fingerprints differ in only a
    few bits. Returned as a signed int so it fits Postgres `bigint`.
    """
    weights = [0] * bits
    for sh in _shingles(text):
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for i in range(bits):
            weights[i] += 1 if (h >> i) & 1 else -1
    value = sum(1 << i for i, w in enumerate(weights) if w > 0)
    return value - (1 << 64) if value >= (1 << 63) else value


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


# At or below this many differing bits (out of 64) two texts are near-duplicates.
# The classic value is 3 with word 3-grams (Manku et al. 2007), but that's
# calibrated for whole web pages. We only have title + RSS summary (30-80
# words), where one inserted word changes ~10% of the 3-grams. On test pairs,
# single words gave: lightly edited wire copies 5-8 bits apart, different
# write-ups of the same event 19-28, unrelated stories ~20. So: words, and 10.
# Revisit when tuning dedupe on real data (ROADMAP M2 human step).
SIMHASH_MAX_DISTANCE = 10

# SimHash on very short text is noisy (two different 8-word headlines can
# collide), so only use it when there's enough text.
SIMHASH_MIN_WORDS = 20


def simhash_text(title: str, summary: str | None) -> str | None:
    """The text we fingerprint, or None when it's too short to be reliable."""
    text = f"{title} {summary or ''}"
    return text if len(normalize_title(text).split()) >= SIMHASH_MIN_WORDS else None
