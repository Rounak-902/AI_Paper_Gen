"use client";

import { Building2, CheckCircle2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useSwitchOrg } from "@/features/auth/hooks/use-switch-org";
import { useSessionStore } from "@/stores/session";

export function SelectOrgScreen() {
  const searchParams = useSearchParams();
  const status = useSessionStore((state) => state.status);
  const orgs = useSessionStore((state) => state.orgs);
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const user = useSessionStore((state) => state.user);
  const { error, pendingOrgId, switchToOrg } = useSwitchOrg();
  const nextPath = searchParams.get("next") ?? "/select-org";

  if (status === "loading") {
    return (
      <div className="space-y-5">
        <div className="h-7 w-48 animate-pulse rounded bg-muted" />
        <div className="grid gap-3 md:grid-cols-2">
          <div className="h-32 animate-pulse rounded-lg bg-muted" />
          <div className="h-32 animate-pulse rounded-lg bg-muted" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">Select Organization</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {user ? `${user.name}, choose the organization for this session.` : "Choose the organization for this session."}
        </p>
      </div>
      {error ? <Alert className="border-destructive/40 bg-destructive/10 text-destructive">{error}</Alert> : null}
      {orgs.length === 0 ? (
        <Alert>No organization memberships were found for this account.</Alert>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {orgs.map((org) => {
            const isActive = org.id === activeOrgId;
            const isPending = org.id === pendingOrgId;

            return (
              <Card className="overflow-hidden" key={org.id}>
                <CardHeader>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
                        <Building2 className="h-5 w-5" />
                      </span>
                      <div>
                        <CardTitle className="text-base">{org.name}</CardTitle>
                        <CardDescription>{org.role.replace("_", " ")}</CardDescription>
                      </div>
                    </div>
                    {isActive ? <CheckCircle2 className="h-5 w-5 text-primary" aria-label="Current organization" /> : null}
                  </div>
                </CardHeader>
                <CardContent>
                  <Button
                    className="w-full"
                    disabled={isPending}
                    onClick={() => switchToOrg(org.id, nextPath)}
                    type="button"
                    variant={isActive ? "secondary" : "default"}
                  >
                    {isPending ? "Switching" : isActive ? "Current organization" : "Use this organization"}
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
