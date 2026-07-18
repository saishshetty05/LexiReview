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
from app.llm_client import LLMClient
from app.pii_gateway import pseudonymise
from app.verifier import verify_quote


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


def _finalize_finding(source_text: str, finding: dict) -> dict:
    finding = dict(finding)

    if finding["category"] == "missing_clause":
        # CONTRACTS.md §2: no quote to check — the playbook/category check
        # that produces this finding is itself deterministic.
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
