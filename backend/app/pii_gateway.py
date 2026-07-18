"""pii_gateway.py — pattern-based PII redaction gate, run before any text
reaches the LLM client (CLAUDE.md rule 3: the client must refuse a payload
not flagged pseudonymised).

First cut: structured, pattern-matchable PII only — emails, phone numbers,
Indian PAN/Aadhaar numbers, US SSNs. Personal names and postal addresses are
NOT detected here. That needs NER-class tooling (e.g. Presidio/spaCy);
deliberately deferred rather than shipped as a false sense of coverage from
naive heuristics like "capitalized word sequences" (see DECISION_LOG.md).

Callers get back only the redacted text and per-category match counts —
never the matched PII itself (CLAUDE.md rule 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ordered most-distinctive-first: once a span is redacted it becomes literal
# text like "[REDACTED_EMAIL]" and can no longer match a later, broader
# pattern. PHONE is last because its digit pattern is the broadest and would
# otherwise be the first to (wrongly) consume a PAN/Aadhaar/SSN span.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "SSN": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "PAN": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    "AADHAAR": re.compile(r"(?<!\d)\d{4}[ ]\d{4}[ ]\d{4}(?!\d)"),
    "PHONE": re.compile(
        r"(?<!\d)(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d{3,5}[ .-]\d{3,5}(?:[ .-]\d{2,4})?(?!\d)"
    ),
}


@dataclass(frozen=True)
class PseudonymisationResult:
    text: str
    redaction_counts: dict[str, int] = field(default_factory=dict)

    @property
    def had_redactions(self) -> bool:
        return bool(self.redaction_counts)


def pseudonymise(text: str) -> PseudonymisationResult:
    """Redact structured PII in `text`, category by category.

    Returns the redacted text plus a count of matches per category — never
    the matched substrings themselves.
    """
    redacted = text
    counts: dict[str, int] = {}
    for category, pattern in _PATTERNS.items():
        redacted, n = pattern.subn(f"[REDACTED_{category}]", redacted)
        if n:
            counts[category] = n
    return PseudonymisationResult(text=redacted, redaction_counts=counts)
