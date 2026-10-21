"""What the input of a Claude API call costs, with prompt caching counted.

A first call's tokens are not all billed alike. A cache read costs a fraction of the base input price, a cache
write costs more than it: 1.25x for the 5-minute duration, 2x for the 1-hour one. Adding the three counts
treats them alike, so a session that read most of its prefix from the cache looks as costly as one that wrote it.

input_equiv weights each count by its price relative to the base input price of the model: uncached input
1, cache read the model's read rate, cache write 1.25 (5 minutes) or 2 (1 hour). It is in tokens, so models
compare by it only through usd. usd is input_equiv at the model's list price, None for a model this table
does not have. Batch, fast mode, regional and partner (Bedrock, Vertex) prices are not counted.

Prices: Anthropic first-party list prices per million tokens, as of PRICES_AS_OF. Update the table when they
change; an unknown model gets the usual read rate (0.1x) and no usd.
"""

from __future__ import annotations

import re

PRICES_AS_OF = "2026-10-06"
# Model id without a date suffix: (base input $/MTok, cache read $/MTok).
PRICES = {
    "claude-fable-5-1": (10.0, 0.25),
    "claude-mythos-5-1": (10.0, 0.25),
    "claude-fable-5": (10.0, 1.0),
    "claude-mythos-5": (10.0, 1.0),
    "claude-opus-5-5": (4.0, 0.20),
    "claude-opus-5": (5.0, 0.50),
    "claude-opus-4-8": (5.0, 0.50),
    "claude-opus-4-7": (5.0, 0.50),
    "claude-opus-4-6": (5.0, 0.50),
    "claude-sonnet-5-5": (2.0, 0.20),
    "claude-sonnet-5": (2.0, 0.20),
    "claude-sonnet-4-6": (3.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.01),
    "claude-haiku-4-5": (1.0, 0.10),
}
# Claude Haiku 5.5 bills a prompt over 100K tokens at 5x, cache reads included.
TIERS = {"claude-haiku-5-5": (100_000, 5.0)}
WRITE_5M, WRITE_1H = 1.25, 2.0
DEFAULT_READ = 0.1
DATE_RE = re.compile(r"-\d{8}$")


def model_key(model: str) -> str:
    return DATE_RE.sub("", (model or "").strip().lower())


def usd(input_equiv: float, model: str, prompt: int = 0) -> float | None:
    """input_equiv tokens at the model's list price, None for a model this table does not have. prompt is the
    call's input tokens before weighting: a model with a tier bills a longer prompt at a higher rate."""
    key = model_key(model)
    price = PRICES.get(key)
    if not price:
        return None
    limit, factor = TIERS.get(key, (None, 1.0))
    return round(input_equiv * price[0] * (factor if limit and prompt > limit else 1.0) / 1_000_000, 4)


def claude(measured: dict, model: str) -> dict:
    """The cost of one call's input. measured: input, cache_creation, cache_read and, when the transcript
    records the split, cache_write_1h (the rest of cache_creation is 5-minute writes)."""
    key = model_key(model)
    price = PRICES.get(key)
    base, read = price if price else (None, None)
    read_rate = read / base if price else DEFAULT_READ
    w1h = min(measured.get("cache_write_1h", 0), measured.get("cache_creation", 0))
    w5m = measured.get("cache_creation", 0) - w1h
    equiv = (measured.get("input", 0) + WRITE_5M * w5m + WRITE_1H * w1h + read_rate * measured.get("cache_read", 0))
    prompt = measured.get("input", 0) + measured.get("cache_creation", 0) + measured.get("cache_read", 0)
    return {"input_equiv": round(equiv), "usd": usd(equiv, key, prompt), "read_rate": round(read_rate, 4),
            "priced_as": key if price else None, "prices_as_of": PRICES_AS_OF}
