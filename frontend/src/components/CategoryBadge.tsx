import type { FindingCategory } from "../types/finding";

function formatCategory(category: FindingCategory): string {
  return category
    .split("_")
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

export function CategoryBadge({ category }: { category: FindingCategory }) {
  return (
    <span className="inline-block rounded-full border border-slate-300 bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
      {formatCategory(category)}
    </span>
  );
}
