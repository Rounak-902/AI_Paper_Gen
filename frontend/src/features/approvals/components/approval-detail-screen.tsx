"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  Loader2,
  MessageSquarePlus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  addApprovalComment,
  approvalKeys,
  approveApprovalRequest,
  getApprovalDiff,
  getApprovalHistory,
  rejectApprovalRequest,
  startApprovalReview,
  withdrawApproval,
} from "@/features/approvals/api";
import { canManageApprovals, isSeparationBlocked } from "@/features/approvals/policy";
import {
  approvalCommentSchema,
  rejectionSchema,
  type ApprovalCommentValues,
  type RejectionValues,
} from "@/features/approvals/schema";
import type { ApprovalRequest } from "@/features/approvals/types";
import { getExport, prepareExportDownload, savePreparedDownload } from "@/features/exports/api";
import { canAccessAnswerKeyExports } from "@/features/exports/policy";
import { getPaperCoverageReport, paperBuilderKeys } from "@/features/paper-builder/api";
import { ApiError } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";

type TransitionKind = "review" | "approve" | "withdraw";

export function ApprovalDetailScreen() {
  const params = useParams<{ requestId: string }>();
  const requestId = params.requestId;
  const queryClient = useQueryClient();
  const orgId = useSessionStore((state) => state.activeOrgId);
  const role = useSessionStore((state) => state.role);
  const userId = useSessionStore((state) => state.user?.id);
  const canReview = canManageApprovals(role);
  const canAnswerKey = canAccessAnswerKeyExports(role);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showReject, setShowReject] = useState(false);

  const history = useQuery({
    queryKey: approvalKeys.detail(orgId ?? "none", requestId),
    queryFn: ({ signal }) => getApprovalHistory(requestId, signal),
    enabled: Boolean(orgId && requestId),
  });
  const request = history.data?.request;
  const diff = useQuery({
    queryKey: request ? approvalKeys.diff(orgId ?? "none", request.paperId, request.paperVersionId) : ["approvals", "diff", "pending"],
    queryFn: ({ signal }) => getApprovalDiff(request!.paperId, request!.paperVersionId, signal),
    enabled: Boolean(request),
  });
  const coverage = useQuery({
    queryKey: request ? paperBuilderKeys.coverageReport(orgId ?? "none", request.paperId, request.paperVersionId) : ["approvals", "coverage", "pending"],
    queryFn: ({ signal }) => getPaperCoverageReport(request!.paperId, request!.paperVersionId, signal),
    enabled: Boolean(request),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: approvalKeys.detail(orgId ?? "none", requestId) }),
      queryClient.invalidateQueries({ queryKey: approvalKeys.queue(orgId ?? "none") }),
    ]);
  };

  const transition = useMutation({
    mutationFn: ({ kind, request: source }: { kind: TransitionKind; request: ApprovalRequest }) => {
      if (kind === "review") return startApprovalReview(source);
      if (kind === "approve") return approveApprovalRequest(source);
      return withdrawApproval(source, "Approval withdrawn by an administrator.");
    },
    onMutate: () => { setActionError(null); setNotice(null); },
    onSuccess: async (_, variables) => {
      await refresh();
      setNotice(variables.kind === "review" ? "Review claimed." : variables.kind === "approve" ? "Paper approved." : "Approval withdrawn; the paper returned to draft.");
    },
    onError: async (error) => {
      setActionError(actionErrorMessage(error));
      if (error instanceof ApiError && error.status === 409) await refresh();
    },
  });

  if (history.isLoading) return <DetailSkeleton />;
  if (history.error || !history.data || !request) {
    return <DetailError error={history.error} retry={() => void history.refetch()} />;
  }

  const selfBlocked = isSeparationBlocked(request, userId);
  const approvalWasWithdrawn = history.data.events.some((event) => event.action === "approval_withdrawn");
  const canComment = canReview && !selfBlocked && (request.status === "submitted" || request.status === "review");

  return (
    <div className="mx-auto max-w-7xl space-y-5" data-approval-detail={request.id}>
      <Button asChild size="sm" variant="ghost"><Link href="/admin/approvals"><ArrowLeft />Back to review queue</Link></Button>

      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2"><Badge variant={statusVariant(request.status)}>{label(request.status)}</Badge><Badge variant="outline">Row version {request.rowVersion}</Badge>{request.separationOfDutiesRequired ? <Badge variant="outline"><ShieldCheck />BR-6 on</Badge> : null}</div>
          <h2 className="mt-3 text-2xl font-semibold tracking-tight">Approval review</h2>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{request.id}</p>
        </div>
        <ArtifactDownloads request={request} showAnswerKey={canAnswerKey} />
      </header>

      {selfBlocked && canReview ? <Alert className="border-amber-200 bg-amber-50 text-amber-950" data-separation-blocked><ShieldCheck className="mr-2 inline h-4 w-4" /><strong>You can’t review or approve a paper you authored.</strong> BR-6 requires a different identity. Reviewer controls remain visible but disabled so the reason is explicit.</Alert> : null}
      {!canReview ? <Alert data-view-only><strong>View-only status.</strong> Review actions and answer-key access require a coaching administrator or super administrator org role.</Alert> : null}
      {notice ? <Alert className="border-emerald-200 bg-emerald-50 text-emerald-950" data-approval-notice>{notice}</Alert> : null}
      {actionError ? <Alert className="border-destructive/30 bg-destructive/5 text-destructive" data-approval-error>{actionError}</Alert> : null}

      {canReview ? (
        <Card data-decision-actions>
          <CardHeader><CardTitle className="text-lg">Decision actions</CardTitle><CardDescription>Actions follow submitted → review → approved or rejected. There is no pending-request cancellation action.</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {request.status === "submitted" ? <Button data-approval-action="review" disabled={selfBlocked || transition.isPending} onClick={() => transition.mutate({ kind: "review", request })} type="button">{transition.isPending ? <Loader2 className="animate-spin" /> : <ShieldCheck />}Start review</Button> : null}
              {request.status === "review" ? <Button data-approval-action="approve" disabled={selfBlocked || transition.isPending} onClick={() => transition.mutate({ kind: "approve", request })} type="button">{transition.isPending ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}Approve</Button> : null}
              {request.status === "review" ? <Button data-approval-action="reject" disabled={selfBlocked || transition.isPending} onClick={() => setShowReject((value) => !value)} type="button" variant="destructive"><XCircle />Reject</Button> : null}
              {request.status === "approved" && !approvalWasWithdrawn ? <Button data-approval-action="withdraw" disabled={transition.isPending} onClick={() => transition.mutate({ kind: "withdraw", request })} type="button" variant="outline"><RotateCcw />Withdraw approval</Button> : null}
            </div>
            {selfBlocked ? <p className="text-sm text-amber-900">The API exposes the machine-readable <code>SEPARATION_OF_DUTIES</code> reason and returns 403 for a direct self-review attempt.</p> : null}
            {approvalWasWithdrawn ? <p className="text-sm text-muted-foreground">This approval was already withdrawn. The decision remains in the immutable history and the paper is back in draft.</p> : null}
            {showReject && request.status === "review" ? <RejectForm request={request} onDone={async () => { setShowReject(false); await refresh(); setNotice("Paper rejected with the required decision comment."); }} onError={setActionError} /> : null}
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(340px,1fr)]">
        <div className="space-y-5">
          <DiffCard data={diff.data} loading={diff.isLoading} error={diff.error} />
          <CoverageCard data={coverage.data} loading={coverage.isLoading} error={coverage.error} snapshotHash={request.coverageReportSha256} />
        </div>
        <div className="space-y-5">
          <RequestFacts request={request} />
          <CommentsCard canComment={canComment} comments={history.data.comments} requestId={request.id} onAdded={refresh} />
          <HistoryCard events={history.data.events} />
        </div>
      </div>
    </div>
  );
}

function ArtifactDownloads({ request, showAnswerKey }: { request: ApprovalRequest; showAnswerKey: boolean }) {
  const download = useMutation({
    mutationFn: async ({ artifactId, purpose }: { artifactId: string; purpose: "paper" | "answer-key" }) => {
      const artifact = await getExport(artifactId);
      const prepared = await prepareExportDownload(artifactId);
      return savePreparedDownload(prepared, `approval-${shortId(request.paperId)}-${purpose}.${artifact.format}`, () => prepareExportDownload(artifactId));
    },
  });
  return (
    <div className="flex flex-wrap gap-2">
      <Button data-download-paper disabled={download.isPending} onClick={() => download.mutate({ artifactId: request.paperExportArtifactId, purpose: "paper" })} type="button" variant="outline">{download.isPending && download.variables?.purpose === "paper" ? <Loader2 className="animate-spin" /> : <Download />}Paper</Button>
      {showAnswerKey ? <Button data-download-answer-key disabled={download.isPending} onClick={() => download.mutate({ artifactId: request.answerKeyExportArtifactId, purpose: "answer-key" })} type="button" variant="outline">{download.isPending && download.variables?.purpose === "answer-key" ? <Loader2 className="animate-spin" /> : <Download />}Answer key</Button> : null}
      {download.error ? <p className="w-full text-xs text-destructive">{errorMessage(download.error)}</p> : null}
    </div>
  );
}

function RejectForm({ request, onDone, onError }: { request: ApprovalRequest; onDone: () => Promise<void>; onError: (message: string) => void }) {
  const form = useForm<RejectionValues>({ resolver: zodResolver(rejectionSchema), defaultValues: { comment: "" } });
  const mutation = useMutation({
    mutationFn: (values: RejectionValues) => rejectApprovalRequest(request, values.comment),
    onSuccess: onDone,
    onError: (error) => onError(actionErrorMessage(error)),
  });
  return (
    <form className="rounded-lg border border-red-200 bg-red-50 p-4" data-rejection-form onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
      <label className="text-sm font-medium" htmlFor="rejection-comment">Required rejection comment</label>
      <textarea className={textareaClass} id="rejection-comment" rows={4} {...form.register("comment")} />
      {form.formState.errors.comment ? <p className="mt-1 text-xs text-red-700">{form.formState.errors.comment.message}</p> : null}
      <Button className="mt-3" disabled={mutation.isPending} type="submit" variant="destructive">{mutation.isPending ? <Loader2 className="animate-spin" /> : <XCircle />}Confirm rejection</Button>
    </form>
  );
}

function CommentsCard({ canComment, comments, requestId, onAdded }: { canComment: boolean; comments: Array<{ id: string; createdBy: string; slotId: string | null; body: string; createdAt: string }>; requestId: string; onAdded: () => Promise<void> }) {
  const form = useForm<ApprovalCommentValues>({ resolver: zodResolver(approvalCommentSchema), defaultValues: { body: "", slotId: "" } });
  const mutation = useMutation({
    mutationFn: (values: ApprovalCommentValues) => addApprovalComment(requestId, { body: values.body, slotId: values.slotId || null }),
    onSuccess: async () => { form.reset(); await onAdded(); },
  });
  return (
    <Card>
      <CardHeader><CardTitle className="text-lg">Review comments</CardTitle><CardDescription>Use a slot ID for a per-question comment, or leave it blank for a round-level note.</CardDescription></CardHeader>
      <CardContent className="space-y-4">
        {canComment ? (
          <form className="space-y-3" data-comment-form onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
            <label className="block text-sm font-medium">Question slot ID (optional)<input className={inputClass} {...form.register("slotId")} /></label>
            <label className="block text-sm font-medium">Comment<textarea className={textareaClass} rows={3} {...form.register("body")} /></label>
            {form.formState.errors.body ? <p className="text-xs text-destructive">{form.formState.errors.body.message}</p> : null}
            {mutation.error ? <p className="text-xs text-destructive">{errorMessage(mutation.error)}</p> : null}
            <Button disabled={mutation.isPending} size="sm" type="submit" variant="outline">{mutation.isPending ? <Loader2 className="animate-spin" /> : <MessageSquarePlus />}Add comment</Button>
          </form>
        ) : null}
        {comments.length === 0 ? <p className="text-sm text-muted-foreground">No comments yet.</p> : comments.map((comment) => <article className="rounded-md border p-3 text-sm" key={comment.id}><div className="flex justify-between gap-2 text-xs text-muted-foreground"><span>{comment.slotId ? `Question ${comment.slotId}` : "Review round"}</span><time>{new Date(comment.createdAt).toLocaleString()}</time></div><p className="mt-2 whitespace-pre-wrap">{comment.body}</p></article>)}
      </CardContent>
    </Card>
  );
}

function DiffCard({ data, loading, error }: { data: Awaited<ReturnType<typeof getApprovalDiff>> | undefined; loading: boolean; error: unknown }) {
  return <Card><CardHeader><CardTitle className="text-lg">Version diff</CardTitle><CardDescription>Shallow structural diff; question answer values are never included.</CardDescription></CardHeader><CardContent>{loading ? <LoadingLine /> : error ? <ErrorLine error={error} /> : data ? <div className="space-y-4" data-version-diff><div className="flex items-center gap-2 text-sm"><Badge variant="outline">{label(data.basis)}</Badge><span>{data.baseVersionId ? `from ${shortId(data.baseVersionId)}` : "No earlier baseline"}</span></div><div className="grid gap-3 sm:grid-cols-3"><Metric label="Added questions" value={data.addedSlotIds.length} /><Metric label="Removed questions" value={data.removedSlotIds.length} /><Metric label="Changed questions" value={data.changedSlots.length} /></div>{data.changedSlots.length ? <ul className="space-y-2">{data.changedSlots.map((change) => <li className="rounded-md border p-3 text-sm" key={change.slotId}><strong>{change.slotId}</strong><span className="ml-2 text-muted-foreground">{(change.changedFields ?? []).join(", ")}</span></li>)}</ul> : <p className="text-sm text-muted-foreground">No question fields changed from the selected baseline.</p>}</div> : null}</CardContent></Card>;
}

function CoverageCard({ data, loading, error, snapshotHash }: { data: Awaited<ReturnType<typeof getPaperCoverageReport>> | undefined; loading: boolean; error: unknown; snapshotHash: string }) {
  return <Card><CardHeader><CardTitle className="text-lg">Coverage report at submission</CardTitle><CardDescription>The approval contract exposes the accepted snapshot hash but not its JSON. The facts below are read from the same immutable paper version.</CardDescription></CardHeader><CardContent>{loading ? <LoadingLine /> : error ? <ErrorLine error={error} /> : data ? <div className="space-y-4" data-coverage-report><div className="flex flex-wrap items-center gap-2"><Badge variant={data.solverOutcome === "optimal" || data.solverOutcome === "feasible" ? "default" : "secondary"}>{label(data.solverOutcome)}</Badge><span className="font-mono text-xs text-muted-foreground" title={snapshotHash}>Accepted SHA-256 {snapshotHash.slice(0, 16)}…</span></div><div className="space-y-2">{data.coverage.map((fact, index) => <div className="grid gap-2 rounded-md border p-3 text-sm sm:grid-cols-[1fr_1fr_1fr_auto]" key={`${fact.dimension}-${fact.target}-${index}`}><span>{label(fact.dimension)} · {fact.target}</span><span className="text-muted-foreground">Target {fact.targetValue}</span><span>Actual {fact.actual}</span><Badge variant={fact.satisfied ? "default" : "secondary"}>{fact.satisfied ? "Met" : "Missed"}</Badge></div>)}</div>{data.compromises.length ? <div><h4 className="mb-2 font-medium">Accepted compromises</h4>{data.compromises.map((item, index) => <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm" key={`${item.tier}-${index}`}>{item.reason} ({item.target} → {item.actual})</p>)}</div> : <p className="text-sm text-muted-foreground">No recorded compromises.</p>}</div> : null}</CardContent></Card>;
}

function RequestFacts({ request }: { request: ApprovalRequest }) {
  return <Card><CardHeader><CardTitle className="text-lg">Request facts</CardTitle></CardHeader><CardContent className="space-y-2 text-sm"><Fact label="Paper" value={request.paperId} /><Fact label="Version" value={request.paperVersionId} /><Fact label="Submitted by" value={request.submittedBy} /><Fact label="Claimed by" value={request.reviewStartedBy ?? "Not claimed"} /><Fact label="Decided by" value={request.approverId ?? "Not decided"} /><Fact label="Submitted" value={new Date(request.submittedAt).toLocaleString()} />{request.decisionComment ? <div className="rounded-md border p-3"><span className="text-xs text-muted-foreground">Decision comment</span><p className="mt-1 whitespace-pre-wrap">{request.decisionComment}</p></div> : null}</CardContent></Card>;
}

function HistoryCard({ events }: { events: Array<{ id: string; action: string; actorId: string; fromStatus: string | null; toStatus: string; comment: string | null; occurredAt: string }> }) {
  return <Card><CardHeader><CardTitle className="text-lg">Audit history</CardTitle></CardHeader><CardContent className="space-y-3">{events.map((event) => <article className="border-l-2 pl-3 text-sm" key={event.id}><div className="flex flex-wrap items-center justify-between gap-2"><strong>{label(event.action)}</strong><time className="text-xs text-muted-foreground">{new Date(event.occurredAt).toLocaleString()}</time></div><p className="text-xs text-muted-foreground">{event.fromStatus ?? "draft"} → {event.toStatus} · {shortId(event.actorId)}</p>{event.comment ? <p className="mt-1">{event.comment}</p> : null}</article>)}</CardContent></Card>;
}

function Fact({ label: factLabel, value }: { label: string; value: string }) { return <div><span className="text-xs text-muted-foreground">{factLabel}</span><p className="break-all font-mono text-xs">{value}</p></div>; }
function Metric({ label: metricLabel, value }: { label: string; value: number }) { return <div className="rounded-md bg-muted p-3"><strong className="block text-xl">{value}</strong><span className="text-xs text-muted-foreground">{metricLabel}</span></div>; }
function LoadingLine() { return <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="animate-spin" />Loading…</p>; }
function ErrorLine({ error }: { error: unknown }) { return <p className="flex items-center gap-2 text-sm text-destructive"><AlertTriangle />{errorMessage(error)}</p>; }
function DetailSkeleton() { return <div className="mx-auto max-w-7xl space-y-5"><div className="h-8 w-48 animate-pulse rounded bg-muted" /><div className="h-40 animate-pulse rounded-lg bg-muted" /><div className="grid gap-5 lg:grid-cols-2"><div className="h-96 animate-pulse rounded-lg bg-muted" /><div className="h-96 animate-pulse rounded-lg bg-muted" /></div></div>; }
function DetailError({ error, retry }: { error: unknown; retry: () => void }) { return <div className="mx-auto max-w-2xl rounded-lg border border-destructive/30 bg-destructive/5 p-8 text-center"><h2 className="text-xl font-semibold">Approval request could not be loaded</h2><p className="mt-2 text-sm text-destructive">{errorMessage(error)}</p><Button className="mt-4" onClick={retry} type="button" variant="outline"><RefreshCw />Retry</Button></div>; }

function actionErrorMessage(error: unknown) {
  if (error instanceof ApiError && error.code === "SEPARATION_OF_DUTIES") return "BR-6 blocked this action: the submitter and reviewer/approver must be different identities.";
  if (error instanceof ApiError && error.status === 409) return "This approval request changed elsewhere. The latest server state has been refreshed; review it before trying again.";
  if (error instanceof ApiError && error.code === "REJECTION_COMMENT_REQUIRED") return "A rejection comment is required.";
  return errorMessage(error);
}
function errorMessage(error: unknown) { return error instanceof Error ? error.message : "Something went wrong. Please try again."; }
function shortId(value: string) { return `${value.slice(0, 8)}…`; }
function label(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase()); }
function statusVariant(status: ApprovalRequest["status"]): "default" | "secondary" | "outline" { return status === "approved" || status === "review" ? "default" : status === "rejected" ? "outline" : "secondary"; }

const inputClass = "mt-1 block h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring";
const textareaClass = "mt-1 block w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring";
