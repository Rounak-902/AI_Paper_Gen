import { apiClient, apiClientWithMeta } from "@/services/api-client";
import type {
  Membership,
  Program,
  ProgramAssignments,
  ProgramCreate,
  ProgramUpdate,
} from "@/features/programs/types";

export function listPrograms(orgId: string, page: number, pageSize: number, signal?: AbortSignal) {
  return apiClientWithMeta<Program[]>(
    "/api/v1/orgs/" + orgId + "/programs?page=" + page + "&pageSize=" + pageSize,
    { signal },
  );
}

export function createProgram(orgId: string, payload: ProgramCreate) {
  return apiClient<Program>("/api/v1/orgs/" + orgId + "/programs", {
    body: payload,
    method: "POST",
  });
}

export function updateProgram(orgId: string, programId: string, payload: ProgramUpdate) {
  return apiClient<Program>("/api/v1/orgs/" + orgId + "/programs/" + programId, {
    body: payload,
    method: "PATCH",
  });
}

export function getProgramAssignments(
  orgId: string,
  programId: string,
  signal?: AbortSignal,
) {
  return apiClient<ProgramAssignments>(
    "/api/v1/orgs/" + orgId + "/programs/" + programId + "/assignments",
    { signal },
  );
}

export function replaceProgramTeachers(orgId: string, programId: string, teacherIds: string[]) {
  return apiClient<{ id: string; deleted: boolean }>(
    "/api/v1/orgs/" + orgId + "/programs/" + programId + "/teachers",
    { body: { teacherIds }, method: "POST" },
  );
}

export function replaceProgramSubjects(orgId: string, programId: string, subjectIds: string[]) {
  return apiClient<{ id: string; deleted: boolean }>(
    "/api/v1/orgs/" + orgId + "/programs/" + programId + "/subjects",
    { body: { subjectIds }, method: "POST" },
  );
}

export function listMemberships(orgId: string, signal?: AbortSignal) {
  return apiClient<Membership[]>(
    "/api/v1/orgs/" + orgId + "/memberships?page=1&pageSize=100",
    { signal },
  );
}
