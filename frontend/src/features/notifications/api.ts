import { apiClient } from "@/services/api-client";
import type {
  MarkAllRead,
  NotificationItem,
  NotificationPage,
  UnreadCount,
} from "@/features/notifications/types";

export const notificationKeys = {
  all: (orgId: string) => ["notifications", orgId] as const,
  count: (orgId: string) => ["notifications", orgId, "unread-count"] as const,
  feed: (orgId: string, unreadOnly: boolean) =>
    ["notifications", orgId, "feed", unreadOnly ? "unread" : "all"] as const,
};

export function listNotifications({
  cursor,
  limit = 20,
  signal,
  unreadOnly,
}: {
  cursor?: string | null;
  limit?: number;
  signal?: AbortSignal;
  unreadOnly: boolean;
}) {
  const query = new URLSearchParams({ limit: String(limit), unreadOnly: String(unreadOnly) });
  if (cursor) query.set("cursor", cursor);
  return apiClient<NotificationPage>(`/api/v1/notifications?${query}`, { signal });
}

export function getUnreadCount(signal?: AbortSignal) {
  return apiClient<UnreadCount>("/api/v1/notifications/unread-count", { signal });
}

export function markNotificationRead(notificationId: string) {
  return apiClient<NotificationItem>(`/api/v1/notifications/${notificationId}/read`, {
    method: "POST",
  });
}

export function markAllNotificationsRead() {
  return apiClient<MarkAllRead>("/api/v1/notifications/read-all", { method: "POST" });
}
