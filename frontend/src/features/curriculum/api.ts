import type { CurriculumDocument } from "@/features/curriculum/schema";
import type {
  CatalogBoard,
  CatalogChapter,
  CatalogClass,
  CatalogOutcome,
  CatalogSubject,
  CatalogVersion,
  ImportPlan,
  ImportResult,
} from "@/features/curriculum/types";
import { ApiError, apiClient, apiClientWithMeta } from "@/services/api-client";

const CURRICULUM_VALIDATE_TIMEOUT_MS = 60_000;
const CURRICULUM_PREVIEW_TIMEOUT_MS = 300_000;
const CURRICULUM_APPLY_TIMEOUT_MS = 900_000;

export function validateManifest(document: CurriculumDocument) {
  return apiClient<{
    valid: true;
    documentDigest: string;
    boardCode: string;
    versionKey: string;
  }>("/api/v1/curriculum-imports/validate", {
    body: document,
    method: "POST",
    timeoutMs: CURRICULUM_VALIDATE_TIMEOUT_MS,
  });
}

export function previewManifest(document: CurriculumDocument) {
  return apiClient<ImportPlan>("/api/v1/curriculum-imports/preview", {
    body: document,
    method: "POST",
    timeoutMs: CURRICULUM_PREVIEW_TIMEOUT_MS,
  });
}

export function applyManifest(document: CurriculumDocument, expectedPlanDigest: string) {
  return apiClient<ImportResult>("/api/v1/curriculum-imports", {
    body: { document, expectedPlanDigest },
    method: "POST",
    timeoutMessage:
      "The curriculum import is still taking longer than expected. It may finish on the server after this message; refresh and preview the same manifest before retrying.",
    timeoutMs: CURRICULUM_APPLY_TIMEOUT_MS,
  });
}

export async function listBoards() {
  return listEveryPage<CatalogBoard>("/api/v1/boards");
}

export async function listVersions(boardId: string) {
  return listEveryPage<CatalogVersion>(
    `/api/v1/curriculum-versions?boardId=${encodeURIComponent(boardId)}`,
  );
}

export async function listClasses(boardId: string) {
  return listEveryPage<CatalogClass>(
    `/api/v1/classes?boardId=${encodeURIComponent(boardId)}`,
  );
}

export async function listSubjects(classId: string, curriculumVersionId: string) {
  return listEveryPage<CatalogSubject>(
    `/api/v1/subjects?classId=${encodeURIComponent(classId)}&curriculumVersionId=${encodeURIComponent(curriculumVersionId)}`,
  );
}

export async function listChapters(subjectId: string, curriculumVersionId: string) {
  return listEveryPage<CatalogChapter>(
    `/api/v1/chapters?subjectId=${encodeURIComponent(subjectId)}&curriculumVersionId=${encodeURIComponent(curriculumVersionId)}`,
  );
}

export async function listOutcomes(chapterId: string) {
  return listEveryPage<CatalogOutcome>(
    `/api/v1/outcomes?chapterId=${encodeURIComponent(chapterId)}`,
  );
}

async function listEveryPage<T>(basePath: string): Promise<T[]> {
  const separator = basePath.includes("?") ? "&" : "?";
  const items: T[] = [];
  for (let page = 1; page <= 100; page += 1) {
    const result = await apiClientWithMeta<T[]>(
      `${basePath}${separator}page=${page}&pageSize=100`,
      { timeoutMs: 30_000 },
    );
    items.push(...result.data);
    if (!result.meta?.pagination?.hasNext) {
      return items;
    }
  }
  throw new ApiError({
    code: "catalog_page_limit",
    message: "The catalog exceeded the safe browser pagination limit.",
    status: 400,
  });
}
