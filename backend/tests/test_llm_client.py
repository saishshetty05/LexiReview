"""Tests for llm_client.py. This is a guardrail-and-config stub — no real
provider call exists yet, so every path either refuses cleanly or raises
NotImplementedError. No network access happens in these tests."""

from __future__ import annotations

import pytest

from app.llm_client import (
    LLMClient,
    LLMClientError,
    LLMConfig,
    ProviderNotConfiguredError,
    PseudonymisationRequiredError,
    SyntheticOnlyViolationError,
)


def _config(**overrides) -> LLMConfig:
    defaults = {"provider": "gemini_free", "model": "gemini-flash", "synthetic_only": True}
    defaults.update(overrides)
    return LLMConfig(**defaults)


def test_unpseudonymised_payload_always_refused():
    client = LLMClient(_config())
    with pytest.raises(PseudonymisationRequiredError) as exc_info:
        client.analyze([], pseudonymised=False, is_synthetic=True)
    assert exc_info.value.category == "pseudonymisation_required"


def test_pseudonymisation_check_runs_before_synthetic_check():
    """Both guardrails would fail here; pseudonymisation must win (it's the
    more fundamental check and must never be shadowed by a different error)."""
    client = LLMClient(_config(synthetic_only=True))
    with pytest.raises(PseudonymisationRequiredError):
        client.analyze([], pseudonymised=False, is_synthetic=False)


def test_free_tier_provider_refuses_non_synthetic_when_synthetic_only():
    client = LLMClient(_config(provider="gemini_free", synthetic_only=True))
    with pytest.raises(SyntheticOnlyViolationError) as exc_info:
        client.analyze([], pseudonymised=True, is_synthetic=False)
    assert exc_info.value.category == "synthetic_only_violation"


def test_free_tier_provider_allows_synthetic_docs(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = LLMClient(_config(provider="gemini_free", synthetic_only=True))
    with pytest.raises(ProviderNotConfiguredError):
        client.analyze([], pseudonymised=True, is_synthetic=True)


def test_paid_provider_not_restricted_to_synthetic_docs(monkeypatch):
    """anthropic is a paid, no-training tier — SYNTHETIC_ONLY must not apply
    to it even when the flag is true (CLAUDE.md rule 3 only restricts free
    tiers)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = LLMClient(_config(provider="anthropic", synthetic_only=True))
    with pytest.raises(ProviderNotConfiguredError):
        client.analyze([], pseudonymised=True, is_synthetic=False)


def test_unknown_provider_rejected():
    client = LLMClient(_config(provider="some_new_provider"))
    with pytest.raises(LLMClientError) as exc_info:
        client.analyze([], pseudonymised=True, is_synthetic=True)
    assert exc_info.value.category == "unknown_provider"


def test_missing_api_key_raises_not_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = LLMClient(_config(provider="gemini_free"))
    with pytest.raises(ProviderNotConfiguredError) as exc_info:
        client.analyze([], pseudonymised=True, is_synthetic=True)
    assert exc_info.value.category == "provider_not_configured"


def test_configured_provider_raises_not_implemented_stub(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-test")
    client = LLMClient(_config(provider="gemini_free"))
    with pytest.raises(NotImplementedError):
        client.analyze([], pseudonymised=True, is_synthetic=True)


def test_config_from_env_requires_provider(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_MODEL", "gemini-flash")
    with pytest.raises(LLMClientError) as exc_info:
        LLMConfig.from_env()
    assert exc_info.value.category == "provider_not_set"


def test_config_from_env_requires_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini_free")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(LLMClientError) as exc_info:
        LLMConfig.from_env()
    assert exc_info.value.category == "model_not_set"


@pytest.mark.parametrize(
    "raw_value,expected",
    [("true", True), ("True", True), ("false", False), ("", False)],
)
def test_config_from_env_parses_synthetic_only(monkeypatch, raw_value, expected):
    monkeypatch.setenv("LLM_PROVIDER", "gemini_free")
    monkeypatch.setenv("LLM_MODEL", "gemini-flash")
    monkeypatch.setenv("SYNTHETIC_ONLY", raw_value)
    config = LLMConfig.from_env()
    assert config.synthetic_only is expected
