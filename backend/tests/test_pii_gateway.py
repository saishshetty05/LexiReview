"""Tests for pii_gateway.py: structured PII (regex) plus NER-based
detection (Presidio + spaCy) for names/locations. Runs against the real
model, not a mock -- matches this codebase's general preference for real
behavior over mocks where feasible."""

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


def test_aadhaar_number_is_not_also_double_matched_as_phone():
    """Regression: an Aadhaar number's first two groups ("1234 5678" inside
    "1234 5678 9012") also match PHONE's broad digit-group pattern -- with
    span-collection done independently per pattern (not sequential
    mutation), this needs explicit overlap resolution or the same real PII
    item gets counted under two categories. AADHAAR must win (higher
    priority in _PATTERNS) and the match must appear exactly once."""
    result = pseudonymise("The Aadhaar number 1234 5678 9012 must be verified.")
    assert result.redaction_counts == {"AADHAAR": 1}
    assert result.text.count("[REDACTED_") == 1


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


def test_person_name_is_redacted():
    result = pseudonymise("This agreement is entered into by John Smith and Priya Sharma.")
    assert "John Smith" not in result.text
    assert "Priya Sharma" not in result.text
    assert "[REDACTED_PERSON]" in result.text
    assert result.redaction_counts.get("PERSON") == 2


def test_location_is_redacted():
    result = pseudonymise("The parties agree this contract is governed by the laws of India.")
    assert "India" not in result.text
    assert "[REDACTED_LOCATION]" in result.text
    assert result.redaction_counts.get("LOCATION") == 1


def test_name_and_structured_pii_in_same_text_both_redacted():
    text = "Contact John Doe at john.doe@example.com regarding this lease."
    result = pseudonymise(text)
    assert "John Doe" not in result.text
    assert "john.doe@example.com" not in result.text
    assert result.redaction_counts == {"PERSON": 1, "EMAIL": 1}
