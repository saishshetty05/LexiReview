// Single source of truth for severity/category display -- previously the
// color mapping was duplicated between SeverityBadge.tsx and
// SummaryHeader.tsx (two copies of one map, a real duplication-bug risk).
// The actual colors live as CSS variables in src/index.css
// (--severity-high/medium/low/info) and are wired into Tailwind's
// `severity-*` color scale in tailwind.config.js; this module only maps a
// Severity value to the right badgeVariants() variant name and a display
// label, so every component (SeverityBadge, SummaryHeader's risk pills,
// FilterChips) reads the same mapping.
import type { FindingCategory, Severity } from "@/types/finding";
import type { BadgeProps } from "@/components/ui/badge";

export const SEVERITY_ORDER: Severity[] = ["high", "medium", "low", "info"];

export const SEVERITY_BADGE_VARIANT: Record<Severity, NonNullable<BadgeProps["variant"]>> = {
  high: "severity-high",
  medium: "severity-medium",
  low: "severity-low",
  info: "severity-info",
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

export function formatCategory(category: FindingCategory): string {
  return category
    .split("_")
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}
