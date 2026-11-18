import type { NotificationItem } from "@/features/notifications/types";

export const NOTIFICATION_BADGE_POLL_MS = 5_000;

export type NotificationPresentation = {
  kind: "approval" | "digest" | "export" | "generation" | "system";
  label: string;
  tone: "blue" | "green" | "red" | "violet" | "slate";
  batched: boolean;
};

export function notificationPresentation(item: Pick<NotificationItem, "code" | "type">): NotificationPresentation {
  if (item.code === "NT-12") {
    return { batched: true, kind: "digest", label: "Digest", tone: "violet" };
  }
  if (item.code === "NT-4") {
    return { batched: false, kind: "approval", label: "Review", tone: "blue" };
  }
  if (item.code === "NT-5") {
    return { batched: false, kind: "approval", label: "Approved", tone: "green" };
  }
  if (item.code === "NT-6") {
    return { batched: false, kind: "approval", label: "Changes requested", tone: "red" };
  }
  if (item.code === "NT-9") {
    return { batched: false, kind: "export", label: "Export", tone: "green" };
  }
  if (item.code === "NT-10") {
    return { batched: false, kind: "export", label: "Export failed", tone: "red" };
  }
  if (item.code === "NT-3") {
    return { batched: false, kind: "generation", label: "Paper ready", tone: "green" };
  }
  if (item.code === "NT-8") {
    return { batched: false, kind: "generation", label: "Generation failed", tone: "red" };
  }
  return { batched: false, kind: "system", label: "Update", tone: "slate" };
}

/** The backend contract supplies an app-relative route, never a bare entity id. */
export function safeNotificationLink(link: string | null | undefined) {
  if (!link || !link.startsWith("/") || link.startsWith("//") || link.includes("\\")) return null;
  try {
    const parsed = new URL(link, "https://papergen.local");
    return parsed.origin === "https://papergen.local" ? `${parsed.pathname}${parsed.search}${parsed.hash}` : null;
  } catch {
    return null;
  }
}

export function notificationAgeGroup(createdAt: string, now = new Date()) {
  const created = new Date(createdAt);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const createdDay = new Date(created.getFullYear(), created.getMonth(), created.getDate());
  const days = Math.floor((today.getTime() - createdDay.getTime()) / 86_400_000);
  if (days <= 0) return "Today";
  if (days <= 7) return "Earlier this week";
  return "Older";
}
