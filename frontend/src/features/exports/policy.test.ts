import assert from "node:assert/strict";
import test from "node:test";
import { canAccessAnswerKeyExports } from "./policy";

test("answer-key export controls are projected only for P6-T4 reviewer org roles", () => {
  assert.equal(canAccessAnswerKeyExports("teacher"), false);
  assert.equal(canAccessAnswerKeyExports("coaching_admin"), true);
  assert.equal(canAccessAnswerKeyExports("super_admin"), true);
  assert.equal(canAccessAnswerKeyExports(null), false);
});
