export type ImportEntity =
  | "boards"
  | "classes"
  | "curriculum_versions"
  | "subjects"
  | "chapters"
  | "learning_outcomes";

export type ImportOperation = "create" | "skip" | "transition";

export type EntityCounts = Record<ImportEntity, { create: number; skip: number }>;

export type ImportIssue = {
  path: string;
  message: string;
  severity: "error" | "warning";
};

export type ImportAction = {
  operation: ImportOperation;
  entity: ImportEntity;
  path: string;
  key: string;
  endpoint: string;
  existingId: string | null;
  payload: Record<string, unknown> | null;
  fromStatus: "draft" | "active" | "superseded" | null;
  toStatus: "draft" | "active" | "superseded" | null;
};

export type ImportPlan = {
  ready: boolean;
  atomic: boolean;
  documentDigest: string;
  planDigest: string;
  boardCode: string;
  versionKey: string;
  counts: EntityCounts;
  actions: ImportAction[];
  issues: ImportIssue[];
};

export type ImportResult = ImportPlan & {
  committed: boolean;
  boardId: string;
  curriculumVersionId: string;
};

export type CatalogBoard = {
  id: string;
  code: string;
  name: string;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
};

export type CatalogVersion = {
  id: string;
  boardId: string;
  academicYear: string;
  label: string | null;
  status: "draft" | "active" | "superseded";
  effectiveFrom: string | null;
  sourceRef: string | null;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
};

export type CatalogClass = {
  id: string;
  boardId: string;
  name: string;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
};

export type CatalogSubject = {
  id: string;
  classId: string;
  curriculumVersionId: string | null;
  name: string;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
};

export type CatalogChapter = {
  id: string;
  subjectId: string;
  curriculumVersionId: string | null;
  seq: number;
  title: string;
  derivedFromChapterId: string | null;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
};

export type CatalogOutcome = {
  id: string;
  chapterId: string;
  code: string | null;
  description: string;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
};
