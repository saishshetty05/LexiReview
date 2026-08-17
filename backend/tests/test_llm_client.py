"""Tests for llm_client.py. Every provider except anthropic is a
guardrail-and-config stub, so those paths refuse cleanly or raise
NotImplementedError. anthropic has a real implementation, tested here
against a fake client (types.SimpleNamespace stand-ins for the SDK's
response objects) -- no network access happens in any of these tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.llm_client as llm_client
from app.anchors import Block
from app.llm_client import (
    LLMClient,
    LLMClientError,
    LLMConfig,
    ProviderNotConfiguredError,
    PseudonymisationRequiredError,
    SummaryGuardrailViolationError,
    SyntheticOnlyViolationError,
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
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "record_findings"}
    # temperature is deprecated for Claude models released after Opus 4.6
    # (April 2026) -- see docs/DECISION_LOG.md 2026-07-23 and the dedicated
    # regression test below.
    assert "temperature" not in call_kwargs


def test_anthropic_analyze_system_prompt_unchanged_with_no_severity_examples(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    created = {}

    def _fake_anthropic(**init_kwargs):
        instance = _FakeAnthropicClient(response=_tool_use_response([]), **init_kwargs)
        created["instance"] = instance
        return instance

    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _fake_anthropic)

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    client.analyze([], pseudonymised=True, is_synthetic=False)

    assert created["instance"].messages.received_kwargs["system"] == llm_client._SYSTEM_PROMPT


def test_anthropic_analyze_system_prompt_includes_severity_examples(monkeypatch):
    """CONTRACTS.md §7a (v1.8): past overrides are appended to the system
    prompt as few-shot guidance, prompt-based rather than embeddings-based
    (docs/DECISION_LOG.md 2026-08-17)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    created = {}

    def _fake_anthropic(**init_kwargs):
        instance = _FakeAnthropicClient(response=_tool_use_response([]), **init_kwargs)
        created["instance"] = instance
        return instance

    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _fake_anthropic)

    examples = [
        {
            "category": "payment",
            "evidence_quote": "a prior clause about late fees",
            "original_severity": "high",
            "severity_override": "medium",
        }
    ]
    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    client.analyze([], pseudonymised=True, is_synthetic=False, severity_examples=examples)

    system_prompt = created["instance"].messages.received_kwargs["system"]
    assert system_prompt.startswith(llm_client._SYSTEM_PROMPT)
    assert "a prior clause about late fees" in system_prompt
    assert "'payment'" in system_prompt
    assert "'high'" in system_prompt and "'medium'" in system_prompt


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


# ── summarize() ──────────────────────────────────────────────────────────
# Guardrails (pseudonymisation, synthetic-only, provider config) are shared
# with analyze() via LLMClient._resolve_api_key -- these tests confirm
# summarize() actually runs that shared path, not that the guardrails work
# (already covered above).


def test_summarize_unpseudonymised_payload_always_refused():
    client = LLMClient(_config())
    with pytest.raises(PseudonymisationRequiredError):
        client.summarize([], pseudonymised=False, is_synthetic=True)


def test_summarize_free_tier_provider_refuses_non_synthetic_when_synthetic_only():
    client = LLMClient(_config(provider="gemini_free", synthetic_only=True))
    with pytest.raises(SyntheticOnlyViolationError):
        client.summarize([], pseudonymised=True, is_synthetic=False)


def test_summarize_missing_api_key_raises_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    with pytest.raises(ProviderNotConfiguredError):
        client.summarize([], pseudonymised=True, is_synthetic=False)


def _summary_tool_use_response(overview: str, key_terms: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                name="record_summary",
                input={"overview": overview, "key_terms": key_terms},
            )
        ]
    )


def test_anthropic_summarize_returns_overview_and_key_terms(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    key_terms = [{"label": "notice_period", "detail": "30 days written notice required"}]
    monkeypatch.setattr(
        llm_client.anthropic,
        "Anthropic",
        lambda **kwargs: _FakeAnthropicClient(
            response=_summary_tool_use_response("A lease agreement between two parties.", key_terms),
            **kwargs,
        ),
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    result = client.summarize([], pseudonymised=True, is_synthetic=False)

    assert result == {"overview": "A lease agreement between two parties.", "key_terms": key_terms}


def test_anthropic_summarize_uses_expected_call_params(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    created = {}

    def _fake_anthropic(**init_kwargs):
        instance = _FakeAnthropicClient(
            response=_summary_tool_use_response("overview", []), **init_kwargs
        )
        created["instance"] = instance
        return instance

    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _fake_anthropic)

    client = LLMClient(_config(provider="anthropic", model="claude-haiku", synthetic_only=False))
    client.summarize([], pseudonymised=True, is_synthetic=False)

    call_kwargs = created["instance"].messages.received_kwargs
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "record_summary"}
    # temperature is deprecated for Claude models released after Opus 4.6
    # (April 2026) -- see docs/DECISION_LOG.md 2026-07-23 and the dedicated
    # regression test below.
    assert "temperature" not in call_kwargs


def test_anthropic_summarize_raises_on_missing_tool_use_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    empty_response = SimpleNamespace(content=[])
    monkeypatch.setattr(
        llm_client.anthropic, "Anthropic", lambda **kwargs: _FakeAnthropicClient(response=empty_response)
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    with pytest.raises(LLMClientError) as exc_info:
        client.summarize([], pseudonymised=True, is_synthetic=False)
    assert exc_info.value.category == "provider_response_invalid"


def test_anthropic_summarize_raises_on_malformed_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    bad_response = SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", name="record_summary", input={"overview": 123, "key_terms": []})
        ]
    )
    monkeypatch.setattr(
        llm_client.anthropic, "Anthropic", lambda **kwargs: _FakeAnthropicClient(response=bad_response)
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    with pytest.raises(LLMClientError) as exc_info:
        client.summarize([], pseudonymised=True, is_synthetic=False)
    assert exc_info.value.category == "provider_response_invalid"


@pytest.mark.parametrize(
    "overview",
    [
        "This document is completely safe to sign.",
        "The contract is risk-free for both parties.",
        "Overall this is a clean agreement with no concerns.",
    ],
)
def test_anthropic_summarize_rejects_forbidden_safety_language(monkeypatch, overview):
    """CONTRACTS.md §2c / CLAUDE.md rule 7: the model must never characterize
    the document as safe/risk-free/clean, even if it decides to on its own --
    this is the defense-in-depth check behind the system-prompt instruction."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(
        llm_client.anthropic,
        "Anthropic",
        lambda **kwargs: _FakeAnthropicClient(response=_summary_tool_use_response(overview, [])),
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    with pytest.raises(SummaryGuardrailViolationError) as exc_info:
        client.summarize([], pseudonymised=True, is_synthetic=False)
    assert exc_info.value.category == "summary_guardrail_violation"


def test_temperature_never_sent_to_anthropic(monkeypatch):
    """Regression guard for the temperature deprecation: Anthropic rejects
    `temperature` outright (invalid_request_error) for Claude models
    released after Opus 4.6 (April 2026) -- see docs/DECISION_LOG.md
    2026-07-23. Confirmed live during upload testing: extraction and
    pseudonymisation completed, and the API 400'd at the analyze() call.
    Covers both analyze() and summarize() since each calls
    client.messages.create() independently."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    captured = {}

    def _capture_analyze(**init_kwargs):
        instance = _FakeAnthropicClient(response=_tool_use_response([]), **init_kwargs)
        captured["analyze"] = instance
        return instance

    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _capture_analyze)
    client.analyze([], pseudonymised=True, is_synthetic=False)
    assert "temperature" not in captured["analyze"].messages.received_kwargs

    def _capture_summarize(**init_kwargs):
        instance = _FakeAnthropicClient(response=_summary_tool_use_response("x", []), **init_kwargs)
        captured["summarize"] = instance
        return instance

    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _capture_summarize)
    client.summarize([], pseudonymised=True, is_synthetic=False)
    assert "temperature" not in captured["summarize"].messages.received_kwargs


def test_subordination_language_included_in_system_prompt():
    """The only thing a mocked unit test can verify for this prompt-only
    fix: the guidance text is actually present in what gets sent. Catches
    an accidental revert/removal, not live-model compliance (that needs a
    live-model eval, out of scope here)."""
    prompt_lower = llm_client._SYSTEM_PROMPT.lower()
    assert "subject to" in prompt_lower
    assert "notwithstanding" in prompt_lower
    assert "arbitration" in prompt_lower


def test_missing_clause_empty_quote_instruction_in_system_prompt():
    """Found live: missing_clause findings came back with a non-empty
    evidence_quote (placeholder text, or the entire document) and non-empty
    block_ids, violating CONTRACTS.md §2 -- the prompt never actually told
    the model not to. analysis_pipeline._finalize_finding now normalizes
    this deterministically regardless (see test_analysis_pipeline.py), but
    the prompt fix is what stops the model from generating oversized
    quotes in the first place, which also looked like a contributor to
    intermittent ANTHROPIC_MAX_TOKENS truncation. Same caveat as the
    subordination-language test above: catches a revert, not live-model
    compliance."""
    prompt_lower = llm_client._SYSTEM_PROMPT.lower()
    assert "missing_clause" in prompt_lower
    assert "empty string" in prompt_lower
    assert "empty array" in prompt_lower


def test_analyze_no_high_inconsistency_for_subordinated_jurisdiction_and_arbitration(monkeypatch):
    """Documents the expected finding shape for the false-positive scenario
    (jurisdiction clause 'Subject to Clause 22' + arbitration clause) using
    a mocked, well-behaved provider response. Does NOT verify the live LLM
    follows the prompt -- only that analyze() passes such a response
    through correctly when it does."""
    blocks = [
        Block(
            id="BLOCK_1",
            start=0,
            end=0,
            text=(
                "This agreement shall be subject to the exclusive jurisdiction "
                "of the courts of Mumbai, subject to Clause 22."
            ),
        ),
        Block(
            id="BLOCK_22",
            start=0,
            end=0,
            text=(
                "Any dispute arising under this agreement shall be referred to "
                "arbitration in Mumbai under the Arbitration and Conciliation "
                "Act, 1996."
            ),
        ),
    ]
    well_behaved_findings = [
        {
            "category": "inconsistency",
            "severity": "low",
            "block_ids": ["BLOCK_1", "BLOCK_22"],
            "evidence_quote": (
                "subject to the exclusive jurisdiction of the courts of Mumbai, "
                "subject to Clause 22. [...] Any dispute arising under this "
                "agreement shall be referred to arbitration"
            ),
            "explanation": (
                "Clause 1 is expressly subject to Clause 22's arbitration "
                "provision; courts retain narrow supervisory jurisdiction while "
                "arbitration is the primary forum. This is deliberate "
                "coordination, not a conflict."
            ),
        }
    ]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(
        llm_client.anthropic,
        "Anthropic",
        lambda **kwargs: _FakeAnthropicClient(response=_tool_use_response(well_behaved_findings), **kwargs),
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    result = client.analyze(blocks, pseudonymised=True, is_synthetic=False)

    high_inconsistency_for_pair = [
        finding
        for finding in result
        if finding["category"] == "inconsistency"
        and finding["severity"] == "high"
        and set(finding["block_ids"]) == {"BLOCK_1", "BLOCK_22"}
    ]
    assert high_inconsistency_for_pair == []


def test_analyze_high_inconsistency_when_subordination_target_does_not_resolve(monkeypatch):
    """Calibration guard flagged in PR #45 review: the prompt must be
    effect-triggered, not keyword-triggered -- the mere presence of
    'subject to Clause X' must not be treated as proof of coordination if
    Clause X doesn't actually resolve the tension. BLOCK_22 here is an
    unrelated definitions clause, not a carve-out or override, so a
    'subject to' phrase pointing at it is camouflage, not coordination.
    Same mocked-response limitation as the sibling test above: this proves
    analyze() passes a HIGH finding through correctly even in the presence
    of 'subject to' language, not that the live model produces one --
    that needs the eval bench, out of scope here."""
    blocks = [
        Block(
            id="BLOCK_1",
            start=0,
            end=0,
            text="Party A shall pay Party B the sum of Rs. 50,000 per month, subject to Clause 22.",
        ),
        Block(
            id="BLOCK_22",
            start=0,
            end=0,
            text='"Business Day" means any day other than a Saturday, Sunday, or public holiday.',
        ),
    ]
    high_finding = [
        {
            "category": "inconsistency",
            "severity": "high",
            "block_ids": ["BLOCK_1", "BLOCK_22"],
            "evidence_quote": (
                "Party A shall pay Party B the sum of Rs. 50,000 per month, subject to "
                "Clause 22. [...] \"Business Day\" means any day other than a Saturday, "
                "Sunday, or public holiday."
            ),
            "explanation": (
                "Clause 1's payment obligation is expressed 'subject to Clause 22', but "
                "Clause 22 is only a definitions clause -- it does not condition, override, "
                "or carve out anything about the payment obligation. The 'subject to' "
                "phrase does not resolve any tension here and should not be treated as "
                "deliberate coordination."
            ),
        }
    ]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(
        llm_client.anthropic,
        "Anthropic",
        lambda **kwargs: _FakeAnthropicClient(response=_tool_use_response(high_finding), **kwargs),
    )

    client = LLMClient(_config(provider="anthropic", synthetic_only=False))
    result = client.analyze(blocks, pseudonymised=True, is_synthetic=False)

    high_inconsistency_for_pair = [
        finding
        for finding in result
        if finding["category"] == "inconsistency"
        and finding["severity"] == "high"
        and set(finding["block_ids"]) == {"BLOCK_1", "BLOCK_22"}
    ]
    assert len(high_inconsistency_for_pair) == 1
