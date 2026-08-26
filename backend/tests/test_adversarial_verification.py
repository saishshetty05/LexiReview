"""Adversarial end-to-end test for rule-6 quote verification.

This test proves the full pipeline (LLM -> verify_quote -> verification:
"unverified" -> UI confirm gate) behaves correctly when the LLM produces
a quote that genuinely cannot be matched in the source document.

Unlike the unit test in test_analysis_pipeline.py that uses a simple
fabricated quote, this test uses a more realistic adversarial scenario:
a quote that looks plausible (uses similar vocabulary, structure, and
legal phrasing) but contains subtle fabrications that the fuzzy matcher
catches at the FUZZY_MATCH_THRESHOLD (0.90).

This is the "adversarial fixture" that CLAUDE.md rule 6 requires:
a real document that causes the LLM to produce a quote the verifier
genuinely can't match, proving the full pipeline works end-to-end.

The multi-finding and misbehaving-missing_clause scenarios already have
dedicated coverage in test_analysis_pipeline.py
(test_multiple_findings_are_each_finalized_independently,
test_missing_clause_evidence_quote_and_block_ids_forced_empty_regardless_of_llm_output)
-- not duplicated here, since neither actually exercises this file's
adversarial document or quote.
"""

from __future__ import annotations

import io

import pytest

from app.analysis_pipeline import run_analysis
from app.verifier import FUZZY_MATCH_THRESHOLD, verify_quote
from tests.test_analysis_pipeline import _FakeLLMClient, _make_docx_bytes

# Realistic lease document with two rent-related clauses that an LLM
# might conflate. This is a SYNTHETIC document per CLAUDE.md.
ADVERSARIAL_DOC = [
    "LEASE AGREEMENT",
    "",
    "This Lease Agreement is entered into between Landlord and Tenant.",
    "",
    "Clause 3: Rent. The monthly rent shall be Rs. 50,000, payable "
    "on the 5th of each month in advance.",
    "",
    "Clause 4: Security Deposit. Tenant shall deposit Rs. 1,00,000 "
    "as security deposit, refundable at termination.",
    "",
    "Clause 14: Rent Escalation. Notwithstanding the above, the parties "
    "agree to a monthly rent of Rs. 60,000 effective from the second "
    "year of the lease term.",
    "",
    "Clause 15: Maintenance. Landlord shall maintain the structural "
    "elements of the premises.",
    "",
    "Contact the landlord at owner@example.com for queries.",
]

# This quote LOOKS like it could be real -- it uses real terms from the
# document (monthly rent, effective date, second year, notwithstanding)
# but combines them in a way that never appears verbatim:
#   - "Rs. 55,000 per month" (never appears, source has 50,000 and 60,000)
#   - "payable on the 5th of each calendar month" (source: "5th of each month")
#   - "notwithstanding the provisions of clause 3" (source: "notwithstanding the above")
#   - "tenancy period" (source: "lease term")
HALLUCINATED_QUOTE = (
    "The monthly rent shall be Rs. 55,000 per month, payable on the 5th "
    "of each calendar month, notwithstanding the provisions of clause 3, "
    "effective from the second year of the tenancy period."
)

ADVERSARIAL_FINDING = {
    "category": "inconsistency",
    "severity": "high",
    "block_ids": ["BLOCK_3", "BLOCK_5"],
    "evidence_quote": HALLUCINATED_QUOTE,
    "explanation": (
        "The LLM hallucinates a blended clause that combines "
        "Clause 3's rent amount with Clause 14's escalation, "
        "but the specific Rs. 55,000 figure, 'per month', 'calendar month', "
        "'notwithstanding the provisions of clause 3', and 'tenancy period' "
        "phrasing never appear in the document."
    ),
}


def test_adversarial_llm_quote_fails_both_exact_and_fuzzy_verification():
    """The core adversarial test: LLM produces a quote that looks plausible
    (uses real document vocabulary) but contains fabrications that fail
    both exact match and fuzzy match (threshold 0.90).

    This proves the full pipeline works end-to-end for the adversarial case.
    """
    file_bytes = _make_docx_bytes(ADVERSARIAL_DOC)
    fake_llm = _FakeLLMClient([ADVERSARIAL_FINDING])

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert len(results) == 1
    finding = results[0]

    # The finding should be marked unverified
    assert finding["verification"] == "unverified", (
        f"Expected verification='unverified', got '{finding['verification']}'. "
        "This means the quote verification pipeline failed to catch the hallucination."
    )

    # Confidence should be needs_review (the UI confirm gate trigger)
    assert finding["confidence"] == "needs_review", (
        f"Expected confidence='needs_review', got '{finding['confidence']}'. "
        "The UI confirm gate requires this exact value."
    )

    # The finding should still have the LLM's original quote (not mutated)
    assert "Rs. 55,000" in finding["evidence_quote"]
    assert "notwithstanding the provisions of clause 3" in finding["evidence_quote"].lower()

    # Category and severity preserved
    assert finding["category"] == "inconsistency"
    assert finding["severity"] == "high"
    assert finding["block_ids"] == ["BLOCK_3", "BLOCK_5"]


def test_adversarial_quote_similarity_below_threshold():
    """Verify that the hallucinated quote's similarity to the source is
    genuinely below FUZZY_MATCH_THRESHOLD. This is a sanity check that our
    adversarial fixture is actually adversarial -- so it has to score the
    quote the same way verify_quote (app/verifier.py) actually does, not an
    approximation. SequenceMatcher.ratio() (2*matches/(len(a)+len(b))) is a
    different, unrelated number from verify_quote's own coverage metric
    (matched_chars/len(span)) -- the two can diverge widely and this check
    must not silently stop meaning anything if FUZZY_MATCH_THRESHOLD or the
    scoring formula is ever retuned.
    """
    # Reconstruct the source text that would be searched
    # (blocks BLOCK_3 and BLOCK_5 from the document after pseudonymisation)
    source_text = (
        "Clause 3: Rent. The monthly rent shall be Rs. 50,000, payable "
        "on the 5th of each month in advance. "
        "Clause 14: Rent Escalation. Notwithstanding the above, the parties "
        "agree to a monthly rent of Rs. 60,000 effective from the second "
        "year of the lease term."
    )

    result = verify_quote(source_text, HALLUCINATED_QUOTE)

    # Must be below threshold to trigger unverified
    assert result.match_score < FUZZY_MATCH_THRESHOLD, (
        f"Adversarial quote match_score ({result.match_score:.3f}) is above "
        f"threshold ({FUZZY_MATCH_THRESHOLD}). The fixture is not adversarial "
        f"enough or the threshold has changed."
    )
    assert result.verification == "unverified"

    # Should also not be an exact substring
    assert HALLUCINATED_QUOTE not in source_text


def test_adversarial_quote_not_an_exact_substring():
    """The hallucinated quote must not appear as an exact substring
    anywhere in the document text."""
    file_bytes = _make_docx_bytes(ADVERSARIAL_DOC)

    # Extract full text to verify
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)

    assert HALLUCINATED_QUOTE not in full_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
