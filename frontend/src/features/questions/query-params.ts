import {
  DEFAULT_QUESTION_FILTERS,
  QUESTION_BLOOM_LEVELS,
  QUESTION_DIFFICULTIES,
  QUESTION_SCOPES,
  QUESTION_SOURCES,
  QUESTION_STATUSES,
  QUESTION_TYPES,
  type QuestionFilters,
} from "@/features/questions/types";

const STRING_FILTER_KEYS = [
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

const UUID_FILTER_KEYS = new Set<keyof QuestionFilters>([
  "board_id",
  "class_id",
  "subject_id",
  "chapter_id",
]);

const ALLOWED_FILTER_VALUES: Partial<Record<keyof QuestionFilters, ReadonlySet<string>>> = {
  qtype: new Set(QUESTION_TYPES),
  difficulty: new Set(QUESTION_DIFFICULTIES),
  bloom: new Set(QUESTION_BLOOM_LEVELS),
  source: new Set(QUESTION_SOURCES),
  status: new Set(QUESTION_STATUSES),
  scope: new Set(QUESTION_SCOPES),
};

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function readQuestionFilters(params: URLSearchParams): QuestionFilters {
  const page = positiveInteger(params.get("page"), DEFAULT_QUESTION_FILTERS.page);
  const pageSize = Math.min(100, positiveInteger(params.get("pageSize"), DEFAULT_QUESTION_FILTERS.pageSize));
  const filters: QuestionFilters = {
    ...DEFAULT_QUESTION_FILTERS,
    q: params.get("q")?.slice(0, 500) ?? "",
    page,
    pageSize,
  };

  for (const key of STRING_FILTER_KEYS) {
    const value = params.get(key);
    if (isValidFilterValue(key, value)) {
      filters[key] = value;
    }
  }

  return filters;
}

export function questionSearchParams(filters: QuestionFilters) {
  const params = new URLSearchParams();
  const query = filters.q.trim();
  if (query) params.set("q", query);

  for (const key of STRING_FILTER_KEYS) {
    const value = filters[key];
    if (value && !(key === "status" && value === DEFAULT_QUESTION_FILTERS.status)) {
      params.set(key, value);
    }
  }

  params.set("page", String(filters.page));
  params.set("pageSize", String(filters.pageSize));
  params.set("current_only", "true");
  return params;
}

export function browserQuestionParams(filters: QuestionFilters) {
  const params = questionSearchParams(filters);
  params.delete("current_only");
  if (filters.page === DEFAULT_QUESTION_FILTERS.page) params.delete("page");
  if (filters.pageSize === DEFAULT_QUESTION_FILTERS.pageSize) params.delete("pageSize");
  return params;
}

function positiveInteger(value: string | null, fallback: number) {
  if (!value || !/^\d+$/.test(value)) return fallback;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function isValidFilterValue(
  key: (typeof STRING_FILTER_KEYS)[number],
  value: string | null,
): value is string {
  if (!value) return false;
  if (UUID_FILTER_KEYS.has(key)) return UUID_PATTERN.test(value);
  return ALLOWED_FILTER_VALUES[key]?.has(value) ?? false;
}
