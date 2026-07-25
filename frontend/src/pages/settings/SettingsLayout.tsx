import { useNavigate } from "react-router-dom";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AppShell } from "@/components/layout/AppShell";

const TABS = [
  { value: "account", path: "/settings", label: "Account" },
  { value: "team", path: "/settings/team", label: "Team" },
  { value: "billing", path: "/settings/billing", label: "Billing" },
];

export function SettingsLayout({
  active,
  children,
}: {
  active: "account" | "team" | "billing";
  children: React.ReactNode;
}) {
  const navigate = useNavigate();

  return (
    <AppShell title="Settings">
      <div className="mx-auto max-w-3xl p-6">
        <Tabs value={active} onValueChange={(value) => {
          const tab = TABS.find((t) => t.value === value);
          if (tab) navigate(tab.path);
        }}>
          <TabsList className="mb-6">
            {TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        {children}
      </div>
    </AppShell>
  );
}
