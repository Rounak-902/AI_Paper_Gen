import assert from "node:assert/strict";
import test from "node:test";
import { canManageApprovals, isSeparationBlocked } from "./policy";
import type { ApprovalRequest } from "./types";

const request = {
  separationOfDutiesRequired: true,
  submittedBy: "author-id",
} as ApprovalRequest;

test("approval management follows the P6-T4 org-role predicate", () => {
  assert.equal(canManageApprovals("teacher"), false);
  assert.equal(canManageApprovals("coaching_admin"), true);
  assert.equal(canManageApprovals("super_admin"), true);
  assert.equal(canManageApprovals(null), false);
});

test("BR-6 blocks a reviewer-role submitter only when separation is required", () => {
  assert.equal(isSeparationBlocked(request, "author-id"), true);
  assert.equal(isSeparationBlocked(request, "other-id"), false);
  assert.equal(isSeparationBlocked({ ...request, separationOfDutiesRequired: false }, "author-id"), false);
});

