"use client";

import { usePathname } from "next/navigation";
import { useSwitchOrg } from "@/features/auth/hooks/use-switch-org";
import { useSessionStore } from "@/stores/session";

export function OrgSwitcher() {
  const pathname = usePathname();
  const orgs = useSessionStore((state) => state.orgs);
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const { pendingOrgId, switchToOrg } = useSwitchOrg();
  const activeOrg = orgs.find((org) => org.id === activeOrgId);

  if (orgs.length <= 1) {
    return (
      <div className="min-w-0 rounded-md border border-border bg-background px-3 py-2 text-sm">
        <p className="truncate font-medium">{activeOrg?.name ?? "Choose organization"}</p>
      </div>
    );
  }

  return (
    <select
      aria-label="Organization"
      className="h-10 max-w-[240px] rounded-md border border-input bg-background px-3 text-sm font-medium text-foreground shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      disabled={Boolean(pendingOrgId)}
      onChange={(event) => switchToOrg(event.target.value, pathname)}
      value={activeOrgId ?? ""}
    >
      <option disabled value="">
        Choose organization
      </option>
      {orgs.map((org) => (
        <option key={org.id} value={org.id}>
          {org.name}
        </option>
      ))}
    </select>
  );
}
