import { Link } from "react-router-dom";
import { CheckCircle2, FileSearch, ShieldCheck, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { MarketingLayout } from "@/components/layout/MarketingLayout";

const FEATURES = [
  {
    icon: FileSearch,
    title: "Verified-quote findings",
    description:
      "Every finding is machine-verified against the exact document text before it's ever shown as verified. Nothing is asserted without a matching quote.",
  },
  {
    icon: ShieldCheck,
    title: "Unverified is flagged, never hidden",
    description:
      "If a quote can't be matched with confidence, the finding is still shown — clearly marked unverified — and requires explicit confirmation before it can be accepted.",
  },
  {
    icon: Sparkles,
    title: "Subordination-aware detection",
    description:
      "LexiReview recognizes coordinating language like “subject to” and “notwithstanding” before flagging clauses as inconsistent, cutting down on false positives from standard drafting patterns.",
  },
];

const STEPS = [
  { title: "Upload", description: "Drop in a PDF or DOCX contract. Preflight checks run before anything is queued." },
  { title: "Automated review", description: "Every clause is checked for risk, inconsistency, and missing standard terms." },
  { title: "Review with confidence", description: "Walk through each finding, verified evidence quote included, and record your decisions." },
];

export function LandingPage() {
  return (
    <MarketingLayout>
      <section className="container flex flex-col items-center gap-6 py-24 text-center">
        <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5" />
          AI-assisted contract review
        </span>
        <h1 className="max-w-2xl text-4xl font-bold tracking-tight sm:text-5xl">
          Catch risky clauses before you sign.
        </h1>
        <p className="max-w-xl text-lg text-muted-foreground">
          LexiReview finds risky clauses, missing terms, and internal contradictions in your
          contracts — with verified evidence for every finding, not just plausible-sounding
          guesses.
        </p>
        <div className="flex gap-3">
          <Button size="lg" asChild>
            <Link to="/signup">Start free</Link>
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          AI-generated draft. Not legal advice. Requires attorney review.
        </p>
      </section>

      <section className="border-t bg-muted/30 py-20">
        <div className="container">
          <h2 className="mb-10 text-center text-2xl font-semibold">How it works</h2>
          <div className="grid gap-6 sm:grid-cols-3">
            {STEPS.map((step, index) => (
              <Card key={step.title}>
                <CardHeader>
                  <span className="mb-2 flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
                    {index + 1}
                  </span>
                  <CardTitle className="text-base">{step.title}</CardTitle>
                  <CardDescription>{step.description}</CardDescription>
                </CardHeader>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <section className="container py-20">
        <h2 className="mb-10 text-center text-2xl font-semibold">Built to be trusted, not just fast</h2>
        <div className="grid gap-6 md:grid-cols-3">
          {FEATURES.map((feature) => (
            <Card key={feature.title}>
              <CardHeader>
                <feature.icon className="mb-2 h-6 w-6 text-primary" />
                <CardTitle className="text-base">{feature.title}</CardTitle>
                <CardDescription>{feature.description}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      </section>

      <section className="border-t bg-muted/30 py-20">
        <div className="container flex flex-col items-center gap-4 text-center">
          <CheckCircle2 className="h-8 w-8 text-primary" />
          <h2 className="text-2xl font-semibold">Ready to review your first contract?</h2>
          <p className="max-w-md text-muted-foreground">
            No credit card required to get started.
          </p>
          <Button size="lg" asChild>
            <Link to="/signup">Start free</Link>
          </Button>
        </div>
      </section>
    </MarketingLayout>
  );
}
