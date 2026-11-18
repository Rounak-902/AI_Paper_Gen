import type { components } from "@/api/api-types";

export type Question = components["schemas"]["QuestionOut"];
export type ImportPreview = components["schemas"]["ImportPreviewOut"];
export type ColumnMapping = components["schemas"]["ColumnMappingEntry"];
export type ImportJob = components["schemas"]["ImportJobOut"];
export type ImportResult = {
  total_rows: ImportJob["total_rows"];
  created_count: ImportJob["created_count"];
  created_with_warnings_count: ImportJob["created_with_warnings_count"];
  error_count: ImportJob["error_count"];
  duplicate_count: ImportJob["duplicate_count"];
  rows: NonNullable<ImportJob["rows"]>;
  error_report_token?: ImportJob["error_report_token"];
};
export type Board = components["schemas"]["BoardOut"];
export type SchoolClass = components["schemas"]["ClassOut"];
export type Subject = components["schemas"]["SubjectOut"];
export type Chapter = components["schemas"]["ChapterOut"];

export const QUESTION_TYPES = [
  "mcq",
  "true_false",
  "one_word",
  "fill_blank",
  "short_answer",
  "long_answer",
  "assertion_reason",
  "case_study",
  "numerical",
  "diagram_based",
  "match",
  "very_short_answer",
  "source_based",
] as const;

export const QUESTION_DIFFICULTIES = ["easy", "medium", "hard"] as const;
export const QUESTION_BLOOM_LEVELS = [
  "remember",
  "understand",
  "apply",
  "analyze",
  "evaluate",
  "create",
] as const;
export const QUESTION_SOURCES = [
  "institute_bank",
  "textbook",
  "intext",
  "previous_year",
  "ai_generated",
] as const;
export const QUESTION_STATUSES = ["approved", "draft", "rejected", "archived"] as const;
export const QUESTION_SCOPES = ["org", "global"] as const;

export const IMPORT_TARGET_FIELDS = [
  "qtype",
  "stem",
  "marks",
  "difficulty",
  "bloom",
  "language",
  "source",
  "scope",
  "subject_name",
  "chapter_name",
  "board_code",
  "class_name",
  "subject_id",
  "chapter_id",
  "board_id",
  "class_id",
  "options_json",
  "answer_json",
  "rubric_json",
  "meta_json",
] as const;

export const REQUIRED_IMPORT_FIELDS = ["qtype", "stem", "marks", "difficulty"] as const;

export type QuestionFilters = {
  q: string;
  board_id: string;
  class_id: string;
  subject_id: string;
  chapter_id: string;
  qtype: string;
  difficulty: string;
  bloom: string;
  source: string;
  status: string;
  scope: string;
  page: number;
  pageSize: number;
};

export const DEFAULT_QUESTION_FILTERS: QuestionFilters = {
  q: "",
  board_id: "",
  class_id: "",
  subject_id: "",
  chapter_id: "",
  qtype: "",
  difficulty: "",
  bloom: "",
  source: "",
  status: "approved",
  scope: "",
  page: 1,
  pageSize: 50,
};

export function humanize(value: string) {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
