import assert from "node:assert/strict";
import test from "node:test";
import {
  notificationPresentation,
  safeNotificationLink,
} from "./presentation";

test("NT-12 is presented as a batch digest despite its generic client type", () => {
  assert.deepEqual(notificationPresentation({ code: "NT-12", type: "approval.requested" }), {
    batched: true,
    kind: "digest",
    label: "Digest",
    tone: "violet",
  });
});

test("notification links accept only app-relative destinations", () => {
  assert.equal(safeNotificationLink("/papers/abc?mode=review"), "/papers/abc?mode=review");
  assert.equal(safeNotificationLink("https://evil.example/papers/abc"), null);
  assert.equal(safeNotificationLink("//evil.example/papers/abc"), null);
  assert.equal(safeNotificationLink("papers/abc"), null);
});
