import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import { useAuth } from "@/hooks/useAuth";
import { useDeleteAccountMutation, useLogoutAndInvalidate, useMFASetupMutation, useMFAVerifySetupMutation, useMFADisableMutation } from "@/hooks/queries";
import { toast } from "sonner";

type MFAState = "disabled" | "setup_qr" | "setup_verify" | "enabled";

export function AccountSettingsPage() {
  const navigate = useNavigate();
  const { email, clearSession } = useAuth();
  const logout = useLogoutAndInvalidate();
  const deleteAccountMutation = useDeleteAccountMutation();
  const mfaSetupMutation = useMFASetupMutation();
  const mfaVerifySetupMutation = useMFAVerifySetupMutation();
  const mfaDisableMutation = useMFADisableMutation();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [mfaState, setMfaState] = useState<MFAState>("disabled");
  const [qrCode, setQrCode] = useState<string | null>(null);
  const [totpSecret, setTotpSecret] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState("");

  async function handleLogout() {
    try {
      await logout.mutateAsync();
    } finally {
      clearSession();
      navigate("/login", { replace: true });
    }
  }

  async function handleDelete() {
    try {
      await deleteAccountMutation.mutateAsync();
    } finally {
      clearSession();
      navigate("/login", { replace: true });
    }
  }

  async function handleEnableMfa() {
    try {
      const result = await mfaSetupMutation.mutateAsync();
      setQrCode(result.qr_code);
      setTotpSecret(result.secret);
      setMfaState("setup_qr");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to start MFA setup. Please try again.");
    }
  }

  function handleMfaCodeChange(event: React.ChangeEvent<HTMLInputElement>) {
    const value = event.target.value.replace(/\D/g, "").slice(0, 6);
    setMfaCode(value);
  }

  async function handleVerifySetup(event: React.FormEvent) {
    event.preventDefault();
    if (mfaCode.length !== 6) return;

    try {
      await mfaVerifySetupMutation.mutateAsync({ code: mfaCode });
      setMfaState("enabled");
      setMfaCode("");
      setQrCode(null);
      setTotpSecret(null);
      toast.success("Two-factor authentication enabled");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Invalid code. Please try again.");
    }
  }

  async function handleDisableMfa(event: React.FormEvent) {
    event.preventDefault();
    if (mfaCode.length !== 6) return;

    try {
      await mfaDisableMutation.mutateAsync({ code: mfaCode });
      setMfaState("disabled");
      setMfaCode("");
      toast.success("Two-factor authentication disabled");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Invalid code. Please try again.");
    }
  }

  function handleCancelSetup() {
    setMfaState("disabled");
    setQrCode(null);
    setTotpSecret(null);
    setMfaCode("");
  }

  return (
    <SettingsLayout active="account">
      <div className="flex flex-col gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Account</CardTitle>
            <CardDescription>Your account details.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div>
              <Label className="text-muted-foreground">Email</Label>
              <p className="text-sm">{email ?? "Unknown"}</p>
            </div>
            <Button variant="outline" className="w-fit" onClick={handleLogout}>
              Log out
            </Button>
          </CardContent>
        </Card>

        <Card className="border-destructive/30">
          <CardHeader>
            <CardTitle className="text-destructive">Danger zone</CardTitle>
            <CardDescription>
              Deleting your account permanently removes all your documents, findings, and account
              data. This cannot be undone.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
              <DialogTrigger asChild>
                <Button variant="destructive">Delete account</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Delete your account?</DialogTitle>
                  <DialogDescription>
                    This permanently deletes your account and all associated documents and
                    analysis history. This action cannot be undone.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setConfirmOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={deleteAccountMutation.isPending}
                    onClick={handleDelete}
                  >
                    {deleteAccountMutation.isPending ? "Deleting..." : "Yes, delete my account"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Two-factor authentication</CardTitle>
            <CardDescription>
              Add an extra layer of security to your account using an authenticator app.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {mfaState === "disabled" && (
              <div className="flex flex-col gap-4">
                <p className="text-muted-foreground">
                  Two-factor authentication is not enabled.
                </p>
                <Button onClick={handleEnableMfa} disabled={mfaSetupMutation.isPending}>
                  {mfaSetupMutation.isPending ? "Setting up..." : "Enable two-factor authentication"}
                </Button>
              </div>
            )}

            {mfaState === "setup_qr" && (
              <div className="flex flex-col gap-4">
                <p className="text-sm text-muted-foreground">
                  Scan this QR code with your authenticator app (Google Authenticator, Authy, 1Password, etc.)
                </p>
                {qrCode && (
                  <div className="flex justify-center">
                    <img
                      src={`data:image/png;base64,${qrCode}`}
                      alt="MFA QR code"
                      className="border rounded-md p-2"
                    />
                  </div>
                )}
                <div className="p-3 rounded-md bg-muted text-sm font-mono break-all">
                  <strong>Secret (manual entry):</strong> {totpSecret}
                </div>
                <div className="flex gap-2">
                  <Button onClick={() => { setMfaState("setup_verify"); setMfaCode(""); }} disabled={mfaSetupMutation.isPending}>
                    Next
                  </Button>
                  <Button variant="outline" onClick={handleCancelSetup} disabled={mfaSetupMutation.isPending}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}

            {mfaState === "setup_verify" && (
              <form onSubmit={handleVerifySetup} className="flex flex-col gap-4">
                <p className="text-sm text-muted-foreground">
                  Enter the 6-digit code from your authenticator app to verify setup.
                </p>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="mfa-verify-code">Authentication code</Label>
                  <Input
                    id="mfa-verify-code"
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
                        handleVerifySetup(event);
                      }
                    }}
                    autoComplete="one-time-code"
                    autoFocus
                    disabled={mfaVerifySetupMutation.isPending}
                    className="text-center text-lg tracking-widest"
                  />
                </div>
                <div className="flex gap-2">
                  <Button type="submit" disabled={mfaVerifySetupMutation.isPending || mfaCode.length !== 6}>
                    {mfaVerifySetupMutation.isPending ? "Verifying..." : "Verify and enable"}
                  </Button>
                  <Button type="button" variant="outline" onClick={handleCancelSetup} disabled={mfaVerifySetupMutation.isPending}>
                    Cancel
                  </Button>
                </div>
              </form>
            )}

            {mfaState === "enabled" && (
              <div className="flex flex-col gap-4">
                <div className="flex items-center gap-3 p-3 rounded-md bg-green-50 border border-green-200">
                  <svg className="h-5 w-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span className="text-green-800 font-medium">Two-factor authentication is enabled</span>
                </div>
                <form onSubmit={handleDisableMfa} className="flex flex-col gap-4">
                  <p className="text-sm text-muted-foreground">
                    Enter a 6-digit code from your authenticator app to disable two-factor authentication.
                  </p>
                  <div className="flex flex-col gap-1.5">
                    <Label htmlFor="mfa-disable-code">Authentication code</Label>
                    <Input
                      id="mfa-disable-code"
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
                          handleDisableMfa(event);
                        }
                      }}
                      autoComplete="one-time-code"
                      autoFocus
                      disabled={mfaDisableMutation.isPending}
                      className="text-center text-lg tracking-widest"
                    />
                  </div>
                  <div className="flex gap-2">
                    <Button type="submit" variant="destructive" disabled={mfaDisableMutation.isPending || mfaCode.length !== 6}>
                      {mfaDisableMutation.isPending ? "Disabling..." : "Disable two-factor authentication"}
                    </Button>
                  </div>
                </form>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </SettingsLayout>
  );
}