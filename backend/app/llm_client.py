"""llm_client.py — provider-agnostic LLM client for the analysis lane.

STUB: enforces every guardrail from CLAUDE.md rule 3 and defines the request/
response shape, but does not call a real provider yet. The Anthropic Console
workspace + API keys are still an open item (docs/DECISION_LOG.md, owner B),
so a call that clears every guardrail raises ProviderNotConfiguredError
(no key set) or NotImplementedError (key set, but no provider wiring exists
yet) instead of making a network request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.anchors import Block

TEMPERATURE = 0
TIMEOUT_SECONDS = 180

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
        if not os.environ.get(api_key_env):
            raise ProviderNotConfiguredError(self.config.provider)

        raise NotImplementedError(
            f"LLMClient stub: {self.config.provider!r} guardrails passed but no provider "
            "call is wired up yet"
        )
