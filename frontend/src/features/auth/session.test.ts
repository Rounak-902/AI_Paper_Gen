import assert from "node:assert/strict";
import test from "node:test";
import { uniqueOrgMemberships } from "./session";

test("uniqueOrgMemberships retains one ordered entry per organization", () => {
  const firstOrg = {
    id: "b3100000-0000-4000-8000-000000000001",
    name: "Curriculum Loader Verification Org",
    role: "super_admin" as const,
  };
  const secondOrg = {
    id: "00000000-0000-4000-8000-000000000001",
    name: "PaperGen Demo Org",
    role: "teacher" as const,
  };

  assert.deepEqual(
    uniqueOrgMemberships([firstOrg, secondOrg, { ...firstOrg }, { ...secondOrg }]),
    [firstOrg, secondOrg],
  );
});

test("uniqueOrgMemberships keeps the first membership returned for an organization", () => {
  const orgId = "b3100000-0000-4000-8000-000000000001";

  assert.deepEqual(
    uniqueOrgMemberships([
      { id: orgId, name: "Authoritative name", role: "teacher" },
      { id: orgId, name: "Unexpected duplicate", role: "super_admin" },
    ]),
    [{ id: orgId, name: "Authoritative name", role: "teacher" }],
  );
});
