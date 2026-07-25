import { Link } from "react-router-dom";
import { Check } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { MarketingLayout } from "@/components/layout/MarketingLayout";
import { cn } from "@/lib/utils";

// Mock pricing tiers -- no real billing/subscription backend exists yet
// (confirmed: no plan/subscription/stripe concept anywhere in the schema).
// This page is UI-complete but not wired to anything real; see
// src/pages/settings/BillingSettingsPage.tsx for the equivalent in-app mock.
const PLANS = [
  {
    name: "Starter",
    price: "$0",
    period: "forever",
    description: "Try LexiReview on a handful of contracts.",
    features: ["5 documents / month", "Standard review turnaround", "Email support"],
    cta: "Start free",
    highlighted: false,
  },
  {
    name: "Team",
    price: "$49",
    period: "per seat / month",
    description: "For legal and ops teams reviewing contracts regularly.",
    features: [
      "Unlimited documents",
      "Priority review turnaround",
      "Team workspace (coming soon)",
      "Priority support",
    ],
    cta: "Start free trial",
    highlighted: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    period: "billed annually",
    description: "Custom volume, SSO, and dedicated support.",
    features: ["Volume pricing", "SSO / SAML (coming soon)", "Dedicated onboarding", "SLA-backed support"],
    cta: "Contact sales",
    highlighted: false,
  },
];

const FAQ = [
  {
    q: "Is this a substitute for a lawyer?",
    a: "No. LexiReview is an AI-assisted first-pass review. Every finding requires attorney review before you rely on it — we never say a document is “safe,” only that no issues were detected by automated review.",
  },
  {
    q: "What happens to my documents?",
    a: "Documents are stored per-account and never used to train third-party models on paid plans.",
  },
  {
    q: "Can I cancel anytime?",
    a: "Yes, plans are month-to-month with no long-term commitment on Starter and Team tiers.",
  },
];

export function PricingPage() {
  return (
    <MarketingLayout>
      <section className="container py-16">
        <div className="mx-auto mb-12 max-w-xl text-center">
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Simple, transparent pricing</h1>
          <p className="mt-3 text-muted-foreground">Start free. Upgrade when your team needs more.</p>
        </div>

        <div className="grid gap-6 md:grid-cols-3">
          {PLANS.map((plan) => (
            <Card
              key={plan.name}
              className={cn("flex flex-col", plan.highlighted && "border-primary shadow-md")}
            >
              <CardHeader>
                {plan.highlighted && (
                  <Badge className="mb-2 w-fit" variant="default">
                    Most popular
                  </Badge>
                )}
                <CardTitle>{plan.name}</CardTitle>
                <CardDescription>{plan.description}</CardDescription>
                <div className="pt-2">
                  <span className="text-3xl font-bold">{plan.price}</span>
                  <span className="ml-1 text-sm text-muted-foreground">{plan.period}</span>
                </div>
              </CardHeader>
              <CardContent className="flex-1">
                <ul className="space-y-2 text-sm">
                  {plan.features.map((feature) => (
                    <li key={feature} className="flex items-start gap-2">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                      {feature}
                    </li>
                  ))}
                </ul>
              </CardContent>
              <CardFooter>
                <Button className="w-full" variant={plan.highlighted ? "default" : "outline"} asChild>
                  <Link to="/signup">{plan.cta}</Link>
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      </section>

      <section className="border-t bg-muted/30 py-16">
        <div className="container max-w-2xl">
          <h2 className="mb-8 text-center text-2xl font-semibold">Frequently asked questions</h2>
          <div className="space-y-6">
            {FAQ.map((item) => (
              <div key={item.q}>
                <h3 className="font-medium">{item.q}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{item.a}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </MarketingLayout>
  );
}
