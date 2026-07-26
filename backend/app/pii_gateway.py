"""pii_gateway.py — PII redaction gate, run before any text reaches the LLM
client (CLAUDE.md rule 3: the client must refuse a payload not flagged
pseudonymised).

Two independent passes over the SAME original text — regex patterns for
structured, pattern-matchable PII (emails, phone numbers, Indian
PAN/Aadhaar numbers, US SSNs), and an NER pass (Presidio + spaCy) for
personal names and locations/addresses — merged into one non-overlapping
set of spans (regex wins on overlap) and replaced in a single pass.

NOT a sequential mutate-then-feed-forward pipeline in either direction —
two other designs were tried and both broke real test cases, so this one
is deliberate, not just "the design that happened to be written first":
  - NER on the original text, THEN regex on what's left: NER misclassified
    "Email a@example.com" itself as a PERSON span, consuming the email
    before the far-more-reliable EMAIL regex ever got a chance at it.
  - Regex first, THEN NER on the redacted text: the "[REDACTED_PAN]"-style
    placeholders NER then had to read are themselves confusing —
    all-caps, underscored, bracketed tokens next to a capitalized word
    (e.g. "Email [REDACTED_EMAIL]") read as name-shaped to the tagger,
    producing NEW false positives that didn't exist in the original text.
Running both passes against the pristine original text and merging
afterward avoids both failure modes: NER never sees a redaction
placeholder, and an exact-format regex match always wins over an NER
guess at the same position.

The NER layer closes the gap the original 2026-07-16 first cut
deliberately left open (see DECISION_LOG.md) — it was safe to defer only
while SYNTHETIC_ONLY forced free-tier traffic to synthetic documents; that
stopped being true once the project moved to a paid provider
(2026-07-23), which SYNTHETIC_ONLY correctly doesn't restrict.

Known, accepted precision tradeoff: spaCy's LOCATION/GPE entity type also
catches bare jurisdiction references in ordinary legal boilerplate (e.g.
"governed by the laws of India" redacts "India"), not just full postal
addresses, and it is more reliable at named places than at fully spelled
street addresses. Over-redacting a non-sensitive place name is a far
smaller cost than under-redacting a real address or name -- the same
fail-closed philosophy already used throughout this codebase (RLS
fail-closed, the verifier's high match threshold, missing_clause's forced-
empty fields). Do NOT add exclusion-list heuristics to suppress this --
that reintroduces exactly the "false sense of coverage from naive
heuristics" the original decision already rejected once.

Callers get back only the redacted text and per-category match counts —
never the matched PII itself (CLAUDE.md rule 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

# Ordered most-distinctive-first: this is priority order for _regex_spans'
# overlap resolution below, not a sequential-mutation ordering (the module
# used to redact-then-feed-forward; it now collects every pattern's matches
# against the same original text and keeps only the highest-priority one at
# each position). PHONE is last because its broad digit-group pattern
# genuinely overlaps AADHAAR on real text -- e.g. "1234 5678" inside
# "1234 5678 9012" matches PHONE's shape too (verified empirically) -- and
# would otherwise wrongly claim part of an Aadhaar number as a phone number.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "SSN": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "PAN": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    "AADHAAR": re.compile(r"(?<!\d)\d{4}[ ]\d{4}[ ]\d{4}(?!\d)"),
    "PHONE": re.compile(
        r"(?<!\d)(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d{3,5}[ .-]\d{3,5}(?:[ .-]\d{2,4})?(?!\d)"
    ),
}

_NER_ENTITIES = ("PERSON", "LOCATION")

# en_core_web_sm, not the larger en_core_web_lg default -- accept the
# dependency/image-size tradeoff already agreed on, without gold-plating it.
# Loaded once at import (matches the module-level _PATTERNS constant above),
# reused for every call in the process.
_nlp_engine = NlpEngineProvider(
    nlp_configuration={"nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}]}
).create_engine()
_analyzer = AnalyzerEngine(nlp_engine=_nlp_engine, supported_languages=["en"])


@dataclass(frozen=True)
class PseudonymisationResult:
    text: str
    redaction_counts: dict[str, int] = field(default_factory=dict)

    @property
    def had_redactions(self) -> bool:
        return bool(self.redaction_counts)


def _regex_spans(text: str) -> list[tuple[int, int, str]]:
    # _PATTERNS' iteration order is the priority order: a later pattern's
    # match is dropped if it overlaps a span an earlier (more distinctive)
    # pattern already claimed at the same position, same reasoning as
    # pseudonymise()'s regex-over-NER priority below.
    spans: list[tuple[int, int, str]] = []
    for category, pattern in _PATTERNS.items():
        for match in pattern.finditer(text):
            if any(match.start() < e and match.end() > s for s, e, _c in spans):
                continue
            spans.append((match.start(), match.end(), category))
    return spans


def _ner_spans(text: str) -> list[tuple[int, int, str]]:
    results = _analyzer.analyze(text=text, entities=list(_NER_ENTITIES), language="en")
    return [(r.start, r.end, r.entity_type) for r in results]


def pseudonymise(text: str) -> PseudonymisationResult:
    """Redact PII in `text` — see the module docstring for why regex and
    NER spans are collected independently against the original text and
    merged, rather than one pass feeding the other.

    Returns the redacted text plus a count of matches per category — never
    the matched substrings themselves.
    """
    regex_matches = _regex_spans(text)
    claimed = [(start, end) for start, end, _category in regex_matches]

    # Regex spans (exact-format, proven zero-false-positive) win on any
    # overlap; an NER span is only kept where it doesn't collide with one.
    spans = list(regex_matches)
    for start, end, entity_type in _ner_spans(text):
        if not any(start < claimed_end and end > claimed_start for claimed_start, claimed_end in claimed):
            spans.append((start, end, entity_type))
            claimed.append((start, end))

    spans.sort(key=lambda span: span[0])
    counts: dict[str, int] = {}
    pieces: list[str] = []
    cursor = 0
    for start, end, category in spans:
        pieces.append(text[cursor:start])
        pieces.append(f"[REDACTED_{category}]")
        counts[category] = counts.get(category, 0) + 1
        cursor = end
    pieces.append(text[cursor:])
    return PseudonymisationResult(text="".join(pieces), redaction_counts=counts)
