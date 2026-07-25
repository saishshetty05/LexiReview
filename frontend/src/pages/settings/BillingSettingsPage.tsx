import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import type { MockPlan } from "@/types/billing";

// Mock only -- see types/billing.ts. Numbers below are illustrative, not
// derived from any real usage data (no usage-tracking/quota concept exists
// on the backend either).
const MOCK_PLAN: MockPlan = {
  name: "Starter",
  price: "$0 / month",
  documentsUsed: 2,
  documentsLimit: 5,
};

export function BillingSettingsPage() {
  const usagePercent = MOCK_PLAN.documentsLimit
    ? (MOCK_PLAN.documentsUsed / MOCK_PLAN.documentsLimit) * 100
    : 0;

  return (
    <SettingsLayout active="billing">
      <div className="flex flex-col gap-6">
        <Card>
          <CardHeader className="flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle>Current plan</CardTitle>
              <CardDescription>{MOCK_PLAN.price}</CardDescription>
            </div>
            <Badge>{MOCK_PLAN.name}</Badge>
          </CardHeader>
          <CardContent>
            <div className="mb-1.5 flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Documents this month</span>
              <span>
                {MOCK_PLAN.documentsUsed}
                {MOCK_PLAN.documentsLimit ? ` / ${MOCK_PLAN.documentsLimit}` : ""}
              </span>
            </div>
            <Progress value={usagePercent} />
          </CardContent>
          <CardFooter className="gap-2">
            <Button asChild>
              <Link to="/pricing">Upgrade plan</Link>
            </Button>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span>
                    <Button variant="outline" disabled>
                      Manage billing
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>Billing integration is coming soon.</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </CardFooter>
        </Card>
      </div>
    </SettingsLayout>
  );
}
