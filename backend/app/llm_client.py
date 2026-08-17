"""llm_client.py — provider-agnostic LLM client for the analysis lane.

Enforces every guardrail from CLAUDE.md rule 3 before any provider dispatch.
`anthropic` has a real implementation (see _analyze_anthropic): 180s
timeout, JSON-schema-constrained via forced tool use, retries via the SDK's
built-in exponential backoff (PRD NFR "Reliability" section -- no specific
retry count is given there, so this matches the job-level policy of 3 for
consistency). No temperature is sent -- deprecated for Claude models
released after Opus 4.6 (April 2026), which are deterministic-by-default
with no value specified; see docs/DECISION_LOG.md 2026-07-23. Other
providers (e.g. gemini_free) remain STUBs: a call that clears every
guardrail raises ProviderNotConfiguredError (no key set) or
NotImplementedError (key set, no provider wiring exists). The Anthropic
Console workspace + API keys are still an open item (docs/DECISION_LOG.md,
owner B).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import anthropic

from app.anchors import Block

# Deliberately unused in both _analyze_anthropic and _summarize_anthropic:
# `temperature` is deprecated for Claude models released after Opus 4.6
# (April 2026) and the API now rejects it (invalid_request_error). Kept
# defined, not deleted, as documentation of the intended semantic --
# newer models are deterministic-by-default with no value specified, which
# is the same behavior this constant used to make explicit. See
# docs/DECISION_LOG.md 2026-07-23.
TEMPERATURE = 0
TIMEOUT_SECONDS = 180
ANTHROPIC_MAX_RETRIES = 3
ANTHROPIC_MAX_TOKENS = 4096

# CONTRACTS.md §2 — one finding object's shape; constrains provider output
# once a real call is implemented.
FINDING_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [
                "termination",
                "liability",
                "indemnity",
                "payment",
                "confidentiality",
                "auto_renewal",
                "governing_law",
                "penalties",
                "intellectual_property",
                "arbitration",
                "non_compete",
                "force_majeure",
                "inconsistency",
                "missing_clause",
            ],
        },
        "severity": {"type": "string", "enum": ["high", "medium", "low", "info"]},
        "block_ids": {"type": "array", "items": {"type": "string"}},
        "evidence_quote": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["category", "severity", "block_ids", "evidence_quote", "explanation"],
}

# Wraps FINDING_JSON_SCHEMA as a forced tool call so Anthropic returns
# already-parsed JSON matching the contract, instead of free-text that would
# need a separate parse/validate step.
_RECORD_FINDINGS_TOOL = {
    "name": "record_findings",
    "description": "Record every finding identified in the document.",
    "input_schema": {
        "type": "object",
        "properties": {"findings": {"type": "array", "items": FINDING_JSON_SCHEMA}},
        "required": ["findings"],
    },
}

_SYSTEM_PROMPT = (
    "You are a contract-review assistant. You are given a legal document "
    "split into numbered blocks. Identify clauses that are risky, "
    "inconsistent with another clause, or missing entirely, and report them "
    "via the record_findings tool only -- do not respond in plain text. "
    "Every evidence_quote must be copied VERBATIM from the block text you "
    "were given: do not paraphrase, fix typos, or change whitespace. "
    "block_ids must reference the [BLOCK_n] labels you were given. "
    "For category='missing_clause', there is nothing to quote or point at -- "
    "the finding is about an absence, not a specific span of text. "
    "evidence_quote MUST be the empty string \"\" and block_ids MUST be an "
    "empty array []. Do not quote surrounding blocks, do not write a "
    "placeholder like 'N/A', and do not summarize the document as the "
    "quote -- leave both fields empty exactly as specified. "
    "Before declaring two clauses inconsistent, check whether either clause "
    "contains subordination or priority language referring to the other -- "
    "phrases such as 'subject to', 'notwithstanding', 'except as provided "
    "in', 'save as otherwise', or 'without prejudice to'. Such language CAN "
    "mean the clauses are deliberately coordinated, not in conflict, even "
    "if they appear to say different things in isolation -- but only if the "
    "referenced clause actually resolves the tension. Standard coexistence "
    "patterns: a jurisdiction/courts clause made 'subject to' an "
    "arbitration clause (courts retain narrow supervisory jurisdiction -- "
    "e.g. interim relief or enforcement -- while arbitration is the primary "
    "dispute-resolution forum); a fixed-term clause alongside a separate "
    "early-termination right (the term is the default, the right is a "
    "narrow, deliberate exception); a general rule stated alongside a "
    "narrow carve-out for specific circumstances. Do not treat 'subject to "
    "Clause X' or similar phrases as resolving the conflict unless the "
    "referenced clause (Clause X, or its equivalent) actually contains a "
    "carve-out, override, or coordinating provision that addresses the "
    "specific tension between the two clauses. A subordinating phrase "
    "pointing at a clause that doesn't materially resolve the conflict is "
    "itself a red flag -- report as HIGH inconsistency. For example, if "
    "Clause 1 says 'Party A shall pay X, subject to Clause 22' and Clause "
    "22 does not condition or override the payment obligation in any way, "
    "this is not deliberate coordination -- the 'subject to' phrase is "
    "being used as camouflage. Report as HIGH inconsistency. Only when you "
    "confirm the referenced clause genuinely resolves the tension should "
    "you report the pair at LOW severity instead of HIGH, with an "
    "explanation of why the clauses coordinate rather than conflict. "
    "Reserve HIGH-severity inconsistency findings for genuine, unresolved "
    "conflicts -- clauses that contradict each other with no subordinating "
    "language connecting them, or where a subordinating phrase is present "
    "but does not actually resolve anything."
)

# CONTRACTS.md §2c — document_summaries.payload shape (partial: overview +
# key_terms come from the provider; risk_snapshot is computed deterministically
# in analysis_pipeline.py from the findings run_analysis already produced,
# never LLM-authored).
SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "key_terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["label", "detail"],
            },
        },
    },
    "required": ["overview", "key_terms"],
}

_RECORD_SUMMARY_TOOL = {
    "name": "record_summary",
    "description": "Record the document's plain-language overview and key terms.",
    "input_schema": SUMMARY_JSON_SCHEMA,
}

_SUMMARY_SYSTEM_PROMPT = (
    "You are a contract-review assistant. You are given a legal document "
    "split into numbered blocks. Write a 2-4 sentence plain-language "
    "overview of what the document is and what it covers, and list its key "
    "terms (e.g. notice period, payment terms, governing law) via the "
    "record_summary tool only -- do not respond in plain text. "
    "You must NEVER characterize the document, or any part of it, as safe, "
    "risk-free, clean, or free of concerns -- that judgment belongs to a "
    "human reviewer, not you."
)

# CONTRACTS.md §2c rule-7 guardrail: case-insensitive substring match, same
# pattern-matching approach as pii_gateway.py. Checked against the provider's
# overview text before it is ever stored.
_FORBIDDEN_SAFETY_PHRASES = (
    "safe",
    "risk-free",
    "riskfree",
    "clean",
    "no concerns",
    "no risk",
    "worry-free",
    "nothing to worry about",
)

# CONTRACTS.md §2c: the exact phrase analysis_pipeline.py substitutes for
# `overview` when the deterministic risk_snapshot is entirely zero -- not a
# paraphrase, and not something the provider is trusted to phrase correctly
# on its own.
REQUIRED_NO_ISSUES_PHRASE = "no issues detected by automated review"


def _contains_forbidden_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _FORBIDDEN_SAFETY_PHRASES)


def _blocks_to_prompt(blocks: list[Block]) -> str:
    return "\n\n".join(f"[{block.id}]\n{block.text}" for block in blocks)


# CONTRACTS.md §7a (v1.8) severity personalization: prompt-based, not
# embeddings -- see docs/DECISION_LOG.md 2026-08-17. Advisory only, no
# deterministic post-processing enforces a match (unlike _finalize_finding's
# handling of missing_clause/verification in analysis_pipeline.py): matching
# "same meaning, different wording" needs the same judgment the override
# exists to correct in the first place, so there's no cheap deterministic
# check available here.
_SEVERITY_PERSONALIZATION_PREAMBLE = (
    "This reviewer has previously corrected the severity of findings on "
    "their own past documents. When a clause in THIS document carries the "
    "same substantive meaning as one of the examples below -- even if "
    "worded completely differently, since contracts rarely repeat a clause "
    "verbatim -- classify it at the reviewer's corrected severity instead "
    "of whatever you would otherwise assign. Do not apply a correction to "
    "a clause that is only superficially similar (same category, different "
    "substance)."
)


def _build_system_prompt(severity_examples: list[dict] | None) -> str:
    if not severity_examples:
        return _SYSTEM_PROMPT
    example_lines = []
    for example in severity_examples:
        quote = example["evidence_quote"] or "(no quote -- missing_clause finding)"
        example_lines.append(
            f"- category={example['category']!r}: originally rated "
            f"{example['original_severity']!r}, reviewer corrected to "
            f"{example['severity_override']!r}. Example clause: {quote!r}"
        )
    return "\n\n".join([_SYSTEM_PROMPT, _SEVERITY_PERSONALIZATION_PREAMBLE, *example_lines])


# Providers whose free tier may use submitted data for training (CLAUDE.md
# rule 3): SYNTHETIC_ONLY must be enforced for these, never for paid tiers.
_FREE_TIER_PROVIDERS = {"gemini_free"}

_API_KEY_ENV_BY_PROVIDER = {
    "gemini_free": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class LLMClientError(Exception):
    """Base for every refusal/failure raised by LLMClient. category+message
    only — never document content (CLAUDE.md rule 2)."""

    def __init__(self, category: str, message: str) -> None:
        self.category = category
        self.message = message
        super().__init__(f"{category}: {message}")


class PseudonymisationRequiredError(LLMClientError):
    def __init__(self) -> None:
        super().__init__("pseudonymisation_required", "payload is not flagged pseudonymised")


class SyntheticOnlyViolationError(LLMClientError):
    def __init__(self, provider: str) -> None:
        super().__init__(
            "synthetic_only_violation",
            f"provider {provider!r} is a free tier restricted to synthetic documents "
            "(SYNTHETIC_ONLY=true)",
        )


class ProviderNotConfiguredError(LLMClientError):
    def __init__(self, provider: str) -> None:
        super().__init__(
            "provider_not_configured", f"no API key configured for provider {provider!r}"
        )


class ProviderResponseError(LLMClientError):
    """The provider replied, but not in the shape record_findings requires.
    Message is a fixed, generic string -- never the response body itself
    (CLAUDE.md rule 2: response content, including on error paths, is never
    logged or otherwise surfaced)."""

    def __init__(self, detail: str) -> None:
        super().__init__("provider_response_invalid", detail)


class SummaryGuardrailViolationError(LLMClientError):
    """CONTRACTS.md §2c / CLAUDE.md rule 7: the model characterized the
    document as safe/risk-free/clean/etc, which is not its call to make.
    Category + a fixed message only -- never the offending text itself
    (rule 2 applies to guardrail refusals the same as any other error)."""

    def __init__(self) -> None:
        super().__init__(
            "summary_guardrail_violation",
            "summary overview used forbidden safety-characterizing language",
        )


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    synthetic_only: bool

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = os.environ.get("LLM_PROVIDER", "")
        if not provider:
            raise LLMClientError("provider_not_set", "LLM_PROVIDER is not set")
        model = os.environ.get("LLM_MODEL", "")
        if not model:
            raise LLMClientError("model_not_set", "LLM_MODEL is not set")
        synthetic_only = os.environ.get("SYNTHETIC_ONLY", "true").strip().lower() == "true"
        return cls(provider=provider, model=model, synthetic_only=synthetic_only)


class LLMClient:
    """Provider-agnostic entry point for clause analysis.

    Guardrails run in a fixed order, before any provider dispatch:
    pseudonymisation first, then synthetic-only. Both are safety checks that
    must hold regardless of whether a provider is even configured yet.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()

    def _resolve_api_key(self, *, pseudonymised: bool, is_synthetic: bool) -> str:
        """Runs every CLAUDE.md rule 3 guardrail, in fixed order, and returns
        the api_key to dispatch with. Shared by analyze() and summarize() --
        both are provider calls and both must clear the same guardrails
        regardless of what they ask the provider to do."""
        if not pseudonymised:
            raise PseudonymisationRequiredError()

        if (
            self.config.provider in _FREE_TIER_PROVIDERS
            and self.config.synthetic_only
            and not is_synthetic
        ):
            raise SyntheticOnlyViolationError(self.config.provider)

        api_key_env = _API_KEY_ENV_BY_PROVIDER.get(self.config.provider)
        if api_key_env is None:
            raise LLMClientError(
                "unknown_provider", f"no client implementation for provider {self.config.provider!r}"
            )
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ProviderNotConfiguredError(self.config.provider)
        return api_key

    def analyze(
        self,
        blocks: list[Block],
        *,
        pseudonymised: bool,
        is_synthetic: bool,
        severity_examples: list[dict] | None = None,
    ) -> list[dict]:
        api_key = self._resolve_api_key(pseudonymised=pseudonymised, is_synthetic=is_synthetic)

        if self.config.provider == "anthropic":
            return self._analyze_anthropic(blocks, api_key, severity_examples=severity_examples)

        raise NotImplementedError(
            f"LLMClient stub: {self.config.provider!r} guardrails passed but no provider "
            "call is wired up yet"
        )

    def summarize(
        self,
        blocks: list[Block],
        *,
        pseudonymised: bool,
        is_synthetic: bool,
    ) -> dict:
        """Returns {"overview": str, "key_terms": [{"label", "detail"}]} --
        the LLM-authored part of CONTRACTS.md §2c's payload. risk_snapshot is
        not this method's concern; analysis_pipeline.run_summary computes it
        deterministically from findings and owns the rule-7 override."""
        api_key = self._resolve_api_key(pseudonymised=pseudonymised, is_synthetic=is_synthetic)

        if self.config.provider == "anthropic":
            return self._summarize_anthropic(blocks, api_key)

        raise NotImplementedError(
            f"LLMClient stub: {self.config.provider!r} guardrails passed but no provider "
            "call is wired up yet"
        )

    def _analyze_anthropic(
        self, blocks: list[Block], api_key: str, *, severity_examples: list[dict] | None = None
    ) -> list[dict]:
        client = anthropic.Anthropic(
            api_key=api_key,
            timeout=TIMEOUT_SECONDS,
            # SDK-native exponential backoff on transient errors (connection,
            # timeout, 429, 5xx) -- see module docstring for why this isn't a
            # hand-rolled retry loop.
            max_retries=ANTHROPIC_MAX_RETRIES,
        )
        response = client.messages.create(
            model=self.config.model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            # temperature is deprecated for Claude models released after
            # Opus 4.6 (April 2026) -- newer models are deterministic-by-
            # default with no value specified. See docs/DECISION_LOG.md
            # 2026-07-23.
            system=_build_system_prompt(severity_examples),
            tools=[_RECORD_FINDINGS_TOOL],
            tool_choice={"type": "tool", "name": "record_findings"},
            messages=[{"role": "user", "content": _blocks_to_prompt(blocks)}],
        )

        for content_block in response.content:
            if content_block.type == "tool_use" and content_block.name == "record_findings":
                findings = content_block.input.get("findings")
                if not isinstance(findings, list):
                    raise ProviderResponseError("record_findings.findings was not a list")
                return findings

        raise ProviderResponseError("no record_findings tool_use block in provider response")

    def _summarize_anthropic(self, blocks: list[Block], api_key: str) -> dict:
        client = anthropic.Anthropic(
            api_key=api_key,
            timeout=TIMEOUT_SECONDS,
            max_retries=ANTHROPIC_MAX_RETRIES,
        )
        response = client.messages.create(
            model=self.config.model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            # temperature is deprecated for Claude models released after
            # Opus 4.6 (April 2026) -- newer models are deterministic-by-
            # default with no value specified. See docs/DECISION_LOG.md
            # 2026-07-23.
            system=_SUMMARY_SYSTEM_PROMPT,
            tools=[_RECORD_SUMMARY_TOOL],
            tool_choice={"type": "tool", "name": "record_summary"},
            messages=[{"role": "user", "content": _blocks_to_prompt(blocks)}],
        )

        for content_block in response.content:
            if content_block.type == "tool_use" and content_block.name == "record_summary":
                overview = content_block.input.get("overview")
                key_terms = content_block.input.get("key_terms")
                if not isinstance(overview, str) or not isinstance(key_terms, list):
                    raise ProviderResponseError("record_summary input did not match the expected shape")
                if _contains_forbidden_phrase(overview):
                    raise SummaryGuardrailViolationError()
                return {"overview": overview, "key_terms": key_terms}

        raise ProviderResponseError("no record_summary tool_use block in provider response")
