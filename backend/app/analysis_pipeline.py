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

import anthropic

from app.anchors import make_anchors
from app.extraction import extract_text
from app.llm_client import REQUIRED_NO_ISSUES_PHRASE, LLMClient, LLMClientError
from app.pii_gateway import pseudonymise
from app.verifier import verify_quote

_RISK_SEVERITIES = ("high", "medium", "low", "info")

# PRD AI-6: High/Medium severity findings get the entailment second pass;
# so does every inconsistency finding regardless of severity (checked
# separately in _entailment_eligible). Low/Info non-inconsistency findings
# rely on quote verification alone, per the PRD.
_ENTAILMENT_ELIGIBLE_SEVERITIES = ("high", "medium")

# A failed/errored entailment call must degrade this one finding's
# confidence, not fail or retry the whole analysis job -- the same
# "annotate, never fail the job" principle worker.py's _execute_summary
# already applies at the job level, here applied per-finding. Deliberately
# broader than worker.py's _TRANSIENT_LLM_ERRORS (which decides job-level
# retryability): every LLMClientError and every anthropic.APIError subclass
# is treated the same way here, since there is no job-level retry decision
# to make -- the finding is returned either way, just with confidence
# reflecting that entailment support could not be confirmed.
_ENTAILMENT_CHECK_ERRORS = (LLMClientError, anthropic.APIError)


def run_analysis(
    file_bytes: bytes,
    file_type: str,
    llm_client: LLMClient,
    *,
    is_synthetic: bool,
    severity_examples: list[dict] | None = None,
) -> list[dict]:
    """Extract, redact, anchor, analyze, and verify.

    Returns a list of finding dicts matching CONTRACTS.md §2, each with
    `verification`/`confidence` filled in.

    severity_examples (CONTRACTS.md §7a, v1.8): this user's past severity
    overrides, fetched by the caller (worker.py, which has DB access) and
    threaded straight through to LLMClient.analyze as few-shot guidance --
    keeps this function DB-free and testable with a fake LLM client, same
    seam philosophy as the rest of this module.
    """
    extraction = extract_text(file_bytes, file_type)
    pseudonymised = pseudonymise(extraction.text)
    blocks = make_anchors(pseudonymised.text)
    block_by_id = {block.id: block.text for block in blocks}

    raw_findings = llm_client.analyze(
        blocks, pseudonymised=True, is_synthetic=is_synthetic, severity_examples=severity_examples
    )
    return [
        _finalize_finding(
            pseudonymised.text,
            finding,
            llm_client=llm_client,
            is_synthetic=is_synthetic,
            block_by_id=block_by_id,
        )
        for finding in raw_findings
    ]


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


def _finalize_finding(
    source_text: str,
    finding: dict,
    *,
    llm_client: LLMClient,
    is_synthetic: bool,
    block_by_id: dict[str, str],
) -> dict:
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
    finding["confidence"] = "standard" if result.verification == "verified" else "needs_review"

    # PRD AI-6: the entailment second pass only has something to add when
    # the quote itself is genuinely in the document (verification=verified)
    # and the finding is High/Medium severity or an inconsistency -- running
    # it against an already-fabricated quote would just re-flag confidence
    # that's already "needs_review", wasting a call. It can only ever
    # downgrade confidence here, never upgrade past what verification set.
    if finding["confidence"] == "standard" and _entailment_eligible(finding):
        finding["confidence"] = _entailment_confidence(
            finding, llm_client=llm_client, is_synthetic=is_synthetic, block_by_id=block_by_id
        )

    return finding


def _entailment_eligible(finding: dict) -> bool:
    return (
        finding["category"] == "inconsistency"
        or finding["severity"] in _ENTAILMENT_ELIGIBLE_SEVERITIES
    )


def _entailment_confidence(
    finding: dict,
    *,
    llm_client: LLMClient,
    is_synthetic: bool,
    block_by_id: dict[str, str],
) -> str:
    block_texts = [block_by_id[bid] for bid in finding["block_ids"] if bid in block_by_id]
    if not block_texts:
        # Defensive: block_ids should always resolve to real blocks by this
        # point (the LLM was given the same block ids it must cite), but if
        # one doesn't, there is nothing to check entailment against --
        # leave confidence as verification already set it rather than guess.
        return finding["confidence"]

    try:
        result = llm_client.check_entailment(
            finding["explanation"], block_texts, pseudonymised=True, is_synthetic=is_synthetic
        )
    except _ENTAILMENT_CHECK_ERRORS:
        # Fail-safe, constitution rule 6's principle applied to entailment:
        # a check that couldn't complete is indistinguishable from a check
        # that failed, so both must not silently present as "standard"
        # confidence. Does NOT fail the job (worker.py's _execute_summary
        # precedent) -- the finding is still returned, just downgraded.
        return "needs_review"

    return "standard" if result.supported else "needs_review"
