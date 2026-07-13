# Interface Contracts (jointly owned — changes require both people's PR approval)

## 1. analysis_jobs table (agree in week 1)
Proposed starting point:
- id (uuid, pk) · user_id (fk) · doc_id (fk) · doc_version_hash (text)
- state: queued -> running -> succeeded | failed  (+ retry_count, error_reason)
- created_at / started_at / finished_at
Writer rules: API creates rows (state=queued); only the worker moves state forward.

## 2. Findings JSON schema (agree in week 1)
Proposed starting point (one finding):
{
  "category": "termination | liability | ... | inconsistency",
  "severity": "high | medium | low | info",
  "block_ids": ["BLOCK_12"],            // two ids for inconsistencies
  "evidence_quote": "verbatim text",
  "explanation": "why this matters",
  "verification": "verified | unverified",
  "confidence": "standard | needs_review"
}
