import { Suspense } from "react";
import { PaperNotificationDestination } from "@/features/notifications/components/paper-notification-destination";

export default async function PaperNotificationPage({ params }: { params: Promise<{ paperId: string }> }) {
  const { paperId } = await params;
  return <Suspense fallback={null}><PaperNotificationDestination paperId={paperId} /></Suspense>;
}
