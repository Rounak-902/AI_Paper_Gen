import type { components } from "@/api/api-types";

export type Program = components["schemas"]["ProgramOut"];
export type ProgramCreate = components["schemas"]["ProgramCreateIn"];
export type ProgramUpdate = components["schemas"]["ProgramUpdateIn"];
export type Membership = components["schemas"]["MembershipOut"];
export type ProgramAssignments = components["schemas"]["ProgramAssignmentsOut"];

export type ProgramFormValues = {
  name: string;
  branchId: string;
  examTarget: string;
  academicYear: string;
};
