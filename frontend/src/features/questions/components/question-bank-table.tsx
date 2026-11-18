"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  listBoards,
  listChapters,
  listClasses,
  listQuestions,
  listSubjects,
} from "@/features/questions/api";
import { browserQuestionParams, readQuestionFilters } from "@/features/questions/query-params";
import {
  DEFAULT_QUESTION_FILTERS,
  QUESTION_BLOOM_LEVELS,
  QUESTION_DIFFICULTIES,
  QUESTION_SCOPES,
  QUESTION_SOURCES,
  QUESTION_STATUSES,
  QUESTION_TYPES,
  humanize,
  type Question,
  type QuestionFilters,
} from "@/features/questions/types";
import { ApiError } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";

const EMPTY_QUESTIONS: Question[] = [];
const tableFeatureSet = tableFeatures({});
const columnHelper = createColumnHelper<typeof tableFeatureSet, Question>();
const COLUMN_WIDTHS: Record<string, number> = {
  qtype: 150,
  stem: 430,
  difficulty: 120,
  bloom: 120,
  source: 150,
  status: 120,
};
const FILTER_KEYS = [
  "board_id",
  "class_id",
  "subject_id",
  "chapter_id",
  "qtype",
  "difficulty",
  "bloom",
  "source",
  "status",
  "scope",
] as const;

const columns = columnHelper.columns([
  columnHelper.accessor("qtype", {
    header: "Type",
    cell: ({ getValue }) => <Badge variant="outline">{humanize(String(getValue()))}</Badge>,
  }),
  columnHelper.accessor("stem", {
    header: "Question",
    cell: ({ getValue, row }) => (
      <Link
        className="line-clamp-2 font-medium leading-5 text-foreground hover:text-primary hover:underline"
        href={"/question-bank/" + row.original.id}
      >
        {String(getValue())}
      </Link>
    ),
  }),
  columnHelper.accessor("difficulty", {
    header: "Difficulty",
    cell: ({ getValue }) => <span>{humanize(String(getValue()))}</span>,
  }),
  columnHelper.accessor("bloom", {
    header: "Bloom",
    cell: ({ getValue }) => <span>{getValue() ? humanize(String(getValue())) : "—"}</span>,
  }),
  columnHelper.accessor("source", {
    header: "Provenance",
    cell: ({ getValue, row }) => (
      <div className="space-y-1">
        <Badge variant="secondary">{humanize(String(getValue()))}</Badge>
        <p className="text-xs text-muted-foreground">{humanize(row.original.scope)}</p>
      </div>
    ),
  }),
  columnHelper.accessor("status", {
    header: "Status",
    cell: ({ getValue }) => {
      const value = String(getValue());
      return <Badge variant={value === "approved" ? "default" : "outline"}>{humanize(value)}</Badge>;
    },
  }),
]);

export function QuestionBankTable() {
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const router = useRouter();
  const urlSearchParams = useSearchParams();
  const filters = useMemo(
    () => readQuestionFilters(new URLSearchParams(urlSearchParams.toString())),
    [urlSearchParams],
  );
  const [searchValue, setSearchValue] = useState(filters.q);
  const scrollRef = useRef<HTMLDivElement>(null);

  const updateFilters = useCallback(
    (updates: Partial<QuestionFilters>, resetPage = true) => {
      const next = { ...filters, ...updates };
      if (resetPage && updates.page === undefined) next.page = 1;
      const query = browserQuestionParams(next).toString();
      router.replace("/question-bank" + (query ? "?" + query : ""), { scroll: false });
    },
    [filters, router],
  );

  useEffect(() => setSearchValue(filters.q), [filters.q]);

  useEffect(() => {
    const normalized = searchValue.trim();
    if (normalized === filters.q) return;
    const timeout = window.setTimeout(() => updateFilters({ q: normalized }), 300);
    return () => window.clearTimeout(timeout);
  }, [filters.q, searchValue, updateFilters]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [filters.page]);

  const questionsQuery = useQuery({
    queryKey: [activeOrgId, "questions", filters],
    queryFn: ({ signal }) => listQuestions(activeOrgId!, filters, signal),
    enabled: Boolean(activeOrgId),
    placeholderData: keepPreviousData,
  });
  const boardsQuery = useQuery({
    queryKey: [activeOrgId, "catalog", "boards"],
    queryFn: ({ signal }) => listBoards(signal),
    enabled: Boolean(activeOrgId),
    staleTime: 10 * 60 * 1000,
  });
  const classesQuery = useQuery({
    queryKey: [activeOrgId, "catalog", "classes", filters.board_id],
    queryFn: ({ signal }) => listClasses(filters.board_id, signal),
    enabled: Boolean(activeOrgId && filters.board_id),
    staleTime: 10 * 60 * 1000,
  });
  const subjectsQuery = useQuery({
    queryKey: [activeOrgId, "catalog", "subjects", filters.class_id],
    queryFn: ({ signal }) => listSubjects(filters.class_id, signal),
    enabled: Boolean(activeOrgId && filters.class_id),
    staleTime: 10 * 60 * 1000,
  });
  const chaptersQuery = useQuery({
    queryKey: [activeOrgId, "catalog", "chapters", filters.subject_id],
    queryFn: ({ signal }) => listChapters(filters.subject_id, signal),
    enabled: Boolean(activeOrgId && filters.subject_id),
    staleTime: 10 * 60 * 1000,
  });

  const questions = questionsQuery.data?.data ?? EMPTY_QUESTIONS;
  const pagination = questionsQuery.data?.meta?.pagination;
  const total = pagination?.total ?? questions.length;
  const hasNext = pagination?.hasNext ?? filters.page * filters.pageSize < total;
  const table = useTable({
    columns,
    data: questions,
    features: tableFeatureSet,
    getRowId: (row) => row.id,
  });
  const rows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: rows.length,
    estimateSize: () => 72,
    getScrollElement: () => scrollRef.current,
    overscan: 8,
  });
  const hasFilters = Boolean(
    filters.q ||
      FILTER_KEYS.some(
        (key) => filters[key] && !(key === "status" && filters[key] === "approved"),
      ),
  );

  function setCatalogFilter(
    key: "board_id" | "class_id" | "subject_id" | "chapter_id",
    value: string,
  ) {
    const updates: Partial<QuestionFilters> = { [key]: value };
    if (key === "board_id") Object.assign(updates, { class_id: "", subject_id: "", chapter_id: "" });
    if (key === "class_id") Object.assign(updates, { subject_id: "", chapter_id: "" });
    if (key === "subject_id") Object.assign(updates, { chapter_id: "" });
    updateFilters(updates);
  }

  return (
    <div className="space-y-4">
      <QuestionFiltersPanel
        boards={(boardsQuery.data ?? []).map((item) => ({ label: item.name, value: item.id }))}
        chapters={(chaptersQuery.data ?? []).map((item) => ({ label: item.title, value: item.id }))}
        classes={(classesQuery.data ?? []).map((item) => ({ label: item.name, value: item.id }))}
        filters={filters}
        hasFilters={hasFilters}
        searchValue={searchValue}
        setCatalogFilter={setCatalogFilter}
        setSearchValue={setSearchValue}
        subjects={(subjectsQuery.data ?? []).map((item) => ({ label: item.name, value: item.id }))}
        updateFilters={updateFilters}
      />

      <div className="flex min-h-6 items-center justify-between gap-4 text-sm text-muted-foreground" role="status">
        <span>
          {questionsQuery.isLoading
            ? "Loading questions…"
            : total.toLocaleString() + " question" + (total === 1 ? "" : "s")}
        </span>
        {questionsQuery.isFetching && !questionsQuery.isLoading ? <span>Updating results…</span> : null}
      </div>

      {questionsQuery.error ? (
        <ErrorState error={questionsQuery.error} retry={() => void questionsQuery.refetch()} />
      ) : (
        <div className="overflow-hidden rounded-lg border bg-card shadow-sm">
          <div className="max-h-[620px] min-h-[320px] overflow-auto" ref={scrollRef}>
            <table className="grid min-w-[1090px] text-sm">
              <thead className="sticky top-0 z-10 grid border-b bg-muted/95 text-muted-foreground backdrop-blur">
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr className="flex w-full" key={headerGroup.id}>
                    {headerGroup.headers.map((header) => (
                      <th
                        className="flex h-11 items-center px-4 text-left text-xs font-semibold uppercase tracking-wide"
                        key={header.id}
                        style={{ width: COLUMN_WIDTHS[header.column.id] ?? 150 }}
                      >
                        {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody className="relative grid" style={{ height: virtualizer.getTotalSize() }}>
                {virtualizer.getVirtualItems().map((virtualRow) => {
                  const row = rows[virtualRow.index];
                  if (!row) return null;
                  return (
                    <tr
                      className="absolute flex w-full border-b bg-card hover:bg-muted/40"
                      data-index={virtualRow.index}
                      key={row.id}
                      ref={(node) => virtualizer.measureElement(node)}
                      style={{ transform: "translateY(" + virtualRow.start + "px)" }}
                    >
                      {row.getAllCells().map((cell) => (
                        <td
                          className="flex min-h-[72px] items-center px-4 py-3"
                          key={cell.id}
                          style={{ width: COLUMN_WIDTHS[cell.column.id] ?? 150 }}
                        >
                          <table.FlexRender cell={cell} />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!questionsQuery.isLoading && questions.length === 0 ? (
              <div className="flex min-h-[260px] flex-col items-center justify-center px-6 text-center">
                <Search className="mb-3 h-8 w-8 text-muted-foreground" />
                <h2 className="font-semibold">No questions found</h2>
                <p className="mt-1 max-w-md text-sm text-muted-foreground">
                  {hasFilters
                    ? "Try clearing a filter or broadening the search."
                    : "Approved questions will appear here once they are added."}
                </p>
              </div>
            ) : null}
          </div>
        </div>
      )}

      <nav aria-label="Question pages" className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Page {filters.page}
          {total ? " of " + Math.max(1, Math.ceil(total / filters.pageSize)) : ""}
        </p>
        <div className="flex gap-2">
          <Button
            disabled={filters.page <= 1 || questionsQuery.isFetching}
            onClick={() => updateFilters({ page: filters.page - 1 }, false)}
            type="button"
            variant="outline"
          >
            <ChevronLeft /> Previous
          </Button>
          <Button
            disabled={!hasNext || questionsQuery.isFetching}
            onClick={() => updateFilters({ page: filters.page + 1 }, false)}
            type="button"
            variant="outline"
          >
            Next <ChevronRight />
          </Button>
        </div>
      </nav>
    </div>
  );
}

type SelectOption = { label: string; value: string };

function QuestionFiltersPanel({
  boards,
  chapters,
  classes,
  filters,
  hasFilters,
  searchValue,
  setCatalogFilter,
  setSearchValue,
  subjects,
  updateFilters,
}: {
  boards: SelectOption[];
  chapters: SelectOption[];
  classes: SelectOption[];
  filters: QuestionFilters;
  hasFilters: boolean;
  searchValue: string;
  setCatalogFilter: (
    key: "board_id" | "class_id" | "subject_id" | "chapter_id",
    value: string,
  ) => void;
  setSearchValue: (value: string) => void;
  subjects: SelectOption[];
  updateFilters: (updates: Partial<QuestionFilters>, resetPage?: boolean) => void;
}) {
  return (
    <section aria-label="Question filters" className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="grid gap-3 lg:grid-cols-4">
        <label className="relative block">
          <span className="sr-only">Search questions</span>
          <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-9"
            maxLength={500}
            onChange={(event) => setSearchValue(event.target.value)}
            placeholder="Search questions"
            type="search"
            value={searchValue}
          />
        </label>
        <FacetSelect label="Board" onChange={(value) => setCatalogFilter("board_id", value)} options={boards} value={filters.board_id} />
        <FacetSelect disabled={!filters.board_id} label="Class" onChange={(value) => setCatalogFilter("class_id", value)} options={classes} value={filters.class_id} />
        <FacetSelect disabled={!filters.class_id} label="Subject" onChange={(value) => setCatalogFilter("subject_id", value)} options={subjects} value={filters.subject_id} />
        <FacetSelect disabled={!filters.subject_id} label="Chapter" onChange={(value) => setCatalogFilter("chapter_id", value)} options={chapters} value={filters.chapter_id} />
        <FacetSelect label="Question type" onChange={(qtype) => updateFilters({ qtype })} options={QUESTION_TYPES.map(option)} value={filters.qtype} />
        <FacetSelect label="Difficulty" onChange={(difficulty) => updateFilters({ difficulty })} options={QUESTION_DIFFICULTIES.map(option)} value={filters.difficulty} />
        <FacetSelect label="Bloom level" onChange={(bloom) => updateFilters({ bloom })} options={QUESTION_BLOOM_LEVELS.map(option)} value={filters.bloom} />
        <FacetSelect label="Source" onChange={(source) => updateFilters({ source })} options={QUESTION_SOURCES.map(option)} value={filters.source} />
        <FacetSelect allowAll={false} label="Status" onChange={(status) => updateFilters({ status })} options={QUESTION_STATUSES.map(option)} value={filters.status} />
        <FacetSelect label="Scope" onChange={(scope) => updateFilters({ scope })} options={QUESTION_SCOPES.map(option)} value={filters.scope} />
        <FacetSelect
          allowAll={false}
          label="Rows per page"
          onChange={(value) => updateFilters({ pageSize: Number(value) })}
          options={[20, 50, 100].map((value) => ({ label: String(value), value: String(value) }))}
          value={String(filters.pageSize)}
        />
      </div>
      {hasFilters ? (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-3">
          <span className="text-xs font-medium text-muted-foreground">Applied</span>
          {filters.q ? (
            <FilterChip
              label={"Search: " + filters.q}
              onClear={() => {
                setSearchValue("");
                updateFilters({ q: "" });
              }}
            />
          ) : null}
          {FILTER_KEYS.map((key) => {
            const value = filters[key];
            if (!value || (key === "status" && value === "approved")) return null;
            const catalogKey =
              key === "board_id" || key === "class_id" || key === "subject_id" || key === "chapter_id"
                ? key
                : null;
            return (
              <FilterChip
                key={key}
                label={filterLabel(key) + ": " + humanize(value)}
                onClear={() =>
                  catalogKey
                    ? setCatalogFilter(catalogKey, "")
                    : updateFilters({ [key]: key === "status" ? "approved" : "" })
                }
              />
            );
          })}
          <Button
            className="h-7 px-2 text-xs"
            onClick={() => {
              setSearchValue("");
              updateFilters(DEFAULT_QUESTION_FILTERS, false);
            }}
            size="sm"
            type="button"
            variant="ghost"
          >
            Reset all
          </Button>
        </div>
      ) : null}
    </section>
  );
}

function FacetSelect({
  allowAll = true,
  disabled = false,
  label,
  onChange,
  options,
  value,
}: {
  allowAll?: boolean;
  disabled?: boolean;
  label: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  value: string;
}) {
  return (
    <label className="grid gap-1 text-xs font-medium text-muted-foreground">
      <span>{label}</span>
      <select
        className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        {allowAll ? <option value="">All</option> : null}
        {options.map((item) => (
          <option key={item.value} value={item.value}>{item.label}</option>
        ))}
      </select>
    </label>
  );
}

function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex h-7 items-center gap-1 rounded-full border bg-background pl-2.5 pr-1 text-xs">
      {label}
      <button aria-label={"Clear " + label} className="rounded-full p-1 hover:bg-muted" onClick={onClear} type="button">
        <X className="h-3 w-3" />
      </button>
    </span>
  );
}

function ErrorState({ error, retry }: { error: unknown; retry: () => void }) {
  const message = error instanceof Error ? error.message : "Questions could not be loaded.";
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-6" role="alert">
      <h2 className="font-semibold text-destructive">Question Bank is unavailable</h2>
      <p className="mt-1 text-sm">{message}</p>
      {requestId ? <p className="mt-1 font-mono text-xs text-muted-foreground">Request {requestId}</p> : null}
      <Button className="mt-4" onClick={retry} type="button" variant="outline">Try again</Button>
    </div>
  );
}

function option(value: string) {
  return { label: humanize(value), value };
}

function filterLabel(key: (typeof FILTER_KEYS)[number]) {
  const labels: Record<(typeof FILTER_KEYS)[number], string> = {
    board_id: "Board",
    class_id: "Class",
    subject_id: "Subject",
    chapter_id: "Chapter",
    qtype: "Type",
    difficulty: "Difficulty",
    bloom: "Bloom",
    source: "Source",
    status: "Status",
    scope: "Scope",
  };
  return labels[key];
}
