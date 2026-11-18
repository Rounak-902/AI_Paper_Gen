"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Clock3, RefreshCw, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { approvalKeys, listApprovalQueue } from "@/features/approvals/api";
import { canManageApprovals, isSeparationBlocked } from "@/features/approvals/policy";
import { useSessionStore } from "@/stores/session";

const QUEUE_POLL_MS = 10_000;

export function ApprovalQueueScreen() {
  const orgId = useSessionStore((state) => state.activeOrgId);
  const role = useSessionStore((state) => state.role);
  const userId = useSessionStore((state) => state.user?.id);
  const canManage = canManageApprovals(role);
  const query = useQuery({
    queryKey: approvalKeys.queue(orgId ?? "none"),
    queryFn: ({ signal }) => listApprovalQueue(signal),
    enabled: Boolean(orgId && canManage),
    refetchInterval: QUEUE_POLL_MS,
    refetchIntervalInBackground: false,
  });

  if (!canManage) {
    return (
      <div className="mx-auto max-w-2xl rounded-lg border bg-card p-8 text-center" data-approval-queue-forbidden>
        <h2 className="text-xl font-semibold">Approval queue is restricted</h2>
        <p className="mt-2 text-sm text-muted-foreground">A coaching administrator or super administrator role is required. Paper authors can view their request status from a direct review link.</p>
      </div>
    );
  }

  const requests = query.data ?? [];
  return (
    <div className="mx-auto max-w-6xl space-y-6" data-approval-queue>
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <div className="flex items-center gap-2"><h2 className="text-2xl font-semibold tracking-tight">Review queue</h2><Badge variant="outline">Pooled queue</Badge></div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Submitted and in-review requests for this organization. There is no reviewer assignment dimension; the list refreshes every 10 seconds.</p>
        </div>
        <Button disabled={query.isFetching} onClick={() => void query.refetch()} type="button" variant="outline"><RefreshCw className={query.isFetching ? "animate-spin" : undefined} />Refresh</Button>
      </header>

      {query.error ? <Alert className="border-destructive/30 bg-destructive/5 text-destructive">{errorMessage(query.error)}</Alert> : null}
      {query.isLoading ? <div className="grid gap-4 md:grid-cols-2">{Array.from({ length: 4 }).map((_, index) => <div className="h-48 animate-pulse rounded-lg bg-muted" key={index} />)}</div> : null}
      {!query.isLoading && !query.error && requests.length === 0 ? <Card className="border-dashed"><CardHeader><CardTitle className="text-lg">Queue is clear</CardTitle><CardDescription>No submitted or in-review requests are waiting.</CardDescription></CardHeader></Card> : null}

      <div className="grid gap-4 md:grid-cols-2">
        {requests.map((request) => {
          const selfBlocked = isSeparationBlocked(request, userId);
          return (
            <Card data-approval-request={request.id} key={request.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div><CardTitle className="text-base">Paper {shortId(request.paperId)}</CardTitle><CardDescription>Version {shortId(request.paperVersionId)}</CardDescription></div>
                  <Badge variant={request.status === "review" ? "default" : "secondary"}>{request.status === "review" ? "In review" : "Submitted"}</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-1 text-xs text-muted-foreground">
                  <p className="flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" />Submitted {new Date(request.submittedAt).toLocaleString()}</p>
                  <p>Submitter {shortId(request.submittedBy)} · row version {request.rowVersion}</p>
                </div>
                {selfBlocked ? <Alert className="border-amber-200 bg-amber-50 text-amber-950" data-separation-blocked><ShieldCheck className="mr-2 inline h-4 w-4" />You can’t review or approve a paper you authored. BR-6 requires another reviewer.</Alert> : null}
                <Button asChild className="w-full" variant={selfBlocked ? "outline" : "default"}><Link href={`/admin/approvals/${request.id}`}>{selfBlocked ? "View status only" : "Open review"}<ArrowRight /></Link></Button>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function shortId(value: string) { return `${value.slice(0, 8)}…`; }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : "The approval queue could not be loaded."; }

