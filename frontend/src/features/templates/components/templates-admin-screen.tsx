"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CheckCircle2, ChevronLeft, ChevronRight, Copy, Eye, Loader2, Plus, RefreshCw, Star } from "lucide-react";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { activateTemplate, archiveTemplate, listTemplates, previewTemplate, setDefaultTemplate } from "@/features/templates/api";
import { TemplateEditorDialog } from "@/features/templates/components/template-editor-dialog";
import type { PaperTemplate, TemplatePreview, TemplateStatus } from "@/features/templates/types";
import { ApiError } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";

const PAGE_SIZE = 12;
type ActionKind = "activate" | "archive" | "default";

export function TemplatesAdminScreen() {
  const orgId = useSessionStore((state) => state.activeOrgId);
  const role = useSessionStore((state) => state.role);
  const canManage = role === "coaching_admin" || role === "super_admin";
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<TemplateStatus | "all">("all");
  const [editor, setEditor] = useState<PaperTemplate | null | "new">(null);
  const [preview, setPreview] = useState<(TemplatePreview & { name: string }) | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const query = useQuery({
    queryKey: [orgId, "templates", { page, pageSize: PAGE_SIZE, filter }],
    queryFn: ({ signal }) => listTemplates(orgId!, page, PAGE_SIZE, filter === "all" ? undefined : filter, signal),
    enabled: Boolean(orgId && canManage),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: [orgId, "templates"] });
  const action = useMutation({
    mutationFn: ({ kind, template }: { kind: ActionKind; template: PaperTemplate }) => {
      if (kind === "activate") return activateTemplate(orgId!, template);
      if (kind === "archive") return archiveTemplate(orgId!, template);
      return setDefaultTemplate(orgId!, template);
    },
    onMutate: () => {
      setActionError(null);
      setNotice(null);
    },
    onSuccess: async (saved, variables) => {
      await refresh();
      setNotice(actionSuccessMessage(saved, variables.kind));
    },
    onError: async (error) => {
      setActionError(actionErrorMessage(error));
      if (isOptimisticConflict(error)) await refresh();
    },
  });
  const previewMutation = useMutation({
    mutationFn: async (template: PaperTemplate) => ({
      ...(await previewTemplate(orgId!, template.id)),
      name: template.name,
    }),
    onMutate: () => {
      setActionError(null);
      setNotice(null);
    },
    onSuccess: setPreview,
    onError: (error) => setActionError(actionErrorMessage(error)),
  });

  if (!canManage) {
    return (
      <div className="mx-auto max-w-2xl rounded-lg border bg-card p-8 text-center" data-template-forbidden>
        <h2 className="text-xl font-semibold">Templates administration is restricted</h2>
        <p className="mt-2 text-sm text-muted-foreground">A coaching administrator or super administrator role is required.</p>
      </div>
    );
  }

  const templates = query.data?.data ?? [];
  const hasNext = Boolean(query.data?.meta?.pagination?.hasNext);
  return (
    <div className="mx-auto max-w-7xl space-y-6" data-templates-admin>
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-2xl font-semibold tracking-tight">Layout templates</h2>
            <Badge variant="outline">Layout & branding only</Badge>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">Manage reusable paper appearance for this organization. Section structure, question counts, marks, and difficulty targets remain outside this v1 contract.</p>
        </div>
        <Button type="button" onClick={() => setEditor("new")}><Plus /> New template</Button>
      </header>

      <div className="flex flex-wrap gap-2" aria-label="Template status filter">
        {(["all", "draft", "active", "archived"] as const).map((value) => (
          <Button key={value} aria-pressed={filter === value} size="sm" variant={filter === value ? "default" : "outline"} type="button" onClick={() => { setFilter(value); setPage(1); }}>
            {capitalize(value)}
          </Button>
        ))}
      </div>

      {notice ? <Alert className="border-emerald-500/40 bg-emerald-500/10 text-emerald-950" data-template-notice role="status">{notice}</Alert> : null}
      {actionError ? (
        <Alert className="flex flex-col gap-3 border-destructive/30 bg-destructive/5 text-destructive sm:flex-row sm:items-center sm:justify-between" data-template-error role="alert">
          <span>{actionError}</span>
          <Button onClick={() => void query.refetch()} size="sm" type="button" variant="outline"><RefreshCw /> Refresh templates</Button>
        </Alert>
      ) : null}
      {query.error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-5" role="alert">
          <h3 className="font-semibold text-destructive">Templates could not be loaded</h3>
          <p className="mt-1 text-sm">{actionErrorMessage(query.error)}</p>
        </div>
      ) : null}
      {query.isLoading ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, index) => <div className="h-64 animate-pulse rounded-lg bg-muted" key={index} />)}</div> : null}
      {!query.isLoading && !query.error && templates.length === 0 ? <Card className="border-dashed"><CardHeader><CardTitle className="text-lg">No templates here</CardTitle><CardDescription>Create a layout draft or choose another status filter.</CardDescription></CardHeader></Card> : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {templates.map((template) => (
          <Card data-template-card={template.id} key={template.id} className={template.isDefault ? "border-primary/60 shadow-sm" : undefined}>
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><CardTitle className="truncate text-lg">{template.name}</CardTitle><CardDescription className="truncate">{template.layout.branding.instituteName}</CardDescription></div>
                <div className="flex flex-wrap justify-end gap-1"><Badge variant={template.status === "active" ? "default" : "outline"}>{template.status}</Badge>{template.isDefault ? <Badge variant="secondary"><Star className="mr-1 h-3 w-3" />Org default</Badge> : null}</div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center gap-3 rounded-md border p-3 text-sm">
                <span aria-label={`Primary colour ${template.layout.branding.primaryColor}`} className="h-9 w-9 rounded-full border" style={{ backgroundColor: template.layout.branding.primaryColor }} />
                <div><p>{template.layout.page.size} · {template.layout.page.orientation}</p><p className="text-xs text-muted-foreground">{template.layout.typography.fontFamily}, {template.layout.typography.bodySizePt} pt · v{template.rowVersion}</p></div>
              </div>
              {template.sourceTemplateId ? <p className="text-xs text-muted-foreground">Linked draft cloned from active template {shortId(template.sourceTemplateId)}</p> : null}
              <div className="flex flex-wrap gap-2">
                {template.status !== "archived" ? (
                  <Button size="sm" variant="outline" type="button" onClick={() => setEditor(template)}>
                    {template.status === "active" ? <Copy /> : null}{template.status === "active" ? "Clone to draft" : "Edit draft"}
                  </Button>
                ) : null}
                <Button size="sm" variant="outline" type="button" disabled={previewMutation.isPending} onClick={() => previewMutation.mutate(template)}>
                  {previewMutation.isPending && previewMutation.variables?.id === template.id ? <Loader2 className="animate-spin" /> : <Eye />} Preview
                </Button>
                {template.status === "draft" ? <ActionButton action={action} kind="activate" template={template}><CheckCircle2 /> Activate</ActionButton> : null}
                {template.status === "active" && !template.isDefault ? <ActionButton action={action} kind="default" template={template} variant="ghost"><Star /> Set org default</ActionButton> : null}
                {template.status === "active" ? <ActionButton action={action} disabled={template.isDefault} kind="archive" template={template} title={template.isDefault ? "Set another active template as the organization default before archiving." : undefined} variant="ghost"><Archive /> Archive</ActionButton> : null}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <nav className="flex items-center justify-between gap-4" aria-label="Template pages">
        <p className="text-sm text-muted-foreground">Page {page} · {templates.length} shown</p>
        <div className="flex gap-2"><Button variant="outline" type="button" disabled={page === 1 || query.isFetching} onClick={() => setPage((value) => value - 1)}><ChevronLeft /> Previous</Button><Button variant="outline" type="button" disabled={!hasNext || query.isFetching} onClick={() => setPage((value) => value + 1)}>Next <ChevronRight /></Button></div>
      </nav>

      {orgId && editor !== null ? (
        <TemplateEditorDialog
          orgId={orgId}
          open
          template={editor === "new" ? null : editor}
          onOpenChange={(open) => !open && setEditor(null)}
          onSaved={(saved, outcome) => {
            setActionError(null);
            setNotice(editorSuccessMessage(saved, outcome));
          }}
        />
      ) : null}
      <Dialog open={Boolean(preview)} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent className="h-[92vh] max-w-6xl">
          <DialogHeader><DialogTitle>{preview ? `${preview.name} preview` : "Template preview"}</DialogTitle><DialogDescription>{preview ? `${preview.sampleContentVersion} · ${preview.fidelity}. This is fixed sample content, not a generated paper; PDF and DOCX output may differ.` : "Loading preview."}</DialogDescription></DialogHeader>
          {preview ? <iframe className="h-full min-h-0 w-full rounded-md border bg-muted" data-template-preview sandbox="" srcDoc={preview.html} title={`${preview.name} fixed sample-content preview`} /> : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ActionButton({ action, children, disabled, kind, template, title, variant }: {
  action: ReturnType<typeof useMutation<PaperTemplate, Error, { kind: ActionKind; template: PaperTemplate }>>;
  children: React.ReactNode;
  disabled?: boolean;
  kind: ActionKind;
  template: PaperTemplate;
  title?: string;
  variant?: "default" | "ghost";
}) {
  const pending = action.isPending && action.variables?.kind === kind && action.variables.template.id === template.id;
  return <Button data-template-action={kind} disabled={disabled || action.isPending} size="sm" title={title} type="button" variant={variant} onClick={() => action.mutate({ kind, template })}>{pending ? <Loader2 className="animate-spin" /> : null}{children}</Button>;
}

function actionSuccessMessage(template: PaperTemplate, kind: ActionKind) {
  if (kind === "activate") return `Activated “${template.name}”${template.isDefault ? " and made it the first organization default" : ""}.`;
  if (kind === "archive") return `Archived “${template.name}”.`;
  return `Set “${template.name}” as the organization default.`;
}

function editorSuccessMessage(template: PaperTemplate, outcome: "created" | "updated" | "cloned" | "conflict-copy") {
  if (outcome === "cloned") return `Created linked draft “${template.name}”. The active source was not changed.`;
  if (outcome === "conflict-copy") return `Preserved your fields in new draft “${template.name}”. The newer server version was not overwritten.`;
  if (outcome === "updated") return `Saved draft “${template.name}”.`;
  return `Created draft “${template.name}”.`;
}

function isOptimisticConflict(error: unknown) {
  return error instanceof ApiError && error.status === 409 && error.code === "CONCURRENT_MODIFICATION";
}

function actionErrorMessage(error: unknown) {
  if (error instanceof ApiError && error.code === "DEFAULT_TEMPLATE_ARCHIVE_BLOCKED") return "Choose another active template as the organization default before archiving this one.";
  if (isOptimisticConflict(error)) return "This template changed elsewhere. The action was not applied; the list has been refreshed with the current server version.";
  return error instanceof Error ? error.message : "The template action failed.";
}

function shortId(value: string) {
  return `${value.slice(0, 8)}…`;
}

function capitalize(value: string) {
  return value[0].toUpperCase() + value.slice(1);
}
