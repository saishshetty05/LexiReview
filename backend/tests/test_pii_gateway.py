"""Tests for pii_gateway.py. This is a pattern-based first cut — structured
PII only (email/phone/PAN/Aadhaar/SSN). Personal names/addresses are NOT
covered; see the module docstring."""

from __future__ import annotations

from app.pii_gateway import pseudonymise

# Realistic lease-style prose, deliberately free of the structured PII
# patterns below, used to check for false positives on ordinary clause
# numbers, money amounts, dates, and BLOCK_n ids.
CLEAN_LEASE_TEXT = (
    "Clause 3: The monthly rent shall be Rs. 50,000, payable on the 5th of "
    "each month. Clause 14: the parties agree to a monthly rent of "
    "Rs. 60,000 effective 2026-07-16. See BLOCK_4 and BLOCK_19."
)


def test_email_is_redacted():
    result = pseudonymise("Contact the tenant at john.doe@example.com for queries.")
    assert "john.doe@example.com" not in result.text
    assert "[REDACTED_EMAIL]" in result.text
    assert result.redaction_counts == {"EMAIL": 1}


def test_ssn_is_redacted():
    result = pseudonymise("SSN on file: 123-45-6789.")
    assert "123-45-6789" not in result.text
    assert "[REDACTED_SSN]" in result.text
    assert result.redaction_counts == {"SSN": 1}


def test_pan_is_redacted():
    result = pseudonymise("PAN: ABCDE1234F provided for verification.")
    assert "ABCDE1234F" not in result.text
    assert "[REDACTED_PAN]" in result.text
    assert result.redaction_counts == {"PAN": 1}


def test_aadhaar_is_redacted():
    result = pseudonymise("Aadhaar number 1234 5678 9012 recorded.")
    assert "1234 5678 9012" not in result.text
    assert "[REDACTED_AADHAAR]" in result.text
    assert result.redaction_counts == {"AADHAAR": 1}


def test_phone_is_redacted():
    result = pseudonymise("Reach the landlord at +91 98765-43210 for repairs.")
    assert "98765-43210" not in result.text
    assert "[REDACTED_PHONE]" in result.text
    assert result.redaction_counts.get("PHONE") == 1


def test_multiple_matches_of_same_category_are_all_redacted_and_counted():
    result = pseudonymise("Emails: a@example.com and b@example.com.")
    assert "a@example.com" not in result.text
    assert "b@example.com" not in result.text
    assert result.redaction_counts == {"EMAIL": 2}


def test_multiple_categories_are_all_redacted():
    text = "Email a@example.com, SSN 123-45-6789, PAN ABCDE1234F."
    result = pseudonymise(text)
    assert result.redaction_counts == {"EMAIL": 1, "SSN": 1, "PAN": 1}


def test_clean_lease_text_has_no_false_positives():
    """Clause numbers, money amounts, dates, and BLOCK_n ids must never be
    mistaken for PII — a false positive here would corrupt evidence_quote
    matching downstream."""
    result = pseudonymise(CLEAN_LEASE_TEXT)
    assert result.text == CLEAN_LEASE_TEXT
    assert result.redaction_counts == {}
    assert result.had_redactions is False


def test_no_pii_returns_unmodified_text():
    text = "This agreement contains no structured PII at all."
    result = pseudonymise(text)
    assert result.text == text
    assert result.had_redactions is False
