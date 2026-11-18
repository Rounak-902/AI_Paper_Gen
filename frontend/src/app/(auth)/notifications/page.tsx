"use client";

import { Card, CardContent } from "@/components/ui/card";
import { NotificationInbox } from "@/features/notifications/components/notification-centre";
import { useSessionStore } from "@/stores/session";

export default function NotificationsPage() {
  const orgId = useSessionStore((state) => state.activeOrgId);
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h2 className="text-2xl font-semibold">Notification centre</h2>
        <p className="text-sm text-muted-foreground">Review durable updates for your active organization.</p>
      </div>
      <Card><CardContent className="p-0"><NotificationInbox orgId={orgId} /></CardContent></Card>
    </div>
  );
}
