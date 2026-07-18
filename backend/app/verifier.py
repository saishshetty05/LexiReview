"""verifier.py — deterministic quote verification (CONTRACTS.md §2, CLAUDE.md rule 6).

Confirms an LLM-produced evidence_quote actually appears in the source text:
an exact substring match first, then a fuzzy fallback for near-matches
(whitespace/unicode differences introduced by extraction). A quote that
fails both is never dropped by this module — callers still record the
finding, just with verification="unverified" (constitution rule 6).

missing_clause findings have no quote to check (evidence_quote is always
"") and are always verification="verified" per CONTRACTS.md §2 — callers
must set that directly and must not invoke verify_quote for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

# Below this, a quote is treated as a wrong/hallucinated match rather than an
# extraction artifact. Set high and deliberately: a false "verified" on a
# legal document is worse than an extra "unverified" flag, which constitution
# rule 6 already handles safely (shown, never dropped).
FUZZY_MATCH_THRESHOLD = 0.90

# CONTRACTS.md §2: inconsistency findings join their two conflicting spans
# with this separator in evidence_quote.
INCONSISTENCY_QUOTE_SEPARATOR = " [...] "


@dataclass(frozen=True)
class VerificationResult:
    verification: str  # "verified" | "unverified"
    match_score: float  # 1.0 for an exact match; else the weakest span's fuzzy coverage


def verify_quote(source_text: str, quote: str) -> VerificationResult:
    """Verify `quote` against `source_text`.

    Inconsistency findings encode two spans joined by
    INCONSISTENCY_QUOTE_SEPARATOR; each is verified independently and the
    quote is "verified" only if every span is.
    """
    if not quote:
        return VerificationResult(verification="unverified", match_score=0.0)

    if INCONSISTENCY_QUOTE_SEPARATOR in quote:
        spans = quote.split(INCONSISTENCY_QUOTE_SEPARATOR)
        # Splitting itself never raises -- the separator appearing more than
        # once is tolerated at the string level and min() below would happily
        # degrade to a score for any span count. But CONTRACTS.md §2 means
        # exactly 2 spans for an inconsistency quote, so a different count is
        # a caller bug, not a shape worth scoring: treat it as unverified
        # outright rather than trusting whatever min() produces for a
        # malformed input. Fail-safe reading of the contract's "two spans"
        # rule (constitution rule 6: never let a wrong-shaped quote pass as
        # verified).
        if len(spans) != 2:
            return VerificationResult(verification="unverified", match_score=0.0)
    else:
        spans = [quote]

    match_score = min(_span_score(source_text, span) for span in spans)
    verification = "verified" if match_score >= FUZZY_MATCH_THRESHOLD else "unverified"
    return VerificationResult(verification=verification, match_score=match_score)


def _span_score(source_text: str, span: str) -> float:
    if not span:
        return 0.0
    if span in source_text:
        return 1.0
    # autojunk=False: the default heuristic is tuned for source-code diffing
    # (treats frequently-repeated lines as junk) and isn't appropriate for
    # prose. Coverage is measured as the fraction of `span`'s characters
    # found in matching runs against `source_text`, not the combined-length
    # ratio SequenceMatcher.ratio() would give — that would understate a
    # perfect near-match buried in a multi-page document.
    matcher = SequenceMatcher(None, source_text, span, autojunk=False)
    matched_chars = sum(block.size for block in matcher.get_matching_blocks())
    return matched_chars / len(span)
