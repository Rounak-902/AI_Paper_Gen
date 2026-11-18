"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, Download, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { listPaperApprovalRounds } from "@/features/approvals/api";
import { getExport, prepareExportDownload, savePreparedDownload } from "@/features/exports/api";
import type { ExportJob } from "@/features/exports/types";
import { useSessionStore } from "@/stores/session";

export function PaperNotificationDestination({ paperId }: { paperId: string }) {
  const search = useSearchParams();
  const router = useRouter();
  const orgId = useSessionStore((state) => state.activeOrgId);
  const exportId = search.get("export");
  const reviewMode = search.get("mode") === "review";
  const approvalStatus = search.get("approval");
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const exportQuery = useQuery({
    queryKey: ["exports", orgId, "notification", exportId],
    queryFn: ({ signal }) => getExport(exportId!, signal),
    enabled: Boolean(orgId && exportId),
  });
  const rounds = useQuery({
    queryKey: ["approvals", orgId, "paper", paperId],
    queryFn: ({ signal }) => listPaperApprovalRounds(paperId, signal),
    enabled: Boolean(orgId && !exportId),
  });

  useEffect(() => {
    if (!rounds.data || exportId) return;
    const matching = reviewMode
      ? rounds.data.find((item) => item.status === "review" || item.status === "submitted")
      : approvalStatus
        ? rounds.data.find((item) => item.status === approvalStatus)
        : rounds.data[0];
    if (matching) router.replace(`/admin/approvals/${matching.id}`);
  }, [approvalStatus, exportId, reviewMode, rounds.data, router]);

  if (exportId) {
    if (exportQuery.isPending) return <DestinationLoading message="Loading export…" />;
    if (exportQuery.isError || !exportQuery.data || exportQuery.data.paperId !== paperId) {
      return <UnavailableDestination />;
    }
    return (
      <div className="mx-auto max-w-2xl">
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div><CardTitle>Export artifact</CardTitle><CardDescription>This notification links to a completed paper export.</CardDescription></div>
              <Badge variant="outline">{exportQuery.data.status}</Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 rounded-md border border-border p-4 text-sm sm:grid-cols-2">
              <Meta label="Format" value={exportQuery.data.format.toUpperCase()} />
              <Meta label="Purpose" value={exportQuery.data.artifactType === "answer_key" ? "Answer key" : "Paper"} />
              <Meta label="Paper" value={paperId} />
              <Meta label="Export" value={exportQuery.data.id} />
            </div>
            {downloadError ? <Alert className="border-destructive/50 text-destructive"><AlertCircle className="h-4 w-4" /><span>{downloadError}</span></Alert> : null}
            <Button disabled={downloading || exportQuery.data.status !== "done"} onClick={() => download(exportQuery.data)} type="button">
              {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              Download {exportQuery.data.format.toUpperCase()}
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (rounds.isPending) return <DestinationLoading message="Opening linked paper…" />;
  if (rounds.isError || !rounds.data?.length) return <UnavailableDestination />;
  return <DestinationLoading message="Opening approval…" />;

  async function download(job: ExportJob) {
    setDownloading(true);
    setDownloadError(null);
    try {
      const prepared = await prepareExportDownload(job.id);
      await savePreparedDownload(prepared, `paper-${paperId}.${job.format}`, () => prepareExportDownload(job.id));
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "The export could not be downloaded.");
    } finally {
      setDownloading(false);
    }
  }
}

function DestinationLoading({ message }: { message: string }) {
  return <div className="mx-auto flex max-w-lg items-center justify-center gap-2 rounded-lg border border-border bg-card p-10 text-sm text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" />{message}</div>;
}

export function UnavailableDestination() {
  return (
    <div className="mx-auto max-w-lg">
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><AlertCircle className="h-5 w-5 text-amber-600" />Destination unavailable</CardTitle><CardDescription>The linked item may have been removed, may be outside your active organization, or your access may have changed.</CardDescription></CardHeader>
        <CardContent className="flex gap-2"><Button asChild variant="outline"><Link href="/notifications">Back to notifications</Link></Button><Button asChild variant="ghost"><Link href="/dashboard">Go to dashboard</Link></Button></CardContent>
      </Card>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div className="min-w-0"><p className="text-xs text-muted-foreground">{label}</p><p className="truncate font-medium" title={value}>{value}</p></div>;
}
