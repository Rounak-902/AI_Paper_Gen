import type { ReactNode } from "react";
import { Suspense } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { SessionProvider } from "@/providers/session-provider";
import { RealtimeHubProvider } from "@/providers/realtime-hub-provider";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="min-h-screen bg-background" />}>
      <SessionProvider>
        <RealtimeHubProvider>
          <AppShell>{children}</AppShell>
        </RealtimeHubProvider>
      </SessionProvider>
    </Suspense>
  );
}
