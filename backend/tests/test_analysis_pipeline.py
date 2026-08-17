"""Tests for analysis_pipeline.py. Uses a fake LLM client (no network, no
real provider) to drive the full extract -> redact -> anchor -> analyze ->
verify chain end to end. All fixtures are SYNTHETIC documents built in-test
(python-docx), per CLAUDE.md's SYNTHETIC_ONLY rule."""

from __future__ import annotations

import io

from app.analysis_pipeline import run_analysis, run_summary
from app.llm_client import REQUIRED_NO_ISSUES_PHRASE


class _FakeLLMClient:
    """Stands in for a real provider call: returns canned findings/summary
    and records what it was invoked with, so tests can assert the pipeline
    redacted PII before anchoring and passed guardrail flags through
    correctly."""

    def __init__(self, findings: list[dict] | None = None, summary: dict | None = None) -> None:
        self._findings = findings if findings is not None else []
        self._summary = summary or {"overview": "A lease agreement.", "key_terms": []}
        self.received_blocks = None
        self.received_kwargs: dict | None = None

    def analyze(self, blocks, *, pseudonymised, is_synthetic, severity_examples=None):
        self.received_blocks = blocks
        self.received_kwargs = {
            "pseudonymised": pseudonymised,
            "is_synthetic": is_synthetic,
            "severity_examples": severity_examples,
        }
        return self._findings

    def summarize(self, blocks, *, pseudonymised, is_synthetic):
        self.received_blocks = blocks
        self.received_kwargs = {"pseudonymised": pseudonymised, "is_synthetic": is_synthetic}
        return self._summary


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


def test_missing_clause_evidence_quote_and_block_ids_forced_empty_regardless_of_llm_output():
    """Found live: the model sometimes returns a non-empty evidence_quote
    (placeholder text, or the entire document concatenated) and non-empty
    block_ids for missing_clause findings, violating CONTRACTS.md §2 --
    nothing validated provider output against the contract, so it passed
    straight through to the frontend. Feeds a deliberately misbehaving fake
    LLM response and asserts _finalize_finding normalizes it regardless,
    matching the same "LLM doesn't own this field" pattern already applied
    to risk_snapshot and the rule-7 summary override."""
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    misbehaving_finding = {
        **MISSING_CLAUSE_FINDING,
        "evidence_quote": "This Lease Agreement is entered into between Landlord and Tenant. "
        "Clause 3: The monthly rent shall be Rs. 50,000...",
        "block_ids": ["BLOCK_1", "BLOCK_2"],
    }
    fake_llm = _FakeLLMClient([misbehaving_finding])

    results = run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert results[0]["evidence_quote"] == ""
    assert results[0]["block_ids"] == []
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

    assert fake_llm.received_kwargs == {
        "pseudonymised": True,
        "is_synthetic": False,
        "severity_examples": None,
    }


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


def test_severity_examples_are_passed_through_to_the_llm_client():
    """CONTRACTS.md §7a (v1.8): run_analysis stays DB-free -- the caller
    (worker.py) fetches this user's past overrides and hands them straight
    through, unmodified, as few-shot guidance."""
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient([])
    examples = [
        {
            "category": "payment",
            "evidence_quote": "some prior clause",
            "original_severity": "high",
            "severity_override": "medium",
        }
    ]

    run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True, severity_examples=examples)

    assert fake_llm.received_kwargs["severity_examples"] == examples


def test_original_finding_dicts_passed_by_caller_are_not_mutated():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    original = dict(MISSING_CLAUSE_FINDING)
    fake_llm = _FakeLLMClient([original])

    run_analysis(file_bytes, "docx", fake_llm, is_synthetic=True)

    assert "verification" not in original
    assert "confidence" not in original


# ── run_summary() ────────────────────────────────────────────────────────


def test_run_summary_computes_risk_snapshot_from_findings_not_the_llm():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    findings = [
        {**INCONSISTENCY_FINDING, "severity": "high"},
        {**MISSING_CLAUSE_FINDING, "severity": "medium"},
        {"category": "liability", "severity": "medium", "block_ids": [], "evidence_quote": "", "explanation": ""},
    ]
    fake_llm = _FakeLLMClient(summary={"overview": "A lease agreement.", "key_terms": []})

    result = run_summary(file_bytes, "docx", fake_llm, findings, is_synthetic=True)

    assert result["risk_snapshot"] == {"high": 1, "medium": 2, "low": 0, "info": 0}


def test_run_summary_preserves_llm_overview_when_findings_exist():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    findings = [{**INCONSISTENCY_FINDING, "severity": "high"}]
    fake_llm = _FakeLLMClient(summary={"overview": "A lease with a rent inconsistency.", "key_terms": []})

    result = run_summary(file_bytes, "docx", fake_llm, findings, is_synthetic=True)

    assert result["overview"] == "A lease with a rent inconsistency."


def test_run_summary_overrides_overview_to_required_phrase_on_clean_document():
    """CONTRACTS.md §2c bench requirement: a genuinely clean synthetic
    contract (no findings) must get the exact required phrase, regardless
    of whatever the LLM actually said -- the override is deterministic, not
    a hope the model phrased the empty case correctly."""
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient(
        findings=[], summary={"overview": "This is a perfectly ordinary lease.", "key_terms": []}
    )

    result = run_summary(file_bytes, "docx", fake_llm, findings=[], is_synthetic=True)

    assert result["overview"] == REQUIRED_NO_ISSUES_PHRASE
    assert result["risk_snapshot"] == {"high": 0, "medium": 0, "low": 0, "info": 0}


def test_run_summary_includes_key_terms_from_llm():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    key_terms = [{"label": "monthly_rent", "detail": "Rs. 50,000"}]
    fake_llm = _FakeLLMClient(summary={"overview": "A lease agreement.", "key_terms": key_terms})

    result = run_summary(file_bytes, "docx", fake_llm, findings=[], is_synthetic=True)

    assert result["key_terms"] == key_terms


def test_run_summary_llm_client_receives_pseudonymised_true_and_the_synthetic_flag():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient()

    run_summary(file_bytes, "docx", fake_llm, findings=[], is_synthetic=False)

    assert fake_llm.received_kwargs == {"pseudonymised": True, "is_synthetic": False}


def test_run_summary_pii_is_redacted_before_the_llm_ever_sees_it():
    file_bytes = _make_docx_bytes(LEASE_PARAGRAPHS)
    fake_llm = _FakeLLMClient()

    run_summary(file_bytes, "docx", fake_llm, findings=[], is_synthetic=True)

    block_texts = " ".join(block.text for block in fake_llm.received_blocks)
    assert "owner@example.com" not in block_texts
    assert "[REDACTED_EMAIL]" in block_texts
