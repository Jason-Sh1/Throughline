"""Token prices, so every extraction, card and pipeline run records what it cost.

USD per million tokens, checked 2026-10-25 against
https://platform.claude.com/docs/en/about-claude/pricing and
https://ai.google.dev/gemini-api/docs/pricing. Update when prices change.
Prompts are well under Haiku 5.5's 100K-token long-context threshold, so only
the standard rate card is listed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0  # 5-minute TTL


PRICES = {
    # Batch API prices (50% off). Editions always go through the Batch API; the
    # one retry for an invalid response is a standard request.
    ("claude-haiku-5-5", "batch"): Price(0.05, 0.25, cache_read=0.005, cache_write=0.0625),
    ("claude-haiku-5-5", "standard"): Price(0.10, 0.50, cache_read=0.01, cache_write=0.125),
    # Optional extraction backend. Paid-tier price; the free tier is free but
    # capped at 20 requests/day.
    ("gemini-2.5-flash-lite", "standard"): Price(0.10, 0.40),
}


def cost_usd(
    model: str,
    mode: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Output tokens include thinking tokens (both providers bill them as output)."""
    p = PRICES.get((model, mode))
    if p is None:
        return 0.0
    return (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_read_tokens * p.cache_read
        + cache_write_tokens * p.cache_write
    ) / 1_000_000
