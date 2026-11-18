"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Circle,
  Database,
  FileJson2,
  Globe2,
  GraduationCap,
  Layers3,
  Loader2,
  Network,
  Search,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  listBoards,
  listChapters,
  listClasses,
  listOutcomes,
  listSubjects,
  listVersions,
} from "@/features/curriculum/api";
import type { CurriculumDocument } from "@/features/curriculum/schema";
import {
  type CatalogBoard,
  type CatalogChapter,
  type CatalogClass,
  type CatalogOutcome,
  type CatalogSubject,
  type CatalogVersion,
} from "@/features/curriculum/types";

export function StatusPill({ children, tone }: { children: ReactNode; tone: "success" | "warning" | "danger" | "neutral" }) {
  const styles = {
    success: "border-teal-200 bg-teal-50 text-teal-800",
    warning: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-red-200 bg-red-50 text-red-800",
    neutral: "border-slate-200 bg-slate-100 text-slate-600",
  }[tone];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold capitalize ${styles}`}>{children}</span>;
}

export function EntityMetric({ count, label }: { count: { create: number; skip: number }; label: string }) {
  return (
    <div className="rounded-2xl border border-border bg-white p-3.5 shadow-sm">
      <p className="truncate text-xs font-semibold text-slate-500">{label}</p>
      <div className="mt-3 flex items-end justify-between gap-2">
        <div><p className="text-2xl font-semibold tracking-tight text-slate-950">{count.create}</p><p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-primary">Create</p></div>
        <div className="text-right"><p className="text-lg font-semibold text-slate-500">{count.skip}</p><p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-slate-400">Skip</p></div>
      </div>
    </div>
  );
}

export function ManifestSummary({ document }: { document: CurriculumDocument }) {
  const subjects = document.board.classes.flatMap((schoolClass) => schoolClass.subjects);
  const chapters = subjects.flatMap((subject) => subject.chapters);
  const outcomes = chapters.reduce((total, chapter) => total + chapter.learningOutcomes.length, 0);
  const version = document.board.curriculumVersion;
  const versionLabel = "academicYear" in version ? version.academicYear : `Existing · ${shortId(version.id)}`;
  const status = version.finalStatus ?? "draft";
  return (
    <div className="rounded-2xl border border-border bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><p className="text-xs font-semibold uppercase tracking-[0.15em] text-primary">{document.board.code}</p><h4 className="mt-1 truncate text-lg font-semibold text-slate-950">{document.board.name}</h4><p className="mt-1 text-sm text-slate-500">{versionLabel}</p></div>
        <StatusPill tone={status === "active" ? "warning" : "neutral"}>{status}</StatusPill>
      </div>
      <div className="mt-4 grid grid-cols-4 gap-2 border-t border-border pt-4">
        {[["Classes", document.board.classes.length], ["Subjects", subjects.length], ["Chapters", chapters.length], ["Outcomes", outcomes]].map(([label, value]) => (
          <div className="text-center" key={label}><p className="text-lg font-semibold text-slate-900">{value}</p><p className="mt-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">{label}</p></div>
        ))}
      </div>
    </div>
  );
}

export function ManifestOutline({ document }: { document: CurriculumDocument }) {
  return (
    <div>
      <div className="mb-3 flex items-center justify-between"><div><p className="text-sm font-semibold">Curriculum outline</p><p className="mt-0.5 text-xs text-muted-foreground">Class → subject → chapter → outcomes</p></div><Network className="h-4.5 w-4.5 text-slate-400" /></div>
      <div className="max-h-[390px] space-y-2 overflow-y-auto pr-1">
        {document.board.classes.map((schoolClass, classIndex) => (
          <details className="group rounded-2xl border border-border bg-white" key={`${schoolClass.name}-${classIndex}`} open={document.board.classes.length === 1}>
            <summary className="flex cursor-pointer list-none items-center gap-2.5 p-3"><span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-950 text-white"><GraduationCap className="h-4 w-4" /></span><span className="min-w-0 flex-1 truncate text-sm font-semibold">{schoolClass.name}</span><span className="text-xs text-slate-400">{schoolClass.subjects.length} subjects</span><ChevronDown className="h-4 w-4 text-slate-400 transition group-open:rotate-180" /></summary>
            <div className="space-y-2 border-t border-border bg-slate-50/70 p-2.5">
              {schoolClass.subjects.map((subject, subjectIndex) => (
                <details className="group/subject rounded-xl border border-border bg-white" key={`${subject.name}-${subjectIndex}`}>
                  <summary className="flex cursor-pointer list-none items-center gap-2 p-2.5"><BookOpen className="h-4 w-4 text-primary" /><span className="min-w-0 flex-1 truncate text-sm font-medium">{subject.name}</span><span className="text-[11px] text-slate-400">{subject.chapters.length} ch.</span><ChevronDown className="h-3.5 w-3.5 text-slate-400 transition group-open/subject:rotate-180" /></summary>
                  <ul className="space-y-1 border-t border-border p-2">
                    {subject.chapters.map((chapter, chapterIndex) => (
                      <li className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-xs" key={`${chapter.seq}-${chapterIndex}`}><span className="mt-0.5 flex h-5 min-w-5 items-center justify-center rounded bg-teal-50 font-mono text-[10px] font-semibold text-primary">{chapter.seq}</span><span className="min-w-0 flex-1"><span className="block truncate font-medium text-slate-700">{chapter.title}</span><span className="text-[10px] text-slate-400">{chapter.learningOutcomes.length} learning outcomes{chapter.derivedFrom ? " · lineage linked" : ""}</span></span></li>
                    ))}
                  </ul>
                </details>
              ))}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

export function CatalogDialog({ onClose, open }: { onClose: () => void; open: boolean }) {
  const [boards, setBoards] = useState<CatalogBoard[]>([]);
  const [versions, setVersions] = useState<CatalogVersion[]>([]);
  const [selectedBoardId, setSelectedBoardId] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listBoards()
      .then((nextBoards) => {
        if (cancelled) return;
        setBoards(nextBoards);
        setSelectedBoardId((current) => current && nextBoards.some((board) => board.id === current) ? current : nextBoards[0]?.id ?? null);
      })
      .catch((catalogError) => !cancelled && setError(readCatalogError(catalogError)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [open]);

  useEffect(() => {
    if (!open || !selectedBoardId) {
      setVersions([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setVersions([]);
    setSelectedVersionId(null);
    void listVersions(selectedBoardId)
      .then((nextVersions) => {
        if (cancelled) return;
        setVersions(nextVersions);
        const preferred = nextVersions.find((version) => version.status === "active") ?? nextVersions[0];
        setSelectedVersionId(preferred?.id ?? null);
      })
      .catch((catalogError) => !cancelled && setError(readCatalogError(catalogError)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [open, selectedBoardId]);

  const visibleBoards = useMemo(() => {
    const query = search.trim().toLowerCase();
    return boards.filter((board) => !query || board.code.toLowerCase().includes(query) || board.name.toLowerCase().includes(query));
  }, [boards, search]);
  const selectedBoard = boards.find((board) => board.id === selectedBoardId) ?? null;
  const selectedVersion = versions.find((version) => version.id === selectedVersionId) ?? null;

  return (
    <Modal className="max-w-5xl" onClose={onClose} open={open} titleId="catalog-title">
      <div className="flex items-start gap-3 border-b border-border px-6 py-5">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-950 text-teal-200"><Globe2 className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1"><p className="text-xs font-semibold uppercase tracking-[0.15em] text-primary">Live reference data</p><h3 className="mt-1 text-xl font-semibold" id="catalog-title">Global curriculum catalog</h3><p className="mt-1 text-sm text-muted-foreground">Browse every board, version, class, subject, chapter, and learning outcome.</p></div>
        <button aria-label="Close catalog" className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700" onClick={onClose} type="button"><X className="h-4 w-4" /></button>
      </div>
      <div className="grid min-h-[570px] md:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="border-b border-border bg-slate-50 p-4 md:border-b-0 md:border-r">
            <label className="relative block"><span className="sr-only">Search boards</span><Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" /><Input className="bg-white pl-9" onChange={(event) => setSearch(event.target.value)} placeholder="Search code or board" value={search} /></label>
            <div className="mt-3 max-h-[485px] space-y-2 overflow-y-auto">
              {loading && boards.length === 0 ? <LoadingRows /> : null}
              {visibleBoards.map((board) => (
                <button className={`w-full rounded-2xl border p-3 text-left transition ${selectedBoardId === board.id ? "border-teal-300 bg-teal-50 shadow-sm" : "border-border bg-white hover:border-slate-300"}`} key={board.id} onClick={() => setSelectedBoardId(board.id)} type="button"><div className="flex items-center justify-between gap-2"><code className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">{board.code}</code>{selectedBoardId === board.id ? <CheckCircle2 className="h-4 w-4 text-primary" /> : null}</div><p className="mt-2 truncate text-sm font-semibold text-slate-900">{board.name}</p><p className="mt-1 text-[11px] text-slate-400">Updated {formatDate(board.updatedAt)}</p></button>
              ))}
              {!loading && visibleBoards.length === 0 ? <div className="rounded-2xl border border-dashed border-slate-300 p-6 text-center text-sm text-muted-foreground">No boards found.</div> : null}
            </div>
          </aside>
          <div className="p-5">
            {error ? <div className="mb-4 flex gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-900"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div> : null}
            {selectedBoard ? (
              <>
                <div className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-start sm:justify-between"><div><div className="flex items-center gap-2"><Database className="h-5 w-5 text-primary" /><code className="text-xs font-semibold text-primary">{selectedBoard.code}</code></div><h4 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">{selectedBoard.name}</h4><p className="mt-1 font-mono text-[11px] text-slate-400">{selectedBoard.id}</p></div><StatusPill tone="success">Catalog record</StatusPill></div>
                <div className="mt-5 flex items-center justify-between"><div><p className="font-semibold">Curriculum versions</p><p className="mt-1 text-sm text-muted-foreground">Choose the snapshot you want to inspect</p></div><span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">{versions.length} versions</span></div>
                {loading && versions.length === 0 ? <div className="mt-4"><LoadingRows /></div> : null}
                {versions.length ? (
                  <>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {versions.map((version) => (
                        <button
                          className={`rounded-xl border px-3 py-2 text-left transition ${selectedVersionId === version.id ? "border-teal-300 bg-teal-50 text-teal-950" : "border-border bg-white text-slate-600 hover:border-slate-300"}`}
                          key={version.id}
                          onClick={() => setSelectedVersionId(version.id)}
                          type="button"
                        >
                          <span className="block text-xs font-semibold">{version.academicYear}</span>
                          <span className="mt-0.5 block text-[10px] capitalize opacity-70">{version.status}</span>
                        </button>
                      ))}
                    </div>
                    {selectedVersion ? <div className="mt-3"><VersionCard version={selectedVersion} /></div> : null}
                    {selectedVersionId ? <CatalogHierarchy boardId={selectedBoard.id} versionId={selectedVersionId} /> : null}
                  </>
                ) : !loading ? <div className="mt-4 rounded-2xl border border-dashed border-slate-300 p-10 text-center"><FileJson2 className="mx-auto h-8 w-8 text-slate-300" /><p className="mt-3 text-sm text-muted-foreground">This board has no curriculum versions yet.</p></div> : null}
              </>
            ) : !loading ? <div className="flex min-h-[450px] items-center justify-center text-sm text-muted-foreground">Choose a board to inspect.</div> : null}
          </div>
      </div>
    </Modal>
  );
}

function CatalogHierarchy({
  boardId,
  versionId,
}: {
  boardId: string;
  versionId: string;
}) {
  const [classes, setClasses] = useState<CatalogClass[]>([]);
  const [subjects, setSubjects] = useState<CatalogSubject[]>([]);
  const [chapters, setChapters] = useState<CatalogChapter[]>([]);
  const [outcomes, setOutcomes] = useState<CatalogOutcome[]>([]);
  const [selectedClassId, setSelectedClassId] = useState<string | null>(null);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | null>(null);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [loadingLevel, setLoadingLevel] = useState<"classes" | "subjects" | "chapters" | "outcomes" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadingLevel("classes");
    setError(null);
    setClasses([]);
    setSubjects([]);
    setChapters([]);
    setOutcomes([]);
    setSelectedClassId(null);
    setSelectedSubjectId(null);
    setSelectedChapterId(null);
    void listClasses(boardId)
      .then((nextClasses) => {
        if (cancelled) return;
        setClasses(nextClasses);
        setSelectedClassId(nextClasses[0]?.id ?? null);
      })
      .catch((catalogError) => !cancelled && setError(readCatalogError(catalogError)))
      .finally(() => !cancelled && setLoadingLevel(null));
    return () => { cancelled = true; };
  }, [boardId, versionId]);

  useEffect(() => {
    if (!selectedClassId) {
      setSubjects([]);
      setSelectedSubjectId(null);
      return;
    }
    let cancelled = false;
    setLoadingLevel("subjects");
    setSubjects([]);
    setChapters([]);
    setOutcomes([]);
    setSelectedSubjectId(null);
    setSelectedChapterId(null);
    void listSubjects(selectedClassId, versionId)
      .then((nextSubjects) => {
        if (cancelled) return;
        setSubjects(nextSubjects);
        setSelectedSubjectId(nextSubjects[0]?.id ?? null);
      })
      .catch((catalogError) => !cancelled && setError(readCatalogError(catalogError)))
      .finally(() => !cancelled && setLoadingLevel(null));
    return () => { cancelled = true; };
  }, [selectedClassId, versionId]);

  useEffect(() => {
    if (!selectedSubjectId) {
      setChapters([]);
      setSelectedChapterId(null);
      return;
    }
    let cancelled = false;
    setLoadingLevel("chapters");
    setChapters([]);
    setOutcomes([]);
    setSelectedChapterId(null);
    void listChapters(selectedSubjectId, versionId)
      .then((nextChapters) => {
        if (cancelled) return;
        setChapters([...nextChapters].sort((left, right) => left.seq - right.seq));
      })
      .catch((catalogError) => !cancelled && setError(readCatalogError(catalogError)))
      .finally(() => !cancelled && setLoadingLevel(null));
    return () => { cancelled = true; };
  }, [selectedSubjectId, versionId]);

  useEffect(() => {
    if (!selectedChapterId) {
      setOutcomes([]);
      return;
    }
    let cancelled = false;
    setLoadingLevel("outcomes");
    setOutcomes([]);
    void listOutcomes(selectedChapterId)
      .then((nextOutcomes) => !cancelled && setOutcomes(nextOutcomes))
      .catch((catalogError) => !cancelled && setError(readCatalogError(catalogError)))
      .finally(() => !cancelled && setLoadingLevel(null));
    return () => { cancelled = true; };
  }, [selectedChapterId]);

  return (
    <section className="mt-5 border-t border-border pt-5">
      <div className="flex items-center justify-between gap-3">
        <div><p className="font-semibold text-slate-950">Curriculum content</p><p className="mt-1 text-sm text-muted-foreground">Explore the resolved hierarchy in this version</p></div>
        <StatusPill tone="success">Live API</StatusPill>
      </div>
      {error ? <div className="mt-3 flex gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-900"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div> : null}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label><span className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">Class</span><select className="h-10 w-full rounded-xl border border-input bg-white px-3 text-sm outline-none focus:ring-2 focus:ring-ring" disabled={!classes.length} onChange={(event) => setSelectedClassId(event.target.value || null)} value={selectedClassId ?? ""}><option value="">{loadingLevel === "classes" ? "Loading classes…" : "No classes"}</option>{classes.map((schoolClass) => <option key={schoolClass.id} value={schoolClass.id}>{schoolClass.name}</option>)}</select></label>
        <label><span className="mb-1.5 block text-xs font-semibold uppercase tracking-[0.12em] text-slate-400">Subject</span><select className="h-10 w-full rounded-xl border border-input bg-white px-3 text-sm outline-none focus:ring-2 focus:ring-ring" disabled={!subjects.length} onChange={(event) => setSelectedSubjectId(event.target.value || null)} value={selectedSubjectId ?? ""}><option value="">{loadingLevel === "subjects" ? "Loading subjects…" : "No subjects in this version"}</option>{subjects.map((subject) => <option key={subject.id} value={subject.id}>{subject.name}</option>)}</select></label>
      </div>
      {loadingLevel === "chapters" ? <div className="mt-4"><LoadingRows /></div> : null}
      {!loadingLevel && selectedSubjectId && chapters.length === 0 ? <div className="mt-4 rounded-2xl border border-dashed border-slate-300 p-6 text-center text-sm text-muted-foreground">No chapters are attached to this subject and version.</div> : null}
      <div className="mt-4 space-y-2">
        {chapters.map((chapter) => {
          const expanded = selectedChapterId === chapter.id;
          return (
            <div className={`overflow-hidden rounded-2xl border transition ${expanded ? "border-teal-300 shadow-sm" : "border-border"}`} key={chapter.id}>
              <button aria-expanded={expanded} className="flex w-full items-start gap-3 bg-white p-3.5 text-left hover:bg-slate-50" onClick={() => setSelectedChapterId(expanded ? null : chapter.id)} type="button">
                <span className="flex h-8 min-w-8 items-center justify-center rounded-lg bg-teal-50 font-mono text-xs font-semibold text-primary">{chapter.seq}</span>
                <span className="min-w-0 flex-1"><span className="block text-sm font-semibold text-slate-900">{chapter.title}</span><span className="mt-1 block font-mono text-[10px] text-slate-400">{shortId(chapter.id)}{chapter.derivedFromChapterId ? " · lineage linked" : ""}</span></span>
                <ChevronDown className={`mt-2 h-4 w-4 text-slate-400 transition ${expanded ? "rotate-180" : ""}`} />
              </button>
              {expanded ? (
                <div className="border-t border-border bg-slate-50 p-3">
                  {loadingLevel === "outcomes" ? <LoadingRows /> : outcomes.length ? (
                    <ul className="space-y-2">
                      {outcomes.map((outcome) => (
                        <li className="flex gap-3 rounded-xl border border-border bg-white p-3" key={outcome.id}>
                          <Circle className="mt-1 h-3.5 w-3.5 shrink-0 fill-teal-100 text-primary" />
                          <div className="min-w-0"><code className="text-[10px] font-semibold text-primary">{outcome.code ?? "No code"}</code><p className="mt-1 text-xs leading-5 text-slate-700">{outcome.description}</p></div>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="p-3 text-center text-xs text-muted-foreground">No learning outcomes in this chapter.</p>}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function VersionCard({ version }: { version: CatalogVersion }) {
  const tone = version.status === "active" ? "success" : version.status === "superseded" ? "neutral" : "warning";
  return (
    <div className="rounded-2xl border border-border bg-white p-4 shadow-sm">
      <div className="flex items-start gap-3"><span className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100 text-slate-600"><Layers3 className="h-4 w-4" /></span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="font-semibold text-slate-900">{version.label || version.academicYear}</p><StatusPill tone={tone}>{version.status}</StatusPill></div><p className="mt-1 text-sm text-slate-500">Academic year {version.academicYear}{version.effectiveFrom ? ` · Effective ${formatDate(version.effectiveFrom)}` : ""}</p>{version.sourceRef ? <p className="mt-2 truncate text-xs text-primary">{version.sourceRef}</p> : null}</div></div>
    </div>
  );
}

function Modal({ children, className = "max-w-2xl", onClose, open, titleId }: { children: ReactNode; className?: string; onClose: () => void; open: boolean; titleId: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      aria-labelledby={titleId}
      className={`max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-y-auto rounded-3xl border border-slate-200 bg-white p-0 text-slate-950 shadow-2xl backdrop:bg-slate-950/75 backdrop:backdrop-blur-sm ${className}`}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
      onClose={onClose}
      ref={ref}
    >
      {children}
    </dialog>
  );
}

function LoadingRows() {
  return <div className="space-y-2" aria-live="polite"><div className="flex items-center gap-2 rounded-xl border border-border bg-white p-4 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin text-primary" /> Loading live catalog…</div></div>;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}

function shortId(value: string) {
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function readCatalogError(error: unknown) {
  return error instanceof Error ? error.message : "Could not load the curriculum catalog.";
}
