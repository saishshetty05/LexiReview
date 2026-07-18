"""llm_client.py — provider-agnostic LLM client for the analysis lane.

Enforces every guardrail from CLAUDE.md rule 3 before any provider dispatch.
`anthropic` has a real implementation (see _analyze_anthropic): temperature
0, 180s timeout, JSON-schema-constrained via forced tool use, retries via
the SDK's built-in exponential backoff (PRD NFR "Reliability" section --
no specific retry count is given there, so this matches the job-level
policy of 3 for consistency). Other providers (e.g. gemini_free) remain
STUBs: a call that clears every guardrail raises ProviderNotConfiguredError
(no key set) or NotImplementedError (key set, no provider wiring exists).
The Anthropic Console workspace + API keys are still an open item
(docs/DECISION_LOG.md, owner B).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import anthropic

from app.anchors import Block

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
    "block_ids must reference the [BLOCK_n] labels you were given."
)


def _blocks_to_prompt(blocks: list[Block]) -> str:
    return "\n\n".join(f"[{block.id}]\n{block.text}" for block in blocks)


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

    def analyze(
        self,
        blocks: list[Block],
        *,
        pseudonymised: bool,
        is_synthetic: bool,
    ) -> list[dict]:
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

        if self.config.provider == "anthropic":
            return self._analyze_anthropic(blocks, api_key)

        raise NotImplementedError(
            f"LLMClient stub: {self.config.provider!r} guardrails passed but no provider "
            "call is wired up yet"
        )

    def _analyze_anthropic(self, blocks: list[Block], api_key: str) -> list[dict]:
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
            temperature=TEMPERATURE,
            system=_SYSTEM_PROMPT,
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
