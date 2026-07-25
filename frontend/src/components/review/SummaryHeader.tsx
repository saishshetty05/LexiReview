import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { SEVERITY_BADGE_VARIANT, SEVERITY_LABEL, SEVERITY_ORDER } from "@/lib/severity";
import type { DocumentSummary } from "@/types/summary";

export function SummaryHeader({ summary }: { summary: DocumentSummary }) {
  return (
    <Card className="mb-4">
      <CardContent className="p-4">
        <p className="mb-3 text-sm">{summary.overview}</p>

        <div className="mb-3 flex flex-wrap gap-2">
          {SEVERITY_ORDER.map((severity) => (
            <Badge key={severity} variant={SEVERITY_BADGE_VARIANT[severity]}>
              {summary.risk_snapshot[severity]} {SEVERITY_LABEL[severity]}
            </Badge>
          ))}
        </div>

        {summary.key_terms.length > 0 && (
          <dl className="grid grid-cols-1 gap-1 text-xs text-muted-foreground sm:grid-cols-2">
            {summary.key_terms.map((term) => (
              <div key={term.label}>
                <dt className="inline font-semibold text-foreground">{term.label}: </dt>
                <dd className="inline">{term.detail}</dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}
