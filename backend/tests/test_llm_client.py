"""Tests for llm_client.py. Every provider except anthropic is a
guardrail-and-config stub, so those paths refuse cleanly or raise
NotImplementedError. anthropic has a real implementation, tested here
against a fake client (types.SimpleNamespace stand-ins for the SDK's
response objects) -- no network access happens in any of these tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.llm_client as llm_client
from app.llm_client import (
    LLMClient,
    LLMClientError,
    LLMConfig,
    ProviderNotConfiguredError,
    PseudonymisationRequiredError,
    SyntheticOnlyViolationError,
    TEMPERATURE,
    TIMEOUT_SECONDS,
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


class _FakeAnthropicMessages:
    def __init__(self, response):
        self._response = response
        self.received_kwargs: dict | None = None

    def create(self, **kwargs):
        self.received_kwargs = kwargs
        return self._response


class _FakeAnthropicClient:
    """Stands in for anthropic.Anthropic — records constructor kwargs so
    tests can assert on api_key/timeout/max_retries without any network
    access, and returns a canned response from .messages.create()."""

    def __init__(self, response=None, **kwargs):
        self.init_kwargs = kwargs
        self.messages = _FakeAnthropicMessages(response)


def _tool_use_response(findings) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record_findings", input={"findings": findings})]
    )


def test_anthropic_analyze_returns_findings_from_tool_use(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    canned_findings = [
        {
            "category": "missing_clause",
            "severity": "medium",
            "block_ids": [],
            "evidence_quote": "",
            "explanation": "No termination clause found.",
        }
    ]
    monkeypatch.setattr(
        llm_client.anthropic,
        "Anthropic",
        lambda **kwargs: _FakeAnthropicClient(response=_tool_use_response(canned_findings), **kwargs),
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    result = client.analyze([], pseudonymised=True, is_synthetic=False)

    assert result == canned_findings


def test_anthropic_analyze_uses_expected_call_params(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    created = {}

    def _fake_anthropic(**init_kwargs):
        instance = _FakeAnthropicClient(response=_tool_use_response([]), **init_kwargs)
        created["instance"] = instance
        return instance

    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _fake_anthropic)

    client = LLMClient(_config(provider="anthropic", model="claude-haiku", synthetic_only=False))
    client.analyze([], pseudonymised=True, is_synthetic=False)

    instance = created["instance"]
    assert instance.init_kwargs["api_key"] == "fake-key-for-test"
    assert instance.init_kwargs["timeout"] == TIMEOUT_SECONDS
    assert instance.init_kwargs["max_retries"] == llm_client.ANTHROPIC_MAX_RETRIES

    call_kwargs = instance.messages.received_kwargs
    assert call_kwargs["model"] == "claude-haiku"
    assert call_kwargs["temperature"] == TEMPERATURE
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "record_findings"}


def test_anthropic_analyze_raises_on_missing_tool_use_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    empty_response = SimpleNamespace(content=[])
    monkeypatch.setattr(
        llm_client.anthropic, "Anthropic", lambda **kwargs: _FakeAnthropicClient(response=empty_response)
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    with pytest.raises(LLMClientError) as exc_info:
        client.analyze([], pseudonymised=True, is_synthetic=False)
    assert exc_info.value.category == "provider_response_invalid"


def test_anthropic_analyze_raises_on_non_list_findings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    bad_response = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="record_findings", input={"findings": "not-a-list"})]
    )
    monkeypatch.setattr(
        llm_client.anthropic, "Anthropic", lambda **kwargs: _FakeAnthropicClient(response=bad_response)
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    with pytest.raises(LLMClientError) as exc_info:
        client.analyze([], pseudonymised=True, is_synthetic=False)
    assert exc_info.value.category == "provider_response_invalid"
