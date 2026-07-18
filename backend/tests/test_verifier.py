"""Tests for verifier.py — deterministic quote verification (CONTRACTS.md §2)."""

from __future__ import annotations

from app.verifier import FUZZY_MATCH_THRESHOLD, verify_quote

SOURCE = (
    "This Lease Agreement is entered into between Landlord and Tenant.\n\n"
    "Clause 3: The monthly rent shall be Rs. 50,000, payable on the 5th of "
    "each month.\n\n"
    "Clause 14: Notwithstanding the above, the parties agree to a monthly "
    "rent of Rs. 60,000 effective from the second year.\n\n"
    "Clause 20: This agreement is governed by the laws of the state."
)


def test_exact_substring_is_verified():
    quote = "The monthly rent shall be Rs. 50,000, payable on the 5th of each month."
    result = verify_quote(SOURCE, quote)
    assert result.verification == "verified"
    assert result.match_score == 1.0


def test_near_match_with_whitespace_artifact_is_verified():
    """Extraction can collapse/insert whitespace; a single-space difference
    should still clear the fuzzy threshold."""
    quote = "The monthly rent shall be Rs. 50,000,  payable on the 5th of each month."
    result = verify_quote(SOURCE, quote)
    assert result.verification == "verified"
    assert result.match_score >= FUZZY_MATCH_THRESHOLD


def test_unrelated_quote_is_unverified():
    quote = "This clause discusses intellectual property assignment and non-compete terms."
    result = verify_quote(SOURCE, quote)
    assert result.verification == "unverified"
    assert result.match_score < FUZZY_MATCH_THRESHOLD


def test_empty_quote_is_unverified():
    """Callers must not invoke this for missing_clause findings (always
    verified per CONTRACTS.md §2) — but the function itself must not raise
    or silently mis-verify on an empty quote."""
    result = verify_quote(SOURCE, "")
    assert result.verification == "unverified"
    assert result.match_score == 0.0


def test_inconsistency_quote_both_spans_present_is_verified():
    quote = (
        "The monthly rent shall be Rs. 50,000 [...] "
        "monthly rent of Rs. 60,000 effective from the second year."
    )
    result = verify_quote(SOURCE, quote)
    assert result.verification == "verified"


def test_inconsistency_quote_one_span_missing_is_unverified():
    quote = (
        "The monthly rent shall be Rs. 50,000 [...] "
        "the tenant shall repaint the walls a specific shade of blue"
    )
    result = verify_quote(SOURCE, quote)
    assert result.verification == "unverified"


def test_inconsistency_verification_uses_weakest_span():
    """match_score reflects the weaker of the two spans, not an average —
    one bad span must not be diluted by one perfect span."""
    quote = (
        "The monthly rent shall be Rs. 50,000 [...] "
        "completely fabricated text that appears nowhere in the document"
    )
    result = verify_quote(SOURCE, quote)
    assert result.match_score < FUZZY_MATCH_THRESHOLD


def test_inconsistency_quote_with_more_than_two_spans_is_unverified():
    """CONTRACTS.md §2: inconsistency findings have EXACTLY 2 block_ids, i.e.
    exactly 2 conflicting spans. A quote where the separator appears more
    than once is a caller bug, not a shape worth scoring -- fail-safe
    reading of the contract: unverified outright, not min()-scored against
    an unexpected span count, and not a raised exception either."""
    quote = (
        "The monthly rent shall be Rs. 50,000 [...] "
        "monthly rent of Rs. 60,000 effective from the second year [...] "
        "a third, unexpected span"
    )
    result = verify_quote(SOURCE, quote)
    assert result.verification == "unverified"
    assert result.match_score == 0.0
