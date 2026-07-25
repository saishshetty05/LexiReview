import { Badge } from "@/components/ui/badge";
import { formatCategory } from "@/lib/severity";
import type { FindingCategory } from "@/types/finding";

export function CategoryBadge({ category }: { category: FindingCategory }) {
  return <Badge variant="outline">{formatCategory(category)}</Badge>;
}
