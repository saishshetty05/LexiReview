import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { Decision } from "@/types/decision";

export type DecisionFilter = Decision | "all";

const FILTERS: { value: DecisionFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "accepted", label: "Accepted" },
  { value: "dismissed", label: "Dismissed" },
];

export function FilterChips({
  value,
  onChange,
}: {
  value: DecisionFilter;
  onChange: (value: DecisionFilter) => void;
}) {
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      value={value}
      onValueChange={(next) => {
        if (next) onChange(next as DecisionFilter);
      }}
      className="justify-start"
    >
      {FILTERS.map((filter) => (
        <ToggleGroupItem key={filter.value} value={filter.value} size="sm" className="px-3">
          {filter.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
