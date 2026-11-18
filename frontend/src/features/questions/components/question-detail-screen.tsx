"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, GitBranch, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getQuestion, getQuestionLineage } from "@/features/questions/api";
import { humanize, type Question } from "@/features/questions/types";
import { ApiError } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";

export function QuestionDetailScreen() {
  const params = useParams<{ questionId: string }>();
  const questionId = params.questionId;
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const questionQuery = useQuery({
    queryKey: [activeOrgId, "questions", "detail", questionId],
    queryFn: ({ signal }) => getQuestion(activeOrgId!, questionId, signal),
    enabled: Boolean(activeOrgId && questionId),
  });
  const lineageQuery = useQuery({
    queryKey: [activeOrgId, "questions", "lineage", questionId],
    queryFn: ({ signal }) => getQuestionLineage(activeOrgId!, questionId, signal),
    enabled: Boolean(activeOrgId && questionId),
  });

  if (questionQuery.isLoading) {
    return <QuestionDetailSkeleton />;
  }

  if (questionQuery.error || !questionQuery.data) {
    return <QuestionDetailError error={questionQuery.error} retry={() => void questionQuery.refetch()} />;
  }

  const question = questionQuery.data;
  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <Button asChild size="sm" variant="ghost">
        <Link href="/question-bank"><ArrowLeft /> Back to Question Bank</Link>
      </Button>

      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{humanize(question.qtype)}</Badge>
            <Badge variant={question.status === "approved" ? "default" : "secondary"}>
              {humanize(question.status)}
            </Badge>
            {question.is_current ? <Badge variant="outline">Current version</Badge> : null}
          </div>
          <h2 className="mt-3 text-2xl font-semibold tracking-tight">Question detail</h2>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{question.id}</p>
        </div>
        <div className="rounded-lg border bg-card px-4 py-3 text-sm">
          <span className="text-muted-foreground">Version </span>
          <strong>{question.version_number}</strong>
          <span className="mx-2 text-muted-foreground">·</span>
          <span>{question.marks} marks</span>
        </div>
      </header>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(300px,1fr)]">
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Question</CardTitle>
              <CardDescription>{humanize(question.language)} · {humanize(question.difficulty)}</CardDescription>
            </CardHeader>
            <CardContent>
              <p className="whitespace-pre-wrap text-lg leading-8">{question.stem}</p>
            </CardContent>
          </Card>
          <StructuredCard title="Options or supporting content" value={question.options} />
          <StructuredCard title="Answer" value={question.answer} />
          <StructuredCard title="Rubric" value={question.rubric} />
          <ProvenanceCard question={question} />
        </div>

        <aside className="space-y-5">
          <MetadataCard question={question} />
          <LineageCard
            error={lineageQuery.error}
            isLoading={lineageQuery.isLoading}
            lineage={lineageQuery.data ?? []}
            retry={() => void lineageQuery.refetch()}
          />
        </aside>
      </div>
    </div>
  );
}

function StructuredCard({ title, value }: { title: string; value: unknown }) {
  if (value === null || value === undefined) return null;
  return (
    <Card>
      <CardHeader><CardTitle className="text-lg">{title}</CardTitle></CardHeader>
      <CardContent><StructuredValue value={value} /></CardContent>
    </Card>
  );
}

function StructuredValue({ value }: { value: unknown }) {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return <p className="whitespace-pre-wrap">{String(value)}</p>;
  }
  return (
    <pre className="max-h-[460px] overflow-auto rounded-md bg-muted p-4 text-xs leading-5">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function ProvenanceCard({ question }: { question: Question }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">Provenance</CardTitle>
        </div>
        <CardDescription>Origin and traceability are retained with every question.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <Meta label="Source" value={humanize(question.source)} />
          <Meta label="Scope" value={humanize(question.scope)} />
          <Meta label="Board ID" value={question.board_id} mono />
          <Meta label="Class ID" value={question.class_id} mono />
          <Meta label="Subject ID" value={question.subject_id} mono />
          <Meta label="Chapter ID" value={question.chapter_id} mono />
          <Meta label="Created by" value={question.created_by} mono />
          <Meta label="Prompt version" value={question.prompt_version_id} mono />
          <Meta label="Content hash" value={question.content_hash} mono />
        </dl>
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Source metadata</h4>
          {Object.keys(question.meta).length > 0 ? (
            <StructuredValue value={question.meta} />
          ) : (
            <p className="text-sm text-muted-foreground">No additional source metadata was recorded.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function MetadataCard({ question }: { question: Question }) {
  return (
    <Card>
      <CardHeader><CardTitle className="text-lg">Classification</CardTitle></CardHeader>
      <CardContent>
        <dl className="grid gap-3 text-sm">
          <Meta label="Type" value={humanize(question.qtype)} />
          <Meta label="Difficulty" value={humanize(question.difficulty)} />
          <Meta label="Bloom level" value={question.bloom ? humanize(question.bloom) : null} />
          <Meta label="Language" value={humanize(question.language)} />
          <Meta label="Learning outcomes" value={question.learning_outcome_ids.length ? String(question.learning_outcome_ids.length) : null} />
          <Meta label="Competencies" value={question.competency_ids.length ? String(question.competency_ids.length) : null} />
          <Meta label="Updated" value={formatDate(question.updated_at)} />
        </dl>
      </CardContent>
    </Card>
  );
}

function LineageCard({
  error,
  isLoading,
  lineage,
  retry,
}: {
  error: unknown;
  isLoading: boolean;
  lineage: Question[];
  retry: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-primary" />
          <CardTitle className="text-lg">Version lineage</CardTitle>
        </div>
        <CardDescription>Oldest version first. Approved edits fork a new immutable version.</CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? <p className="text-sm text-muted-foreground">Loading lineage…</p> : null}
        {error ? (
          <div className="space-y-2 text-sm" role="alert">
            <p>{error instanceof Error ? error.message : "Lineage could not be loaded."}</p>
            <Button onClick={retry} size="sm" type="button" variant="outline">Try again</Button>
          </div>
        ) : null}
        {!isLoading && !error && lineage.length === 0 ? (
          <p className="text-sm text-muted-foreground">No version history is available.</p>
        ) : null}
        {!error && lineage.length > 0 ? (
          <ol className="space-y-0">
            {lineage.map((version, index) => (
              <li className="relative flex gap-3 pb-5 last:pb-0" key={version.id}>
                {index < lineage.length - 1 ? <span className="absolute left-2 top-5 h-full w-px bg-border" /> : null}
                <span className="relative z-[1] mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border bg-background">
                  {version.is_current ? <CheckCircle2 className="h-4 w-4 text-primary" /> : null}
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link className="font-medium hover:text-primary hover:underline" href={"/question-bank/" + version.id}>
                      Version {version.version_number}
                    </Link>
                    {version.is_current ? <Badge variant="outline">Current</Badge> : null}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{humanize(version.status)} · {formatDate(version.created_at)}</p>
                  <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground" title={version.id}>{version.id}</p>
                </div>
              </li>
            ))}
          </ol>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Meta({ label, mono = false, value }: { label: string; mono?: boolean; value: string | null | undefined }) {
  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "break-all font-mono text-xs" : "break-words"}>{value || "—"}</dd>
    </div>
  );
}

function QuestionDetailError({ error, retry }: { error: unknown; retry: () => void }) {
  const isNotFound = error instanceof ApiError && error.status === 404;
  return (
    <div className="mx-auto max-w-2xl rounded-lg border bg-card p-8 text-center" role="alert">
      <h2 className="text-xl font-semibold">{isNotFound ? "Question not found" : "Question could not be loaded"}</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {error instanceof Error ? error.message : "The requested question is unavailable."}
      </p>
      <div className="mt-5 flex justify-center gap-2">
        {!isNotFound ? <Button onClick={retry} type="button" variant="outline">Try again</Button> : null}
        <Button asChild><Link href="/question-bank">Return to Question Bank</Link></Button>
      </div>
    </div>
  );
}

function QuestionDetailSkeleton() {
  return (
    <div className="mx-auto max-w-7xl space-y-5" aria-label="Loading question detail">
      <div className="h-9 w-44 animate-pulse rounded bg-muted" />
      <div className="h-24 animate-pulse rounded-lg bg-muted" />
      <div className="grid gap-5 lg:grid-cols-[2fr_1fr]">
        <div className="h-96 animate-pulse rounded-lg bg-muted" />
        <div className="h-72 animate-pulse rounded-lg bg-muted" />
      </div>
    </div>
  );
}

function formatDate(value: unknown) {
  if (typeof value !== "string" && typeof value !== "number") return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}
