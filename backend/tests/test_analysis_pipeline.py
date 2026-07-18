"""Tests for analysis_pipeline.py. Uses a fake LLM client (no network, no
real provider) to drive the full extract -> redact -> anchor -> analyze ->
verify chain end to end. All fixtures are SYNTHETIC documents built in-test
(python-docx), per CLAUDE.md's SYNTHETIC_ONLY rule."""

from __future__ import annotations

import io

from app.analysis_pipeline import run_analysis


class _FakeLLMClient:
    """Stands in for a real provider call: returns canned findings and
    records what it was invoked with, so tests can assert the pipeline
    redacted PII before anchoring and passed guardrail flags through
    correctly."""

    def __init__(self, findings: list[dict]) -> None:
        self._findings = findings
        self.received_blocks = None
        self.received_kwargs: dict | None = None

    def analyze(self, blocks, *, pseudonymised, is_synthetic):
        self.received_blocks = blocks
        self.received_kwargs = {"pseudonymised": pseudonymised, "is_synthetic": is_synthetic}
        return self._findings


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


LEASE_PARAGRAPHS = [
    "This Lease Agreement is entered into between Landlord and Tenant.",
    "Clause 3: The monthly rent shall be Rs. 50,000, payable on the 5th of each month.",
    "Clause 14: Notwithstanding the above, the parties agree to a monthly rent "
    "of Rs. 60,000 effective from the second year.",
    "Contact the landlord at owner@example.com for queries.",
]

INCONSISTENCY_FINDING = {
    "category": "inconsistency",
    "severity": "high",
    "block_ids": ["BLOCK_2", "BLOCK_3"],
    "evidence_quote": (
        "The monthly rent shall be Rs. 50,000 [...] "
        "a monthly rent of Rs. 60,000 effective from the second year."
    ),
    "explanation": "Clause 3 and Clause 14 disagree on the monthly rent.",
}

MISSING_CLAUSE_FINDING = {
    "category": "missing_clause",
    "severity": "medium",
    "block_ids": [],
    "evidence_quote": "",
    "explanation": "No arbitration clause was found in this document.",
}

FABRICATED_FINDING = {
    "category": "liability",
    "severity": "high",
    "block_ids": ["BLOCK_1"],
    "evidence_quote": "This clause completely fabricates non-existent liability terms.",
    "explanation": "Hallucinated by the LLM — should not verify.",
}


def test_inconsistency_finding_with_real_quote_is_verified():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient([INCONSISTENCY_FINDING])

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert len(results) == 1
    assert results[0]["verification"] == "verified"
    assert results[0]["confidence"] == "standard"
    assert results[0]["category"] == "inconsistency"


def test_missing_clause_finding_is_always_verified_with_no_quote():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient([MISSING_CLAUSE_FINDING])

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert results[0]["verification"] == "verified"
    assert results[0]["confidence"] == "standard"


def test_fabricated_quote_is_unverified_and_needs_review():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient([FABRICATED_FINDING])

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert results[0]["verification"] == "unverified"
    assert results[0]["confidence"] == "needs_review"


def test_pii_is_redacted_before_the_llm_ever_sees_it():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient([])

    run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    block_texts = " ".join(block.text for block in fake_llm.received_blocks)
    assert "owner@example.com" not in block_texts
    assert "[REDACTED_EMAIL]" in block_texts


def test_llm_client_receives_pseudonymised_true_and_the_synthetic_flag():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient([])

    run_analysis(file_bytes, "docx", fake_llm, is_synthetic=False)

    assert fake_llm.received_kwargs == {"pseudonymised": True, "is_synthetic": False}


def test_multiple_findings_are_each_finalized_independently():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient([INCONSISTENCY_FINDING, MISSING_CLAUSE_FINDING, FABRICATED_FINDING])

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert len(results) == 3
    verifications = {r["category"]: r["verification"] for r in results}
    assert verifications == {
        "inconsistency": "verified",
        "missing_clause": "verified",
        "liability": "unverified",
    }


def test_original_finding_dicts_passed_by_caller_are_not_mutated():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    original = dict(MISSING_CLAUSE_FINDING)
    fake_llm = _FakeLLMClient([original])

    run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert "verification" not in original
    assert "confidence" not in original
