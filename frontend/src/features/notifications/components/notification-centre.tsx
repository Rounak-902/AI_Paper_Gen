"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Bell,
  Check,
  CheckCheck,
  CheckCircle2,
  FileDown,
  FileText,
  Inbox,
  Layers3,
  Loader2,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import {
  getUnreadCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  notificationKeys,
} from "@/features/notifications/api";
import {
  NOTIFICATION_BADGE_POLL_MS,
  notificationAgeGroup,
  notificationPresentation,
  safeNotificationLink,
} from "@/features/notifications/presentation";
import type { NotificationItem } from "@/features/notifications/types";
import { useSessionStore } from "@/stores/session";

export function NotificationCentre() {
  const [open, setOpen] = useState(false);
  const orgId = useSessionStore((state) => state.activeOrgId);
  const authenticated = useSessionStore((state) => state.status === "authenticated");
  const count = useQuery({
    queryKey: notificationKeys.count(orgId ?? "none"),
    queryFn: ({ signal }) => getUnreadCount(signal),
    enabled: authenticated && Boolean(orgId),
    refetchInterval: NOTIFICATION_BADGE_POLL_MS,
    refetchIntervalInBackground: false,
    staleTime: 0,
  });
  const unreadCount = count.data?.unreadCount ?? 0;
  const label = unreadCount
    ? `Notifications, ${unreadCount} unread`
    : "Notifications, none unread";

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <Button
          aria-label={label}
          className="relative"
          data-testid="notification-bell"
          size="icon"
          title={label}
          type="button"
          variant="ghost"
        >
          <Bell className="h-5 w-5" />
          {unreadCount > 0 ? (
            <span
              className="absolute -right-1 -top-1 min-w-5 rounded-full bg-destructive px-1 text-center text-[10px] font-bold leading-5 text-destructive-foreground"
              data-testid="notification-badge"
            >
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          ) : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(92vw,430px)] p-0">
        <NotificationInbox enabled={open} onNavigate={() => setOpen(false)} orgId={orgId} panel />
      </PopoverContent>
    </Popover>
  );
}

export function NotificationInbox({
  enabled = true,
  onNavigate,
  orgId,
  panel = false,
}: {
  enabled?: boolean;
  onNavigate?: () => void;
  orgId: string | null;
  panel?: boolean;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const feed = useInfiniteQuery({
    queryKey: notificationKeys.feed(orgId ?? "none", unreadOnly),
    queryFn: ({ pageParam, signal }) =>
      listNotifications({ cursor: pageParam, signal, unreadOnly }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
    enabled: enabled && Boolean(orgId),
    staleTime: 0,
  });
  const items = useMemo(() => feed.data?.pages.flatMap((page) => page.items) ?? [], [feed.data]);

  async function refreshNotificationQueries() {
    if (!orgId) return;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: notificationKeys.count(orgId) }),
      queryClient.invalidateQueries({ queryKey: notificationKeys.all(orgId) }),
    ]);
  }

  const readOne = useMutation({
    mutationFn: markNotificationRead,
    onSuccess: refreshNotificationQueries,
  });
  const readAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: refreshNotificationQueries,
  });

  async function openNotification(item: NotificationItem) {
    const destination = safeNotificationLink(item.link);
    setActionError(null);
    try {
      if (!item.readAt) await readOne.mutateAsync(item.id);
      if (!destination) {
        setActionError("This notification has no safe destination. Its message remains available here.");
        return;
      }
      onNavigate?.();
      router.push(destination);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "This notification could not be marked as read.");
    }
  }

  async function handleReadAll() {
    setActionError(null);
    try {
      await readAll.mutateAsync();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Notifications could not be marked as read.");
    }
  }

  return (
    <section aria-label="Notification centre" data-testid="notification-centre">
      <div className="flex items-center justify-between gap-3 border-b border-border p-4">
        <div>
          <h2 className="font-semibold">Notifications</h2>
          <p className="text-xs text-muted-foreground">Updates for this organization</p>
        </div>
        <Button
          disabled={readAll.isPending || (items.every((item) => item.readAt) && !feed.hasNextPage)}
          onClick={handleReadAll}
          size="sm"
          type="button"
          variant="ghost"
        >
          {readAll.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCheck className="h-4 w-4" />}
          Mark all read
        </Button>
      </div>
      <div className="flex gap-1 border-b border-border px-4 py-2" role="group" aria-label="Notification filter">
        <Button onClick={() => setUnreadOnly(false)} size="sm" type="button" variant={unreadOnly ? "ghost" : "secondary"}>All</Button>
        <Button onClick={() => setUnreadOnly(true)} size="sm" type="button" variant={unreadOnly ? "secondary" : "ghost"}>Unread</Button>
        <Button aria-label="Refresh notifications" className="ml-auto" onClick={() => feed.refetch()} size="icon" type="button" variant="ghost">
          <RefreshCw className={cn("h-4 w-4", feed.isFetching && "animate-spin")} />
        </Button>
      </div>
      {actionError ? <Alert className="m-3 border-destructive/50 text-destructive"><AlertCircle className="h-4 w-4" /><span>{actionError}</span></Alert> : null}
      <div className={cn("overflow-y-auto", panel ? "max-h-[min(65vh,560px)]" : "max-h-none")}>
        {feed.isPending ? <LoadingRows /> : null}
        {feed.isError ? (
          <div className="p-6 text-center">
            <AlertCircle className="mx-auto mb-2 h-5 w-5 text-destructive" />
            <p className="text-sm font-medium">Notifications could not be loaded.</p>
            <Button className="mt-3" onClick={() => feed.refetch()} size="sm" type="button" variant="outline">Retry</Button>
          </div>
        ) : null}
        {!feed.isPending && !feed.isError && items.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground">
            <Inbox className="mx-auto mb-2 h-7 w-7" />
            <p className="text-sm font-medium">{unreadOnly ? "You’re all caught up" : "No notifications yet"}</p>
          </div>
        ) : null}
        <NotificationRows items={items} onOpen={openNotification} />
        {feed.hasNextPage ? (
          <div className="border-t border-border p-3 text-center">
            <Button disabled={feed.isFetchingNextPage} onClick={() => feed.fetchNextPage()} size="sm" type="button" variant="outline">
              {feed.isFetchingNextPage ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Load older
            </Button>
          </div>
        ) : null}
      </div>
      {panel ? (
        <div className="border-t border-border p-2 text-center">
          <Button asChild onClick={onNavigate} size="sm" variant="link"><Link href="/notifications">Open notification centre</Link></Button>
        </div>
      ) : null}
    </section>
  );
}

function NotificationRows({ items, onOpen }: { items: NotificationItem[]; onOpen: (item: NotificationItem) => void }) {
  let lastGroup = "";
  return items.map((item) => {
    const group = notificationAgeGroup(item.createdAt);
    const showGroup = group !== lastGroup;
    lastGroup = group;
    const presentation = notificationPresentation(item);
    const Icon = iconFor(presentation.kind, presentation.tone);
    const destination = safeNotificationLink(item.link);
    return (
      <div key={item.id}>
        {showGroup ? <p className="bg-muted/40 px-4 py-1.5 text-[11px] font-semibold uppercase text-muted-foreground">{group}</p> : null}
        <button
          className={cn("flex w-full gap-3 border-t border-border/70 p-4 text-left transition-colors hover:bg-muted/50", !item.readAt && "bg-primary/[0.04]")}
          data-notification-code={item.code}
          data-notification-id={item.id}
          onClick={() => onOpen(item)}
          type="button"
        >
          <span className={cn("mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full", toneClass(presentation.tone))}>
            <Icon className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-start gap-2">
              <span className="min-w-0 flex-1 text-sm font-medium">{item.title}</span>
              {!item.readAt ? <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary" aria-label="Unread" /> : null}
            </span>
            {item.body ? <span className="mt-1 block text-xs leading-5 text-muted-foreground">{item.body}</span> : null}
            <span className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <Badge variant="outline">{presentation.label}</Badge>
              {presentation.batched ? <span>Batched update</span> : null}
              <time dateTime={item.createdAt}>{formatRelative(item.createdAt)}</time>
              {item.readAt ? <span title={new Date(item.readAt).toLocaleString()}><Check className="mr-1 inline h-3 w-3" />Read</span> : null}
              {!destination ? <span>Destination unavailable</span> : null}
            </span>
          </span>
        </button>
      </div>
    );
  });
}

function iconFor(kind: ReturnType<typeof notificationPresentation>["kind"], tone: ReturnType<typeof notificationPresentation>["tone"]) {
  if (kind === "digest") return Layers3;
  if (kind === "export") return FileDown;
  if (kind === "approval") return tone === "green" ? CheckCircle2 : tone === "red" ? XCircle : ShieldCheck;
  if (kind === "generation") return tone === "red" ? AlertCircle : FileText;
  return Bell;
}

function toneClass(tone: ReturnType<typeof notificationPresentation>["tone"]) {
  return { blue: "bg-blue-100 text-blue-700", green: "bg-emerald-100 text-emerald-700", red: "bg-red-100 text-red-700", violet: "bg-violet-100 text-violet-700", slate: "bg-slate-100 text-slate-700" }[tone];
}

function formatRelative(value: string) {
  const delta = Date.now() - new Date(value).getTime();
  const minutes = Math.max(0, Math.floor(delta / 60_000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(value).toLocaleDateString();
}

function LoadingRows() {
  return <div className="space-y-3 p-4">{Array.from({ length: 3 }).map((_, index) => <div className="h-20 animate-pulse rounded-md bg-muted" key={index} />)}</div>;
}
