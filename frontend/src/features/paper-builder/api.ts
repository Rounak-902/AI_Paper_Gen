import type { components } from "@/api/api-types";
import { apiClient, apiClientWithMeta } from "@/services/api-client";
import type {
  FeasibilityResult,
  GeneratePaperRequest,
  GenerationAccepted,
  PaperCoverageReport,
  PaperDraftResource,
  PaperRealtimeState,
  PaperBlueprint,
  RegenerationAccepted,
  RegenerationRequest,
  SavePaperDraftRequest,
} from "./types";

type Board = components["schemas"]["BoardOut"];
type CurriculumVersion = components["schemas"]["CurriculumVersionOut"];
type SchoolClass = components["schemas"]["ClassOut"];
type Subject = components["schemas"]["SubjectOut"];
type Chapter = components["schemas"]["ChapterOut"];
type Outcome = components["schemas"]["OutcomeOut"];

export const paperBuilderKeys = {
  boards: (orgId: string) => ["paper-builder", orgId, "boards"] as const,
  versions: (orgId: string, boardId: string) => ["paper-builder", orgId, "versions", boardId] as const,
  classes: (orgId: string, boardId: string) => ["paper-builder", orgId, "classes", boardId] as const,
  subjects: (orgId: string, classId: string, versionId: string) => ["paper-builder", orgId, "subjects", classId, versionId] as const,
  chapters: (orgId: string, subjectId: string, versionId: string) => ["paper-builder", orgId, "chapters", subjectId, versionId] as const,
  outcomes: (orgId: string, chapterIds: string[]) => ["paper-builder", orgId, "outcomes", ...chapterIds.slice().sort()] as const,
  coverageReport: (orgId: string, paperId: string, versionId: string) => ["paper-builder", orgId, "coverage-report", paperId, versionId] as const,
  draft: (orgId: string, draftId: string) => ["paper-builder", "draft", orgId, draftId] as const,
  realtime: (orgId: string, paperId: string) => ["paper-builder", "realtime", orgId, paperId] as const,
};

export const listBoards = (signal?: AbortSignal) => listAll<Board>("/api/v1/boards", signal);
export const listVersions = (boardId: string, signal?: AbortSignal) =>
  listAll<CurriculumVersion>(`/api/v1/curriculum-versions?boardId=${encodeURIComponent(boardId)}`, signal);
export const listClasses = (boardId: string, signal?: AbortSignal) =>
  listAll<SchoolClass>(`/api/v1/classes?boardId=${encodeURIComponent(boardId)}`, signal);
export const listSubjects = (classId: string, versionId: string, signal?: AbortSignal) =>
  listAll<Subject>(`/api/v1/subjects?classId=${encodeURIComponent(classId)}&curriculumVersionId=${encodeURIComponent(versionId)}`, signal);
export const listChapters = (subjectId: string, versionId: string, signal?: AbortSignal) =>
  listAll<Chapter>(`/api/v1/chapters?subjectId=${encodeURIComponent(subjectId)}&curriculumVersionId=${encodeURIComponent(versionId)}`, signal);
export async function listOutcomes(chapterIds: string[], signal?: AbortSignal) {
  const groups = await Promise.all(
    chapterIds.map((chapterId) => listAll<Outcome>(`/api/v1/outcomes?chapterId=${encodeURIComponent(chapterId)}`, signal)),
  );
  return groups.flat();
}

export function checkFeasibility(orgId: string, blueprint: PaperBlueprint, signal?: AbortSignal) {
  return apiClient<FeasibilityResult>(`/api/v1/orgs/${orgId}/papers/feasibility`, {
    body: blueprint,
    method: "POST",
    signal,
    timeoutMs: 30_000,
  });
}

export function generatePaper(orgId: string, request: GeneratePaperRequest) {
  return apiClient<GenerationAccepted>(`/api/v1/orgs/${orgId}/papers/generate`, {
    body: request,
    method: "POST",
    timeoutMs: 30_000,
  });
}

export function getPaperCoverageReport(paperId: string, versionId: string, signal?: AbortSignal) {
  return apiClient<PaperCoverageReport>(
    `/api/v1/papers/${paperId}/versions/${versionId}/coverage-report`,
    { signal },
  );
}

export function getPaperRealtimeState(paperId: string, signal?: AbortSignal) {
  return apiClient<PaperRealtimeState>(`/api/v1/papers/${paperId}/realtime-state`, { signal });
}

export function regeneratePaperSlots(paperId: string, request: RegenerationRequest) {
  return apiClient<RegenerationAccepted>(`/api/v1/papers/${paperId}/regenerations`, {
    body: request,
    idempotencyKey: request.requestId,
    method: "POST",
    timeoutMs: 30_000,
  });
}

export function getPaperDraft(orgId: string, draftId: string, signal?: AbortSignal) {
  return apiClient<PaperDraftResource>(
    `/api/v1/orgs/${orgId}/papers/drafts/${draftId}`,
    { signal },
  );
}

export function savePaperDraft(
  orgId: string,
  draftId: string,
  request: SavePaperDraftRequest,
) {
  return apiClient<PaperDraftResource>(
    `/api/v1/orgs/${orgId}/papers/drafts/${draftId}`,
    { body: request, method: "PUT" },
  );
}

async function listAll<T>(path: string, signal?: AbortSignal) {
  const items: T[] = [];
  const separator = path.includes("?") ? "&" : "?";
  for (let page = 1; ; page += 1) {
    const response = await apiClientWithMeta<T[]>(`${path}${separator}page=${page}&pageSize=100`, { signal });
    items.push(...response.data);
    if (!response.meta?.pagination?.hasNext || response.data.length === 0) return items;
  }
}
