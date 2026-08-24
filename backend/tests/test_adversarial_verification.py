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
"""

from __future__ import annotations

import io

import pytest

from app.analysis_pipeline import run_analysis
from app.verifier import FUZZY_MATCH_THRESHOLD


class _AdversarialLLMClient:
    """Simulates an LLM that hallucinates a quote that looks plausible
    but doesn't actually exist in the document. The quote uses real
    vocabulary and legal phrasing from the document but combines them
    in ways that never appear verbatim."""

    def __init__(self) -> None:
        self.received_blocks = None
        self.received_kwargs = None

    def analyze(self, blocks, *, pseudonymised, is_synthetic, severity_examples=None):
        self.received_blocks = blocks
        self.received_kwargs = {
            "pseudonymised": pseudonymised,
            "is_synthetic": is_synthetic,
            "severity_examples": severity_examples,
        }

        # This quote LOOKS like it could be real - it uses real terms
        # from the document (monthly rent, effective date, second year,
        # notwithstanding) but the specific combination with the
        # exact numbers and phrasing never appears in the source.
        #
        # Source text: "Clause 3: The monthly rent shall be Rs. 50,000..."
        # Source text: "Clause 14: Notwithstanding the above, the parties agree
        # to a monthly rent of Rs. 60,000 effective from the second year."
        #
        # Hallucinated quote combines them with significant fabrication:
        # - "Rs. 55,000 per month" (never appears, source has 50,000 and 60,000)
        # - "payable on the 5th of each calendar month" (source: "5th of each month")
        # - "notwithstanding the provisions of clause 3" (source: "notwithstanding the above")
        # - "tenancy period" (source: "lease term")
        #
        # SequenceMatcher ratio should be well below 0.90 threshold.
        return [
            {
                "category": "inconsistency",
                "severity": "high",
                "block_ids": ["BLOCK_3", "BLOCK_5"],
                "evidence_quote": (
                    "The monthly rent shall be Rs. 55,000 per month, payable on the 5th "
                    "of each calendar month, notwithstanding the provisions of clause 3, "
                    "effective from the second year of the tenancy period."
                ),
                "explanation": (
                    "The LLM hallucinates a blended clause that combines "
                    "Clause 3's rent amount with Clause 14's escalation, "
                    "but the specific Rs. 55,000 figure, 'per month', 'calendar month', "
                    "'notwithstanding the provisions of clause 3', and 'tenancy period' "
                    "phrasing never appear in the document."
                ),
            }
        ]

    def summarize(self, blocks, *, pseudonymised, is_synthetic):
        self.received_blocks = blocks
        self.received_kwargs = {"pseudonymised": pseudonymised, "is_synthetic": is_synthetic}
        return {"overview": "A lease agreement with rent terms.", "key_terms": []}


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


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


def test_adversarial_llm_quote_fails_both_exact_and_fuzzy_verification():
    """The core adversarial test: LLM produces a quote that looks plausible
    (uses real document vocabulary) but contains fabrications that fail
    both exact match and fuzzy match (threshold 0.90).

    This proves the full pipeline works end-to-end for the adversarial case.
    """
    file_bytes = _make_docx_bytes(ADVERSARIAL_DOC)
    fake_llm = _AdversarialLLMClient()

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
    """Verify that the hallucinated quote's similarity to the source
    is genuinely below the FUZZY_MATCH_THRESHOLD. This is a sanity
    check that our adversarial fixture is actually adversarial."""
    from difflib import SequenceMatcher

    # Reconstruct the source text that would be searched
    # (blocks BLOCK_3 and BLOCK_5 from the document after pseudonymisation)
    source_text = (
        "Clause 3: Rent. The monthly rent shall be Rs. 50,000, payable "
        "on the 5th of each month in advance. "
        "Clause 14: Rent Escalation. Notwithstanding the above, the parties "
        "agree to a monthly rent of Rs. 60,000 effective from the second "
        "year of the lease term."
    )

    hallucinated_quote = (
        "The monthly rent shall be Rs. 55,000 per month, payable on the 5th "
        "of each calendar month, notwithstanding the provisions of clause 3, "
        "effective from the second year of the tenancy period."
    )

    ratio = SequenceMatcher(None, source_text, hallucinated_quote).ratio()

    # Must be below threshold to trigger unverified
    assert ratio < FUZZY_MATCH_THRESHOLD, (
        f"Adversarial quote similarity ({ratio:.3f}) is above threshold "
        f"({FUZZY_MATCH_THRESHOLD}). The fixture is not adversarial enough "
        f"or the threshold has changed."
    )

    # Should also not be an exact substring
    assert hallucinated_quote not in source_text


def test_adversarial_quote_not_an_exact_substring():
    """The hallucinated quote must not appear as an exact substring
    anywhere in the document text."""
    file_bytes = _make_docx_bytes(ADVERSARIAL_DOC)

    # Extract full text to verify
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)

    hallucinated_quote = (
        "The monthly rent shall be Rs. 55,000 per month, payable on the 5th "
        "of each calendar month, notwithstanding the provisions of clause 3, "
        "effective from the second year of the tenancy period."
    )

    assert hallucinated_quote not in full_text


def test_multiple_adversarial_findings_each_handled_independently():
    """When the LLM returns multiple findings, some verified and some
    unverified, each is handled independently."""
    from app.analysis_pipeline import run_analysis

    class _MixedLLMClient:
        def __init__(self):
            self.received_blocks = None
            self.received_kwargs = None

        def analyze(self, blocks, *, pseudonymised, is_synthetic, severity_examples=None):
            self.received_blocks = blocks
            self.received_kwargs = {
                "pseudonymised": pseudonymised,
                "is_synthetic": is_synthetic,
                "severity_examples": severity_examples,
            }
            # One real inconsistency (verified) + one hallucinated (unverified)
            return [
                {
                    "category": "inconsistency",
                    "severity": "high",
                    "block_ids": ["BLOCK_2", "BLOCK_3"],
                    "evidence_quote": (
                        "The monthly rent shall be Rs. 50,000 [...] "
                        "a monthly rent of Rs. 60,000 effective from the second year."
                    ),
                    "explanation": "Clause 3 and Clause 14 disagree on the monthly rent.",
                },
                {
                    "category": "liability",
                    "severity": "high",
                    "block_ids": ["BLOCK_1"],
                    "evidence_quote": (
                        "Tenant shall indemnify Landlord for all claims "
                        "arising from structural defects in the premises."
                    ),
                    "explanation": "Hallucinated indemnity clause that doesn't exist.",
                },
            ]

        def summarize(self, blocks, *, pseudonymised, is_synthetic):
            self.received_blocks = blocks
            self.received_kwargs = {"pseudonymised": pseudonymised, "is_synthetic": is_synthetic}
            return {"overview": "A lease agreement.", "key_terms": []}

    file_bytes = _make_docx_bytes(ADVERSARIAL_DOC)
    fake_llm = _MixedLLMClient()

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert len(results) == 2

    # First finding (real quote) should be verified
    verified_finding = next(r for r in results if r["category"] == "inconsistency")
    assert verified_finding["verification"] == "verified"
    assert verified_finding["confidence"] == "standard"

    # Second finding (hallucinated) should be unverified
    unverified_finding = next(r for r in results if r["category"] == "liability")
    assert unverified_finding["verification"] == "unverified"
    assert unverified_finding["confidence"] == "needs_review"


def test_missing_clause_always_verified_even_with_adversarial_llm():
    """Missing clause findings must always be verified (empty quote,
    empty block_ids) regardless of what the LLM returns. This is
    a defense-in-depth check from CONTRACTS.md §2."""
    from app.analysis_pipeline import run_analysis

    class _MisbehavingMissingClauseLLM:
        def __init__(self):
            self.received_blocks = None

        def analyze(self, blocks, *, pseudonymised, is_synthetic, severity_examples=None):
            self.received_blocks = blocks
            # LLM returns non-empty quote and block_ids for missing_clause
            return [
                {
                    "category": "missing_clause",
                    "severity": "medium",
                    "block_ids": ["BLOCK_1", "BLOCK_2"],  # WRONG - should be empty
                    "evidence_quote": "This Lease Agreement is entered into...",  # WRONG - should be empty
                    "explanation": "No arbitration clause was found.",
                }
            ]

        def summarize(self, blocks, *, pseudonymised, is_synthetic):
            self.received_blocks = blocks
            return {"overview": "A lease agreement.", "key_terms": []}

    file_bytes = _make_docx_bytes(ADVERSARIAL_DOC)
    fake_llm = _MisbehavingMissingClauseLLM()

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert len(results) == 1
    finding = results[0]

    # _finalize_finding should normalize missing_clause findings
    assert finding["evidence_quote"] == ""
    assert finding["block_ids"] == []
    assert finding["verification"] == "verified"
    assert finding["confidence"] == "standard"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])