import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AuthLayout } from "@/components/layout/AuthLayout";
import { useAuth } from "@/hooks/useAuth";
import { useLoginMutation, useMFAChallengeMutation } from "@/hooks/queries";
import { ApiError } from "@/lib/api";
import { toast } from "sonner";

type Step = "credentials" | "mfa";

export function LoginPage() {
  const navigate = useNavigate();
  const { markLoggedIn } = useAuth();
  const loginMutation = useLoginMutation();
  const challengeMutation = useMFAChallengeMutation();
  const [step, setStep] = useState<Step>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [mfaPendingToken, setMfaPendingToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleCredentialsSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await loginMutation.mutateAsync({ email, password });
      if (result.mfa_required && result.mfa_token) {
        setMfaPendingToken(result.mfa_token);
        setStep("mfa");
      } else {
        markLoggedIn(email);
        navigate("/dashboard", { replace: true });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    }
  }

  function handleMfaCodeChange(event: React.ChangeEvent<HTMLInputElement>) {
    const value = event.target.value.replace(/\D/g, "").slice(0, 6);
    setMfaCode(value);
  }

  async function handleMfaSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (mfaCode.length !== 6 || !mfaPendingToken) return;
    setError(null);
    try {
      await challengeMutation.mutateAsync({ mfa_token: mfaPendingToken, code: mfaCode });
      markLoggedIn(email);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 429) {
          toast.error("Too many attempts. Please log in again.");
          setStep("credentials");
          setMfaPendingToken(null);
          setMfaCode("");
        } else {
          setError("Invalid code. Please try again.");
        }
      } else {
        setError("Something went wrong. Please try again.");
      }
    }
  }

  const isMfaSubmitting = challengeMutation.isPending;
  const isCredentialsSubmitting = loginMutation.isPending;

  return (
    <AuthLayout>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{step === "credentials" ? "Log in to LexiReview" : "Two-factor authentication"}</CardTitle>
          <CardDescription>
            {step === "credentials"
              ? "Welcome back. Enter your details below."
              : "Enter the 6-digit code from your authenticator app."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {step === "credentials" ? (
            <form onSubmit={handleCredentialsSubmit} className="flex flex-col gap-4">
              {error && (
                <p className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive">
                  {error}
                </p>
              )}

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="email"
                  disabled={isCredentialsSubmitting}
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  disabled={isCredentialsSubmitting}
                />
              </div>

              <Button type="submit" disabled={isCredentialsSubmitting} className="w-full">
                {isCredentialsSubmitting ? "Logging in..." : "Log in"}
              </Button>

              <p className="text-center text-sm text-muted-foreground">
                Don't have an account?{" "}
                <Link to="/signup" className="font-medium text-foreground underline underline-offset-4">
                  Sign up
                </Link>
              </p>
            </form>
          ) : (
            <form onSubmit={handleMfaSubmit} className="flex flex-col gap-4">
              {error && (
                <p className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive">
                  {error}
                </p>
              )}

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="mfa-code">Authentication code</Label>
                <Input
                  id="mfa-code"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={6}
                  required
                  value={mfaCode}
                  onChange={handleMfaCodeChange}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && mfaCode.length === 6) {
                      event.preventDefault();
                      handleMfaSubmit(event);
                    }
                  }}
                  autoComplete="one-time-code"
                  autoFocus
                  disabled={isMfaSubmitting}
                  className="text-center text-lg tracking-widest"
                />
              </div>

              <Button type="submit" disabled={isMfaSubmitting || mfaCode.length !== 6} className="w-full">
                {isMfaSubmitting ? "Verifying..." : "Verify and log in"}
              </Button>

              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="w-full"
                onClick={() => {
                  setStep("credentials");
                  setMfaPendingToken(null);
                  setMfaCode("");
                  setError(null);
                }}
                disabled={isMfaSubmitting}
              >
                Back to login
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </AuthLayout>
  );
}