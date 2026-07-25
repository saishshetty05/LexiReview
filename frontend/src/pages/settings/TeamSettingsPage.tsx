import { useState } from "react";
import { toast } from "sonner";
import { Mail } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { SettingsLayout } from "@/pages/settings/SettingsLayout";
import { useAuth } from "@/hooks/useAuth";
import type { TeamMember } from "@/types/team";

export function TeamSettingsPage() {
  const { email } = useAuth();
  // Mock only -- no organization/membership concept exists on the backend
  // (see types/team.ts). Seeded with just the current user as "owner" so
  // the page reflects reality (you, alone) rather than fabricated
  // teammates. Invited members are added to local state only and vanish on
  // refresh -- the invite button says so explicitly, it doesn't silently
  // pretend to send anything.
  const [members, setMembers] = useState<TeamMember[]>([
    { id: "self", email: email ?? "you@example.com", role: "owner", status: "active" },
  ]);
  const [inviteEmail, setInviteEmail] = useState("");

  function handleInvite(event: React.FormEvent) {
    event.preventDefault();
    if (!inviteEmail.trim()) return;
    setMembers((prev) => [
      ...prev,
      { id: crypto.randomUUID(), email: inviteEmail.trim(), role: "member", status: "invited" },
    ]);
    setInviteEmail("");
    toast("Team workspaces are coming soon -- this invite isn't actually sent yet.");
  }

  return (
    <SettingsLayout active="team">
      <div className="flex flex-col gap-6">
        <Card>
          <CardHeader>
            <CardTitle>Invite teammates</CardTitle>
            <CardDescription>
              Team workspaces are coming soon. Invites here are a preview only -- nothing is sent
              yet, and this list resets on refresh.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleInvite} className="flex gap-2">
              <Input
                type="email"
                placeholder="teammate@company.com"
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
              />
              <Button type="submit">
                <Mail />
                Invite
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Members</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {members.map((member) => (
              <div key={member.id} className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <Avatar>
                    <AvatarFallback>{member.email[0]?.toUpperCase()}</AvatarFallback>
                  </Avatar>
                  <div>
                    <p className="text-sm font-medium">{member.email}</p>
                    <p className="text-xs capitalize text-muted-foreground">{member.role}</p>
                  </div>
                </div>
                <Badge variant={member.status === "active" ? "secondary" : "outline"}>
                  {member.status === "active" ? "Active" : "Invited"}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </SettingsLayout>
  );
}
