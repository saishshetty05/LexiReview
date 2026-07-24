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
import { Label } from "@/components/ui/label";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import { useAuth } from "@/hooks/useAuth";
import { useDeleteAccountMutation, useLogoutAndInvalidate } from "@/hooks/queries";

export function AccountSettingsPage() {
  const navigate = useNavigate();
  const { email, clearSession } = useAuth();
  const logout = useLogoutAndInvalidate();
  const deleteAccountMutation = useDeleteAccountMutation();
  const [confirmOpen, setConfirmOpen] = useState(false);

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
      </div>
    </SettingsLayout>
  );
}
