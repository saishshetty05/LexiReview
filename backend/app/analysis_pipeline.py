"""analysis_pipeline.py — composes extraction, PII redaction, anchors, the
LLM client, and quote verification into one function that turns raw document
bytes into CONTRACTS.md §2 finding objects.

Storage retrieval (doc_id -> bytes) is still open (worker.py isn't wired to
call this yet) — this function takes bytes directly and an already-
constructed LLMClient, so it's fully testable now and becomes the single
seam worker.py needs once that wiring lands, including the is_synthetic flag
(CONTRACTS.md §1a), which the caller resolves off the documents row.
"""

from __future__ import annotations

from app.anchors import make_anchors
from app.extraction import extract_text
from app.llm_client import REQUIRED_NO_ISSUES_PHRASE, LLMClient
from app.pii_gateway import pseudonymise
from app.verifier import verify_quote

_RISK_SEVERITIES = ("high", "medium", "low", "info")


def run_analysis(
    file_bytes: bytes,
    file_type: str,
    llm_client: LLMClient,
    *,
    is_synthetic: bool,
) -> list[dict]:
    """Extract, redact, anchor, analyze, and verify.

    Returns a list of finding dicts matching CONTRACTS.md §2, each with
    `verification`/`confidence` filled in.
    """
    extraction = extract_text(file_bytes, file_type)
    pseudonymised = pseudonymise(extraction.text)
    blocks = make_anchors(pseudonymised.text)

    raw_findings = llm_client.analyze(blocks, pseudonymised=True, is_synthetic=is_synthetic)
    return [_finalize_finding(pseudonymised.text, finding) for finding in raw_findings]


def run_summary(
    file_bytes: bytes,
    file_type: str,
    llm_client: LLMClient,
    findings: list[dict],
    *,
    is_synthetic: bool,
) -> dict:
    """Extract, redact, anchor, and summarize. Returns a payload dict
    matching CONTRACTS.md §2c: `overview`/`key_terms` come from the LLM,
    `risk_snapshot` is computed here from `findings` (already produced by
    run_analysis for the same job) -- never LLM-authored, so the summary
    can never disagree with the findings that back it.

    Re-extracts/re-anchors rather than reusing run_analysis's intermediate
    blocks: this keeps run_summary a standalone, independently testable
    function taking the same "bytes + LLMClient" seam as run_analysis, at
    the cost of redoing deterministic extraction work worker.py's caller
    already paid for once.
    """
    extraction = extract_text(file_bytes, file_type)
    pseudonymised = pseudonymise(extraction.text)
    blocks = make_anchors(pseudonymised.text)

    raw_summary = llm_client.summarize(blocks, pseudonymised=True, is_synthetic=is_synthetic)
    risk_snapshot = _compute_risk_snapshot(findings)

    overview = raw_summary["overview"]
    if not any(risk_snapshot.values()):
        # CONTRACTS.md §2c: deterministic override, not a hope the model
        # phrased it correctly -- rule 7 can't depend on the LLM behaving,
        # the same principle _finalize_finding applies to verification.
        overview = REQUIRED_NO_ISSUES_PHRASE

    return {
        "overview": overview,
        "key_terms": raw_summary["key_terms"],
        "risk_snapshot": risk_snapshot,
    }


def _compute_risk_snapshot(findings: list[dict]) -> dict:
    snapshot = {severity: 0 for severity in _RISK_SEVERITIES}
    for finding in findings:
        severity = finding.get("severity")
        if severity in snapshot:
            snapshot[severity] += 1
    return snapshot


def _finalize_finding(source_text: str, finding: dict) -> dict:
    finding = dict(finding)

    if finding["category"] == "missing_clause":
        # CONTRACTS.md §2: no quote to check — the playbook/category check
        # that produces this finding is itself deterministic. evidence_quote
        # and block_ids are forced to "" / [] regardless of what the model
        # returned: the system prompt asks for this, but nothing validates
        # provider output against CONTRACTS.md, and in practice the model has
        # filled evidence_quote with placeholder text or the entire document
        # instead — the latter is also a likely contributor to intermittent
        # ANTHROPIC_MAX_TOKENS truncation (large quotes repeated across
        # several missing_clause findings in one response). Same "one layer
        # that can lie is enough" principle already applied to risk_snapshot
        # and the rule-7 override — the LLM doesn't own this field.
        finding["evidence_quote"] = ""
        finding["block_ids"] = []
        finding["verification"] = "verified"
        finding["confidence"] = "standard"
        return finding

    result = verify_quote(source_text, finding["evidence_quote"])
    finding["verification"] = result.verification
    # CONTRACTS.md §2's other needs_review trigger — entailment-pass scoring
    # — isn't implemented anywhere yet, so confidence here reflects
    # verification only, not entailment.
    finding["confidence"] = "standard" if result.verification == "verified" else "needs_review"
    return finding
