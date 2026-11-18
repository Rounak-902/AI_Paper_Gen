"use client";

import { useMemo, useRef, useState, type DragEvent } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  CloudUpload,
  Code2,
  Database,
  Eye,
  FileJson2,
  Filter,
  GitCompareArrows,
  Globe2,
  Layers3,
  Loader2,
  LockKeyhole,
  Play,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Sparkles,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  applyManifest,
  previewManifest,
  validateManifest,
} from "@/features/curriculum/api";
import {
  SAMPLE_MANIFEST_TEXT,
  validateManifestText,
  type CurriculumDocument,
} from "@/features/curriculum/schema";
import {
  type ImportAction,
  type ImportEntity,
  type ImportOperation,
  type ImportPlan,
  type ImportResult,
} from "@/features/curriculum/types";
import { ApiError } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";
import {
  CatalogDialog,
  EntityMetric,
  ManifestOutline,
  ManifestSummary,
  StatusPill,
} from "./curriculum-studio-ui";

const MAX_FILE_BYTES = 10 * 1024 * 1024;

const workflow = [
  { key: "source", label: "Source", icon: CloudUpload },
  { key: "review", label: "Review", icon: Code2 },
  { key: "validate", label: "Validate", icon: ShieldCheck },
  { key: "compare", label: "Compare", icon: GitCompareArrows },
  { key: "publish", label: "Publish", icon: Database },
] as const;

type Stage = (typeof workflow)[number]["key"];
type ServerValidation = Awaited<ReturnType<typeof validateManifest>>;
type ActionFilter = "all" | ImportOperation;

const entityLabels: Record<ImportEntity, string> = {
  boards: "Board",
  classes: "Classes",
  curriculum_versions: "Version",
  subjects: "Subjects",
  chapters: "Chapters",
  learning_outcomes: "Outcomes",
};

export function CurriculumIngestionStudio() {
  const role = useSessionStore((state) => state.role);
  const appStatus = useSessionStore((state) => state.status);
  const user = useSessionStore((state) => state.user);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [stage, setStage] = useState<Stage>("source");
  const [manifestText, setManifestText] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [serverValidation, setServerValidation] = useState<ServerValidation | null>(null);
  const [plan, setPlan] = useState<ImportPlan | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [confirmCode, setConfirmCode] = useState("");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("all");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<"validate" | "preview" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const local = useMemo(() => validateManifestText(manifestText), [manifestText]);
  const currentIndex = workflow.findIndex((item) => item.key === stage);
  const filteredActions = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (plan?.actions ?? []).filter((action) => {
      const matchesFilter = actionFilter === "all" || action.operation === actionFilter;
      const matchesSearch =
        !query ||
        action.key.toLowerCase().includes(query) ||
        action.path.toLowerCase().includes(query) ||
        entityLabels[action.entity].toLowerCase().includes(query);
      return matchesFilter && matchesSearch;
    });
  }, [actionFilter, plan, search]);

  if (appStatus === "loading") {
    return <StudioLoading />;
  }

  if (role !== "super_admin") {
    return <AccessDenied />;
  }

  function replaceManifest(text: string, name: string | null, nextStage: Stage = "review") {
    setManifestText(text);
    setFileName(name);
    setStage(nextStage);
    setServerValidation(null);
    setPlan(null);
    setResult(null);
    setConfirmCode("");
    setError(null);
  }

  async function acceptFile(file: File) {
    if (file.size > MAX_FILE_BYTES) {
      setError("That file is larger than the 10 MiB API limit.");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".json")) {
      setError("Choose a .json curriculum manifest.");
      return;
    }
    replaceManifest(await file.text(), file.name);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files.item(0);
    if (file) void acceptFile(file);
  }

  async function runValidation() {
    if (!local.document) {
      setStage("validate");
      setError("Resolve the local manifest issues before contacting the catalog.");
      return;
    }
    setBusy("validate");
    setError(null);
    try {
      const validated = await validateManifest(local.document);
      setServerValidation(validated);
      setStage("validate");
    } catch (validationError) {
      setError(readError(validationError));
    } finally {
      setBusy(null);
    }
  }

  async function runPreview() {
    if (!local.document) {
      setStage("validate");
      return;
    }
    setBusy("preview");
    setError(null);
    try {
      const validated = await validateManifest(local.document);
      const nextPlan = await previewManifest(local.document);
      setServerValidation(validated);
      setPlan(nextPlan);
      setStage("compare");
    } catch (previewError) {
      setError(readError(previewError));
    } finally {
      setBusy(null);
    }
  }

  async function runApply() {
    if (!local.document || !plan) return;
    setBusy("apply");
    setError(null);
    try {
      const nextResult = await applyManifest(local.document, plan.planDigest);
      setResult(nextResult);
      setStage("publish");
    } catch (applyError) {
      setError(readError(applyError));
    } finally {
      setBusy(null);
    }
  }

  function goBack() {
    setStage(workflow[Math.max(0, currentIndex - 1)]!.key);
    setError(null);
  }

  const boardCode = local.document?.board.code ?? plan?.boardCode ?? "";

  return (
    <div className="mx-auto max-w-[1540px] space-y-6 pb-16">
      <section className="relative overflow-hidden rounded-[30px] border border-slate-800 bg-slate-950 px-6 py-7 text-white shadow-[0_30px_90px_-45px_rgba(15,23,42,0.9)] md:px-9 md:py-9">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_85%_5%,rgba(45,212,191,0.23),transparent_30%),radial-gradient(circle_at_58%_125%,rgba(245,158,11,0.16),transparent_36%)]" />
        <div className="relative grid gap-7 xl:grid-cols-[minmax(0,1fr)_390px] xl:items-end">
          <div>
            <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-teal-300/20 bg-teal-300/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.16em] text-teal-200">
              <Globe2 className="h-3.5 w-3.5" />
              Global curriculum control room
            </div>
            <h2 className="max-w-4xl text-3xl font-semibold tracking-[-0.04em] md:text-5xl">
              Ingest official curricula with every change in view.
            </h2>
            <p className="mt-4 max-w-3xl text-sm leading-6 text-slate-300 md:text-base md:leading-7">
              Validate a versioned manifest, compare it with the live global catalog, and commit one audited,
              atomic import—without touching the database directly.
            </p>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/[0.065] p-4 backdrop-blur">
            <div className="flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-300/15 text-teal-200">
                <ShieldCheck className="h-5 w-5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">Using your app session</p>
                <p className="mt-0.5 truncate text-xs text-slate-400">
                  {user ? `${user.name} · ${user.email}` : "Authenticated super-admin"}
                </p>
              </div>
              <StatusPill tone="success">Authenticated</StatusPill>
            </div>
            <div className="mt-4 border-t border-white/10 pt-4">
              <Button
                className="w-full bg-teal-400 text-slate-950 hover:bg-teal-300"
                onClick={() => setCatalogOpen(true)}
              >
                <Eye className="h-4 w-4" /> View catalog
              </Button>
            </div>
          </div>
        </div>
      </section>

      <section aria-label="Import workflow" className="rounded-3xl border border-border bg-white p-3 shadow-sm">
        <ol className="grid gap-2 sm:grid-cols-5">
          {workflow.map((item, index) => {
            const Icon = item.icon;
            const active = stage === item.key;
            const completed = index < currentIndex || (item.key === "publish" && Boolean(result));
            const accessible =
              index <= currentIndex ||
              (item.key === "review" && Boolean(manifestText)) ||
              (item.key === "validate" && Boolean(manifestText)) ||
              (item.key === "compare" && Boolean(plan)) ||
              (item.key === "publish" && Boolean(plan?.ready));
            return (
              <li key={item.key}>
                <button
                  aria-current={active ? "step" : undefined}
                  className={`flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-left transition ${
                    active
                      ? "bg-slate-950 text-white shadow-sm"
                      : completed
                        ? "bg-teal-50 text-teal-900 hover:bg-teal-100"
                        : "text-slate-500 hover:bg-slate-50"
                  }`}
                  disabled={!accessible}
                  onClick={() => accessible && setStage(item.key)}
                  type="button"
                >
                  <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${active ? "bg-teal-300/15 text-teal-200" : completed ? "bg-white text-primary" : "bg-slate-100"}`}>
                    {completed ? <CheckCircle2 className="h-4.5 w-4.5" /> : <Icon className="h-4.5 w-4.5" />}
                  </span>
                  <span>
                    <span className="block text-[10px] font-semibold uppercase tracking-[0.15em] opacity-60">0{index + 1}</span>
                    <span className="block text-sm font-semibold">{item.label}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </section>

      {error ? (
        <div aria-live="polite" className="flex items-start gap-3 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <AlertCircle className="mt-0.5 h-4.5 w-4.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button className="font-semibold underline underline-offset-2" onClick={() => setError(null)} type="button">Dismiss</button>
        </div>
      ) : null}

      {stage === "source" ? (
        <SourceStage
          fileInputRef={fileInputRef}
          onDrop={handleDrop}
          onFile={(file) => void acceptFile(file)}
          onSample={() => replaceManifest(SAMPLE_MANIFEST_TEXT, "sample-curriculum-v1.json")}
        />
      ) : null}

      {stage === "review" ? (
        <ReviewStage
          document={local.document}
          fileName={fileName}
          issueCount={local.issues.length}
          manifestText={manifestText}
          onBack={goBack}
          onChange={(value) => replaceManifest(value, fileName, "review")}
          onContinue={() => setStage("validate")}
        />
      ) : null}

      {stage === "validate" ? (
        <ValidateStage
          actorEmail={user?.email ?? "super-admin"}
          busy={busy}
          document={local.document}
          issues={local.issues}
          onBack={goBack}
          onPreview={() => void runPreview()}
          onValidate={() => void runValidation()}
          serverValidation={serverValidation}
        />
      ) : null}

      {stage === "compare" && plan ? (
        <CompareStage
          actionFilter={actionFilter}
          actions={filteredActions}
          onBack={goBack}
          onFilter={setActionFilter}
          onPublish={() => setStage("publish")}
          onRefresh={() => void runPreview()}
          onSearch={setSearch}
          plan={plan}
          search={search}
          busy={busy === "preview"}
        />
      ) : null}

      {stage === "compare" && !plan ? (
        <EmptyPlan onBack={goBack} onPreview={() => void runPreview()} busy={busy === "preview"} />
      ) : null}

      {stage === "publish" ? (
        <PublishStage
          boardCode={boardCode}
          busy={busy === "apply"}
          confirmCode={confirmCode}
          onBack={goBack}
          onCatalog={() => setCatalogOpen(true)}
          onConfirmCode={setConfirmCode}
          onPublish={() => void runApply()}
          onRestart={() => replaceManifest("", null, "source")}
          plan={plan}
          result={result}
        />
      ) : null}

      <CatalogDialog onClose={() => setCatalogOpen(false)} open={catalogOpen} />
    </div>
  );
}

function SourceStage({
  fileInputRef,
  onDrop,
  onFile,
  onSample,
}: {
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  onFile: (file: File) => void;
  onSample: () => void;
}) {
  return (
    <section className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(330px,0.55fr)]">
      <div className="overflow-hidden rounded-3xl border border-border bg-white shadow-sm">
        <StageHeader eyebrow="New ingestion" icon={Upload} title="Choose the source manifest" />
        <div className="p-6">
          <div
            className="group flex min-h-[330px] w-full flex-col items-center justify-center rounded-[24px] border border-dashed border-slate-300 bg-[linear-gradient(145deg,rgba(240,253,250,0.72),rgba(248,250,252,0.95))] px-6 text-center transition hover:border-primary/60 hover:bg-teal-50/80"
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
          >
            <span className="flex h-16 w-16 items-center justify-center rounded-2xl border border-teal-200 bg-white text-primary shadow-[0_14px_32px_-16px_rgba(13,148,136,0.9)] transition group-hover:-translate-y-1">
              <FileJson2 className="h-7 w-7" />
            </span>
            <h3 className="mt-5 text-xl font-semibold tracking-tight text-slate-950">Drop an official curriculum JSON file</h3>
            <p className="mt-2 max-w-lg text-sm leading-6 text-slate-500">
              One board and one curriculum version per manifest. The file is checked in your browser before a live request is made.
            </p>
            <input
              accept="application/json,.json"
              className="sr-only"
              onChange={(event) => {
                const file = event.target.files?.item(0);
                if (file) onFile(file);
                event.target.value = "";
              }}
              ref={fileInputRef}
              type="file"
            />
            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <Button className="bg-slate-950 text-white hover:bg-slate-800" onClick={() => fileInputRef.current?.click()}>
                Browse files <ArrowRight className="h-4 w-4" />
              </Button>
              <Button onClick={onSample} variant="outline">
                <Sparkles className="h-4 w-4" /> Start from example
              </Button>
            </div>
            <p className="mt-4 text-xs text-slate-400">JSON · Schema v1 · Maximum 10 MiB</p>
          </div>
        </div>
      </div>
      <aside className="rounded-3xl border border-border bg-white p-6 shadow-sm">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">Readiness checklist</p>
            <h3 className="mt-1 text-xl font-semibold tracking-tight">Before you import</h3>
          </div>
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-50 text-primary"><BookOpen className="h-5 w-5" /></span>
        </div>
        <div className="mt-6 space-y-3">
          {[
            ["Official source reference", "Record the ministry, board, or publisher URL."],
            ["Stable outcome codes", "Codes make repeated imports safely idempotent."],
            ["Version policy", "Start in draft unless activation is intentional."],
            ["Lineage references", "Point revised chapters to an earlier active version."],
          ].map(([title, description], index) => (
            <div className="flex gap-3 rounded-2xl border border-border bg-slate-50 p-3.5" key={title}>
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white text-xs font-bold text-primary shadow-sm">{index + 1}</span>
              <div><p className="text-sm font-semibold text-slate-800">{title}</p><p className="mt-0.5 text-xs leading-5 text-slate-500">{description}</p></div>
            </div>
          ))}
        </div>
        <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
          Global reference data affects every organization. The final write is guarded by a live plan digest and one atomic transaction.
        </div>
      </aside>
    </section>
  );
}

function ReviewStage({
  document,
  fileName,
  issueCount,
  manifestText,
  onBack,
  onChange,
  onContinue,
}: {
  document: CurriculumDocument | null;
  fileName: string | null;
  issueCount: number;
  manifestText: string;
  onBack: () => void;
  onChange: (value: string) => void;
  onContinue: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-3xl border border-border bg-white shadow-sm">
      <StageHeader
        eyebrow={fileName ?? "Unsaved manifest"}
        icon={Code2}
        title="Review the manifest and its hierarchy"
        trailing={<StatusPill tone={document ? "success" : "danger"}>{document ? "Locally valid" : `${issueCount} issue${issueCount === 1 ? "" : "s"}`}</StatusPill>}
      />
      <div className="grid min-h-[620px] xl:grid-cols-[minmax(0,1.28fr)_minmax(360px,0.72fr)]">
        <div className="border-b border-border p-5 xl:border-b-0 xl:border-r">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div><p className="text-sm font-semibold">Raw JSON editor</p><p className="mt-0.5 text-xs text-muted-foreground">Edits are validated continuously in this tab.</p></div>
            <span className="rounded-lg bg-slate-100 px-2.5 py-1 font-mono text-[11px] text-slate-500">schemaVersion: 1</span>
          </div>
          <textarea
            aria-label="Curriculum manifest JSON"
            className="min-h-[525px] w-full resize-y rounded-2xl border border-slate-800 bg-slate-950 p-4 font-mono text-[13px] leading-6 text-slate-200 shadow-inner outline-none transition focus:border-teal-400 focus:ring-2 focus:ring-teal-400/25"
            onChange={(event) => onChange(event.target.value)}
            spellCheck={false}
            value={manifestText}
          />
        </div>
        <aside className="bg-slate-50/65 p-5">
          {document ? (
            <>
              <ManifestSummary document={document} />
              <div className="mt-5"><ManifestOutline document={document} /></div>
            </>
          ) : (
            <div className="flex min-h-[460px] flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center">
              <AlertCircle className="h-9 w-9 text-amber-500" />
              <p className="mt-4 font-semibold">The outline will return when the JSON is valid</p>
              <p className="mt-2 max-w-xs text-sm leading-6 text-muted-foreground">Continue to validation to see exact JSON paths for every issue.</p>
            </div>
          )}
        </aside>
      </div>
      <StageFooter backLabel="Choose another source" onBack={onBack}>
        <Button className="bg-slate-950 text-white hover:bg-slate-800" onClick={onContinue}>Review validation <ArrowRight className="h-4 w-4" /></Button>
      </StageFooter>
    </section>
  );
}

function ValidateStage({
  actorEmail,
  busy,
  document,
  issues,
  onBack,
  onPreview,
  onValidate,
  serverValidation,
}: {
  actorEmail: string;
  busy: string | null;
  document: CurriculumDocument | null;
  issues: Array<{ path: string; message: string }>;
  onBack: () => void;
  onPreview: () => void;
  onValidate: () => void;
  serverValidation: ServerValidation | null;
}) {
  const checks = [
    { label: "JSON and schema", passed: Boolean(document), note: document ? "Schema v1 structure is valid." : "Resolve the path-addressed issues below." },
    { label: "Semantic keys", passed: Boolean(document), note: "Natural keys, duplicates, lineage, and status intent." },
    { label: "App authentication", passed: true, note: `Using the signed-in super-admin session for ${actorEmail}.` },
    { label: "Server contract", passed: Boolean(serverValidation), note: serverValidation ? `Accepted · ${shortDigest(serverValidation.documentDigest)}` : "Run the server validation gate." },
  ];
  return (
    <section className="overflow-hidden rounded-3xl border border-border bg-white shadow-sm">
      <StageHeader eyebrow="Preflight gates" icon={ShieldCheck} title="Validate before comparing live state" />
      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,1.1fr)_420px]">
        <div>
          <div className="grid gap-3 sm:grid-cols-2">
            {checks.map((check) => (
              <div className={`rounded-2xl border p-4 ${check.passed ? "border-teal-200 bg-teal-50/70" : "border-slate-200 bg-slate-50"}`} key={check.label}>
                <div className="flex items-center gap-2">
                  {check.passed ? <CheckCircle2 className="h-5 w-5 text-primary" /> : <AlertCircle className="h-5 w-5 text-slate-400" />}
                  <p className="font-semibold text-slate-900">{check.label}</p>
                </div>
                <p className="mt-2 text-sm leading-5 text-slate-500">{check.note}</p>
              </div>
            ))}
          </div>
          <div className="mt-5 rounded-2xl border border-border">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div><p className="text-sm font-semibold">Local validation report</p><p className="mt-0.5 text-xs text-muted-foreground">Exact paths make correction deterministic.</p></div>
              <StatusPill tone={issues.length ? "danger" : "success"}>{issues.length ? `${issues.length} blocking` : "No issues"}</StatusPill>
            </div>
            <div className="max-h-[360px] overflow-y-auto p-3">
              {issues.length ? issues.map((issue, index) => (
                <div className="mb-2 rounded-xl border border-red-100 bg-red-50 p-3 last:mb-0" key={`${issue.path}-${index}`}>
                  <code className="break-all text-xs font-semibold text-red-700">{issue.path}</code>
                  <p className="mt-1 text-sm text-red-950">{issue.message}</p>
                </div>
              )) : (
                <div className="flex items-center gap-3 rounded-xl bg-teal-50 p-4 text-sm text-teal-950"><CheckCircle2 className="h-5 w-5 text-primary" /> All local schema and semantic checks passed.</div>
              )}
            </div>
          </div>
        </div>
        <aside className="rounded-3xl bg-slate-950 p-6 text-white">
          <Server className="h-8 w-8 text-teal-300" />
          <h3 className="mt-5 text-xl font-semibold">Server-authoritative validation</h3>
          <p className="mt-2 text-sm leading-6 text-slate-300">The backend re-validates the same document under your audited super-admin context. No curriculum rows are written at this step.</p>
          {serverValidation ? (
            <div className="mt-6 space-y-3 rounded-2xl border border-teal-300/20 bg-teal-300/10 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-teal-100"><CheckCircle2 className="h-4 w-4" /> Server accepted the manifest</div>
              <InfoPair label="Board" value={serverValidation.boardCode} />
              <InfoPair label="Version" value={serverValidation.versionKey} />
              <InfoPair label="Digest" value={shortDigest(serverValidation.documentDigest)} mono />
            </div>
          ) : null}
          <div className="mt-6 grid gap-2">
            <Button className="border-white/15 bg-white/5 text-white hover:bg-white/10" disabled={!document || busy !== null} onClick={onValidate} variant="outline">
              {busy === "validate" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />} Run server validation
            </Button>
          </div>
        </aside>
      </div>
      <StageFooter backLabel="Edit manifest" onBack={onBack}>
        <Button className="bg-slate-950 text-white hover:bg-slate-800" disabled={!document || busy !== null} onClick={onPreview}>
          {busy === "preview" ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCompareArrows className="h-4 w-4" />} Build live comparison
        </Button>
      </StageFooter>
    </section>
  );
}

function CompareStage({ actionFilter, actions, busy, onBack, onFilter, onPublish, onRefresh, onSearch, plan, search }: {
  actionFilter: ActionFilter;
  actions: ImportAction[];
  busy: boolean;
  onBack: () => void;
  onFilter: (filter: ActionFilter) => void;
  onPublish: () => void;
  onRefresh: () => void;
  onSearch: (value: string) => void;
  plan: ImportPlan;
  search: string;
}) {
  const totals = Object.values(plan.counts).reduce((acc, count) => ({ create: acc.create + count.create, skip: acc.skip + count.skip }), { create: 0, skip: 0 });
  return (
    <section className="overflow-hidden rounded-3xl border border-border bg-white shadow-sm">
      <StageHeader
        eyebrow={`Plan ${shortDigest(plan.planDigest)}`}
        icon={GitCompareArrows}
        title="Compare the manifest with the live catalog"
        trailing={<div className="flex items-center gap-2"><StatusPill tone={plan.atomic ? "success" : "warning"}>{plan.atomic ? "Atomic plan" : "Review plan"}</StatusPill><Button disabled={busy} onClick={onRefresh} size="sm" variant="outline">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />} Refresh</Button></div>}
      />
      <div className="border-b border-border bg-slate-50/70 p-5">
        <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {(Object.keys(plan.counts) as ImportEntity[]).map((entity) => <EntityMetric count={plan.counts[entity]} key={entity} label={entityLabels[entity]} />)}
        </div>
      </div>
      <div className="p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h3 className="font-semibold text-slate-950">Planned actions</h3>
            <p className="mt-1 text-sm text-muted-foreground">{totals.create} create · {totals.skip} skip · lifecycle transitions shown inline</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="relative min-w-[250px]"><span className="sr-only">Search actions</span><Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" /><Input className="pl-9" onChange={(event) => onSearch(event.target.value)} placeholder="Search key or JSON path" value={search} /></label>
            <label className="relative"><span className="sr-only">Filter actions</span><Filter className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" /><select className="h-10 rounded-md border border-input bg-white pl-9 pr-8 text-sm outline-none focus:ring-2 focus:ring-ring" onChange={(event) => onFilter(event.target.value as ActionFilter)} value={actionFilter}><option value="all">All operations</option><option value="create">Create</option><option value="skip">Skip</option><option value="transition">Transition</option></select></label>
          </div>
        </div>
        {plan.issues.length ? <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">{plan.issues.map((issue) => <p key={`${issue.path}-${issue.message}`}><strong>{issue.path}</strong> — {issue.message}</p>)}</div> : null}
        <div className="mt-4 max-h-[580px] space-y-2 overflow-y-auto pr-1">
          {actions.length ? actions.map((action, index) => <ActionRow action={action} index={index} key={`${action.path}-${action.operation}-${index}`} />) : <div className="rounded-2xl border border-dashed border-slate-300 p-10 text-center text-sm text-muted-foreground">No actions match this filter.</div>}
        </div>
      </div>
      <StageFooter backLabel="Validation" onBack={onBack}>
        <Button className="bg-slate-950 text-white hover:bg-slate-800" disabled={!plan.ready} onClick={onPublish}>Review and publish <ArrowRight className="h-4 w-4" /></Button>
      </StageFooter>
    </section>
  );
}

function PublishStage({ boardCode, busy, confirmCode, onBack, onCatalog, onConfirmCode, onPublish, onRestart, plan, result }: {
  boardCode: string;
  busy: boolean;
  confirmCode: string;
  onBack: () => void;
  onCatalog: () => void;
  onConfirmCode: (value: string) => void;
  onPublish: () => void;
  onRestart: () => void;
  plan: ImportPlan | null;
  result: ImportResult | null;
}) {
  if (result) {
    const totals = Object.values(result.counts).reduce((acc, count) => ({ create: acc.create + count.create, skip: acc.skip + count.skip }), { create: 0, skip: 0 });
    return (
      <section className="overflow-hidden rounded-3xl border border-teal-200 bg-white shadow-sm">
        <div className="bg-[linear-gradient(135deg,#042f2e,#0f766e)] px-6 py-10 text-center text-white">
          <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-white/15"><CheckCircle2 className="h-8 w-8 text-teal-200" /></span>
          <p className="mt-5 text-xs font-semibold uppercase tracking-[0.18em] text-teal-200">Import committed</p>
          <h3 className="mt-2 text-3xl font-semibold tracking-tight">{result.boardCode} is in the global catalog</h3>
          <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-teal-50/80">The reviewed plan was rebuilt under lock and committed in one transaction. Reusing the same manifest is safe.</p>
        </div>
        <div className="grid gap-3 p-6 sm:grid-cols-2 lg:grid-cols-4">
          <ResultMetric label="Rows created" value={totals.create} />
          <ResultMetric label="Rows unchanged" value={totals.skip} />
          <ResultMetric label="Board ID" value={shortId(result.boardId)} mono />
          <ResultMetric label="Version ID" value={shortId(result.curriculumVersionId)} mono />
        </div>
        <div className="flex flex-col gap-3 border-t border-border p-5 sm:flex-row sm:justify-center">
          <Button onClick={onCatalog}><Eye className="h-4 w-4" /> Inspect live catalog</Button>
          <Button onClick={onRestart} variant="outline"><Sparkles className="h-4 w-4" /> Start another import</Button>
        </div>
      </section>
    );
  }
  if (!plan) return <EmptyPlan busy={false} onBack={onBack} onPreview={onBack} />;
  const matches = confirmCode === boardCode;
  return (
    <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_440px]">
      <div className="overflow-hidden rounded-3xl border border-border bg-white shadow-sm">
        <StageHeader eyebrow="Final checkpoint" icon={Database} title="Commit the reviewed import" />
        <div className="p-6">
          <div className="rounded-2xl border border-teal-200 bg-teal-50 p-5">
            <div className="flex items-start gap-3"><ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-primary" /><div><p className="font-semibold text-teal-950">Atomic apply is armed</p><p className="mt-1 text-sm leading-6 text-teal-900/75">The service locks the board, rebuilds the plan, verifies <code className="rounded bg-white/80 px-1.5 py-0.5 text-xs">{shortDigest(plan.planDigest)}</code>, and rolls back everything if any action fails.</p></div></div>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            <ResultMetric label="Board" value={plan.boardCode} />
            <ResultMetric label="Version" value={plan.versionKey} />
            <ResultMetric label="Actions" value={plan.actions.length} />
            <ResultMetric label="Plan digest" value={shortDigest(plan.planDigest)} mono />
          </div>
        </div>
        <StageFooter backLabel="Review comparison" onBack={onBack}><span className="text-xs text-muted-foreground">No writes have happened yet.</span></StageFooter>
      </div>
      <aside className="rounded-3xl border border-slate-800 bg-slate-950 p-6 text-white shadow-sm">
        <LockKeyhole className="h-8 w-8 text-amber-300" />
        <h3 className="mt-5 text-xl font-semibold">Confirm the global change</h3>
        <p className="mt-2 text-sm leading-6 text-slate-300">Type the exact board code to make the final action available.</p>
        <label className="mt-6 block"><span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Board code</span><Input autoComplete="off" className="mt-2 border-white/15 bg-white/10 font-mono text-white placeholder:text-slate-500" onChange={(event) => onConfirmCode(event.target.value)} placeholder={boardCode} value={confirmCode} /></label>
        <div className="mt-4 rounded-xl border border-white/10 bg-white/5 p-3 text-xs leading-5 text-slate-400">Publishing global reference data is audited to the connected super-admin and organization context.</div>
        <Button className="mt-5 w-full bg-amber-300 text-slate-950 hover:bg-amber-200" disabled={!matches || busy} onClick={onPublish}>
          {busy ? <><Loader2 className="h-4 w-4 animate-spin" /> Committing atomically…</> : <><Play className="h-4 w-4" /> Commit global curriculum</>}
        </Button>
      </aside>
    </section>
  );
}

function ActionRow({ action, index }: { action: ImportAction; index: number }) {
  const tone = action.operation === "create" ? "success" : action.operation === "transition" ? "warning" : "neutral";
  return (
    <details className="group rounded-2xl border border-border bg-white open:border-slate-300 open:shadow-sm">
      <summary className="flex cursor-pointer list-none items-center gap-3 p-3.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 font-mono text-[11px] font-semibold text-slate-500">{String(index + 1).padStart(2, "0")}</span>
        <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="truncate text-sm font-semibold text-slate-900">{action.key}</p><StatusPill tone={tone}>{action.operation}</StatusPill></div><p className="mt-1 truncate font-mono text-[11px] text-slate-500">{action.path}</p></div>
        <span className="hidden rounded-lg bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-500 sm:block">{entityLabels[action.entity]}</span>
        <ArrowRight className="h-4 w-4 text-slate-400 transition group-open:rotate-90" />
      </summary>
      <div className="grid gap-3 border-t border-border bg-slate-50 p-4 text-xs sm:grid-cols-2">
        <InfoBlock label="Endpoint" value={action.endpoint} />
        <InfoBlock label="Existing ID" value={action.existingId ?? "New row"} />
        {action.fromStatus || action.toStatus ? <InfoBlock label="Lifecycle" value={`${action.fromStatus ?? "new"} → ${action.toStatus ?? "unchanged"}`} /> : null}
        {action.payload ? <pre className="max-h-52 overflow-auto rounded-xl bg-slate-950 p-3 text-[11px] leading-5 text-slate-200 sm:col-span-2">{JSON.stringify(action.payload, null, 2)}</pre> : null}
      </div>
    </details>
  );
}

function EmptyPlan({ busy, onBack, onPreview }: { busy: boolean; onBack: () => void; onPreview: () => void }) {
  return <section className="rounded-3xl border border-border bg-white p-10 text-center shadow-sm"><GitCompareArrows className="mx-auto h-10 w-10 text-slate-400" /><h3 className="mt-4 text-xl font-semibold">No live plan yet</h3><p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-muted-foreground">Generate a server-authoritative comparison before publishing.</p><div className="mt-6 flex justify-center gap-3"><Button onClick={onBack} variant="outline"><ArrowLeft className="h-4 w-4" /> Back</Button><Button disabled={busy} onClick={onPreview}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCompareArrows className="h-4 w-4" />} Build comparison</Button></div></section>;
}

function StageHeader({ eyebrow, icon: Icon, title, trailing }: { eyebrow: string; icon: typeof Layers3; title: string; trailing?: React.ReactNode }) {
  return <header className="flex flex-col gap-4 border-b border-border px-6 py-5 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-center gap-3"><span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-50 text-primary"><Icon className="h-5 w-5" /></span><div><p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">{eyebrow}</p><h3 className="mt-1 text-xl font-semibold tracking-tight">{title}</h3></div></div>{trailing}</header>;
}

function StageFooter({ backLabel, children, onBack }: { backLabel: string; children: React.ReactNode; onBack: () => void }) {
  return <footer className="flex flex-col-reverse gap-3 border-t border-border bg-slate-50/70 px-6 py-4 sm:flex-row sm:items-center sm:justify-between"><Button onClick={onBack} variant="ghost"><ArrowLeft className="h-4 w-4" /> {backLabel}</Button>{children}</footer>;
}

function ResultMetric({ label, mono, value }: { label: string; mono?: boolean; value: string | number }) {
  return <div className="rounded-2xl border border-border bg-slate-50 p-4"><p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">{label}</p><p className={`mt-2 truncate text-lg font-semibold text-slate-950 ${mono ? "font-mono text-sm" : ""}`}>{value}</p></div>;
}

function InfoPair({ label, mono, value }: { label: string; mono?: boolean; value: string }) {
  return <div className="flex items-center justify-between gap-3 text-xs"><span className="text-slate-400">{label}</span><span className={`truncate font-semibold text-white ${mono ? "font-mono" : ""}`}>{value}</span></div>;
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-border bg-white p-3"><p className="font-semibold uppercase tracking-[0.12em] text-slate-400">{label}</p><p className="mt-1 break-all font-mono text-slate-700">{value}</p></div>;
}

function StudioLoading() {
  return <div className="flex min-h-[420px] items-center justify-center rounded-3xl border border-border bg-white"><Loader2 className="h-6 w-6 animate-spin text-primary" /><span className="ml-3 text-sm text-muted-foreground">Loading curriculum workspace…</span></div>;
}

function AccessDenied() {
  return <div className="mx-auto max-w-2xl rounded-3xl border border-amber-200 bg-white p-10 text-center shadow-sm"><span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-50 text-amber-700"><LockKeyhole className="h-6 w-6" /></span><h2 className="mt-5 text-2xl font-semibold tracking-tight">Platform access required</h2><p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-muted-foreground">This internal tool is visible only to PaperGen super-admins. Sign in with a super-admin account to work with global curriculum data.</p></div>;
}

function shortDigest(value: string) {
  return value.length > 22 ? `${value.slice(0, 13)}…${value.slice(-7)}` : value;
}

function shortId(value: string) {
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function readError(error: unknown) {
  if (error instanceof ApiError) {
    const details = Array.isArray(error.details) ? error.details : [];
    const detail = details[0] as { field?: string | null; issue?: string } | undefined;
    return detail?.issue
      ? `${error.message} ${detail.field ? `${detail.field}: ` : ""}${detail.issue}`
      : error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong while contacting the curriculum service.";
}
