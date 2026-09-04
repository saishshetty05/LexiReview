import { useState } from "react";
import { Loader2, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { AdminUserRow } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { useAdminUsersQuery, useCurrentUserQuery, useSetUserActiveMutation } from "@/hooks/queries";

function formatDate(isoString: string): string {
  return new Date(isoString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function AdminUsersPage() {
  const currentUserQuery = useCurrentUserQuery();
  const usersQuery = useAdminUsersQuery(true);
  const setActiveMutation = useSetUserActiveMutation();
  const [pendingSuspend, setPendingSuspend] = useState<AdminUserRow | null>(null);

  const currentUserId = currentUserQuery.data?.user_id;

  async function handleReactivate(user: AdminUserRow) {
    try {
      await setActiveMutation.mutateAsync({ userId: user.user_id, active: true });
      toast.success(`${user.email} reactivated`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to reactivate user.");
    }
  }

  async function handleConfirmSuspend() {
    if (!pendingSuspend) return;
    try {
      await setActiveMutation.mutateAsync({ userId: pendingSuspend.user_id, active: false });
      toast.success(`${pendingSuspend.email} suspended`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to suspend user.");
    } finally {
      setPendingSuspend(null);
    }
  }

  const users = usersQuery.data ?? [];

  return (
    <AppShell title="Admin">
      <div className="flex h-full flex-col p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold">Users</h1>
          <p className="text-sm text-muted-foreground">
            List every account and suspend or reactivate access. Metadata only — document
            content is never visible here.
          </p>
        </div>

        {usersQuery.isLoading && (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        )}

        {usersQuery.isError && (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-destructive">Failed to load users. Please try again.</p>
          </div>
        )}

        {!usersQuery.isLoading && !usersQuery.isError && (
          <Card>
            <CardHeader>
              <CardTitle>{users.length} accounts</CardTitle>
              <CardDescription>Sorted by signup date.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="py-2 pr-4 font-medium">Email</th>
                      <th className="py-2 pr-4 font-medium">Role</th>
                      <th className="py-2 pr-4 font-medium">Status</th>
                      <th className="py-2 pr-4 font-medium">Joined</th>
                      <th className="py-2 pr-4 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((user) => {
                      const isSelf = user.user_id === currentUserId;
                      return (
                        <tr key={user.user_id} className="border-b last:border-0">
                          <td className="py-3 pr-4">{user.email}</td>
                          <td className="py-3 pr-4">
                            {user.is_admin ? (
                              <Badge variant="secondary">Admin</Badge>
                            ) : (
                              <span className="text-muted-foreground">User</span>
                            )}
                          </td>
                          <td className="py-3 pr-4">
                            {user.active ? (
                              <Badge variant="secondary">Active</Badge>
                            ) : (
                              <Badge variant="destructive">Suspended</Badge>
                            )}
                          </td>
                          <td className="py-3 pr-4 text-muted-foreground">{formatDate(user.created_at)}</td>
                          <td className="py-3 pr-4 text-right">
                            {user.active ? (
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={isSelf || setActiveMutation.isPending}
                                title={isSelf ? "You can't suspend your own account" : undefined}
                                onClick={() => setPendingSuspend(user)}
                              >
                                Suspend
                              </Button>
                            ) : (
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={setActiveMutation.isPending}
                                onClick={() => handleReactivate(user)}
                              >
                                Reactivate
                              </Button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}

        <Dialog open={pendingSuspend !== null} onOpenChange={(open) => !open && setPendingSuspend(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <ShieldAlert className="h-5 w-5 text-destructive" />
                Suspend {pendingSuspend?.email}?
              </DialogTitle>
              <DialogDescription>
                They'll be signed out on their next request and unable to log back in until
                reactivated. Their documents and analysis history are not affected.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setPendingSuspend(null)}>
                Cancel
              </Button>
              <Button variant="destructive" disabled={setActiveMutation.isPending} onClick={handleConfirmSuspend}>
                {setActiveMutation.isPending ? "Suspending..." : "Yes, suspend"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </AppShell>
  );
}
