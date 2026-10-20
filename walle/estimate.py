"""Token estimators for context sources.

bytes-v1: ASCII at 4 characters per token, other characters at 1.3 characters per token
  (bytes_v1, also stack.estimate_tokens). `walle scan --estimator v1` uses it with its own budget.
bytes-v2: tokens per byte for each kind of source. The values come from a least-squares fit of the
  measured first-call tokens of 522 claude-opus-5-5 sessions on the author's machine (2026-10-20)
  against the recorded bytes of each source. The fit predicts held-out sessions with a median error
  of 1.7%. `range` is the 10th to 90th percentile of 30 bootstrap fits. Prose sources have one rate
  for ASCII bytes and one for other bytes (Korean, for example).

`walle scan` and `walle context` both use bytes-v2 by default, with the prose rates for instruction
files and the skill list. So both give one number for one file.

The fit leaves about 1,900 tokens per call (bootstrap range 1,200-2,400) that no recorded source
explains. `walle context` shows them in the residual row.

The bootstrap resamples sessions, not time. A fit on the earlier half that predicts the later half
moves prose_ascii to 0.48 and the unexplained part to about 0. Sources that barely change between
sessions (the skill list, the system prompt) share their weight with that constant, so their rates
are less certain than `range` shows.
"""

from __future__ import annotations

ESTIMATORS = ("bytes-v1", "bytes-v2")
DEFAULT = "bytes-v2"
# Short names for the command line: --estimator v1 is bytes-v1.
ALIASES = {"v1": "bytes-v1", "v2": "bytes-v2"}
# Default instruction-stack budget of each estimator, in its own tokens. 8,000 bytes-v1 tokens was the
# first budget. 14,500 bytes-v2 tokens keeps the same stacks OVER BUDGET on the author's machine
# (2026-10-20: the largest stack under the budget has 14,425, the smallest stack over it has 14,800).
BUDGETS = {"bytes-v1": 8000, "bytes-v2": 14500}
PROSE = ("skill_listing", "instructions", "first_prompt", "hook_context", "other_attachments")

# Tokens per byte. basis: "fit" (bootstrap range is tight), "fit, unstable" (wide range: it borrows the
# prose rates), "plan" (taken from the research plan).
BYTES_V2 = {
    "system_prompt": {"tok_per_byte": 0.27, "range": [0.26, 0.29], "basis": "fit"},
    "builtin_tools": {"tok_per_byte": 0.21, "range": [0.21, 0.22], "basis": "fit"},
    "mcp_tools": {"tok_per_byte": 0.27, "range": [0.27, 0.28], "basis": "fit"},
    # The plan measured 0.60-0.72 from 478 sessions; the fit gives 0.61-0.64. Tool names with
    # connector UUIDs cost about 2.5 times as much per byte as prose.
    "deferred_tools": {"tok_per_byte": 0.62, "range": [0.61, 0.64], "basis": "fit"},
    "mcp_instructions": {"tok_per_byte": 0.32, "range": [0.31, 0.35], "basis": "fit"},
    "agent_listing": {"tok_per_byte": 0.30, "range": [0.25, 0.33], "basis": "fit"},
    "prose_ascii": {"tok_per_byte": 0.41, "range": [0.40, 0.43], "basis": "fit"},
    "prose_other": {"tok_per_byte": 0.58, "range": [0.55, 0.59], "basis": "fit"},
}
FIT = {"model": "claude-opus-5-5", "sessions": 522, "date": "2026-10-20", "holdout_median_error": 0.017,
       "unexplained_tokens_per_call": 1900}


def bytes_v1(text: str) -> int:
    """Rough token count: ASCII at 4 chars per token, other chars at 1.3 chars per token."""
    ascii_n = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_n
    return round(ascii_n / 4 + other / 1.3)


def name(estimator: str) -> str:
    """The full name of an estimator: v1 -> bytes-v1. Raises ValueError for an unknown name."""
    full = ALIASES.get(estimator, estimator)
    if full not in ESTIMATORS:
        raise ValueError(f"unknown estimator {estimator!r}; use one of {', '.join([*ALIASES, *ESTIMATORS])}")
    return full


def budget(estimator: str = DEFAULT) -> int:
    """The default instruction-stack budget in the tokens of this estimator."""
    return BUDGETS[name(estimator)]


def prose(text: str, estimator: str = DEFAULT) -> int:
    """Estimated tokens of an instruction file or a part of one (the "instructions" source)."""
    return estimate(text, "instructions", estimator)


def estimate(text: str, source: str, estimator: str = DEFAULT) -> int:
    """Estimated tokens of one source's recorded text."""
    estimator = name(estimator)
    if estimator == "bytes-v1":
        return bytes_v1(text)
    if source in PROSE or source not in BYTES_V2:
        data = text.encode()
        ascii_n = sum(1 for c in text if ord(c) < 128)
        return round(ascii_n * BYTES_V2["prose_ascii"]["tok_per_byte"]
                     + (len(data) - ascii_n) * BYTES_V2["prose_other"]["tok_per_byte"])
    return round(len(text.encode()) * BYTES_V2[source]["tok_per_byte"])


def basis(source: str, estimator: str = DEFAULT) -> str:
    """How the estimate of a source was made, for the report."""
    if name(estimator) == "bytes-v1":
        return "bytes-v1"
    if source == "other_attachments":
        return "prose rates (fit unstable for this source)"
    if source in PROSE or source not in BYTES_V2:
        return "prose rates (fit)"
    return BYTES_V2[source]["basis"]


def describe(estimator: str = DEFAULT) -> dict:
    """The estimator record for a run block."""
    if name(estimator) == "bytes-v1":
        return {"name": "bytes-v1", "rule": "ASCII 4 chars per token, other chars 1.3 per token"}
    return {"name": "bytes-v2", "tok_per_byte": BYTES_V2, "prose_sources": list(PROSE), "fit": FIT}
