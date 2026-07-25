import { Badge } from "@/components/ui/badge";
import { SEVERITY_BADGE_VARIANT, SEVERITY_LABEL } from "@/lib/severity";
import type { Severity } from "@/types/finding";

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge variant={SEVERITY_BADGE_VARIANT[severity]}>{SEVERITY_LABEL[severity]}</Badge>;
}
