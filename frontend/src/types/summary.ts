// Mirrors CONTRACTS.md §2c's document_summaries.payload shape exactly.
export interface KeyTerm {
  label: string;
  detail: string;
}

export interface RiskSnapshot {
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface DocumentSummary {
  overview: string;
  key_terms: KeyTerm[];
  risk_snapshot: RiskSnapshot;
}
