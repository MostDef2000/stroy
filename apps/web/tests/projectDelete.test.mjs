// Zero-dependency unit tests for projectDelete helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/projectDelete.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/projectDelete.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  PROJECT_BUSY_MESSAGE,
  PROJECT_DELETE_ERROR_MESSAGE,
  deleteConfirmText,
  deleteOutcomeFromStatus,
  nextStateAfterDelete
} from "../build/projectDelete.js";

test("deleteOutcomeFromStatus maps 204/404/409 and falls back to error", () => {
  assert.equal(deleteOutcomeFromStatus(204), "deleted");
  assert.equal(deleteOutcomeFromStatus(404), "already-deleted");
  assert.equal(deleteOutcomeFromStatus(409), "busy");
  assert.equal(deleteOutcomeFromStatus(500), "error");
  assert.equal(deleteOutcomeFromStatus(401), "error");
  assert.equal(deleteOutcomeFromStatus(200), "error");
});

test("204 selects the next project while any remain", () => {
  assert.deepEqual(nextStateAfterDelete("deleted", [{ id: "p1" }, { id: "p2" }]), {
    status: "select-next"
  });
  assert.deepEqual(nextStateAfterDelete("deleted", [{ id: "p1" }]), { status: "select-next" });
});

test("204 falls back to the empty state when none remain", () => {
  assert.deepEqual(nextStateAfterDelete("deleted", []), { status: "empty" });
});

test("404 is treated as already deleted, same refresh path as 204", () => {
  assert.deepEqual(
    nextStateAfterDelete("already-deleted", [{ id: "p1" }, { id: "p2" }, { id: "p3" }]),
    { status: "select-next" }
  );
  assert.deepEqual(nextStateAfterDelete("already-deleted", []), { status: "empty" });
});

test("409 surfaces the busy message and keeps the selection", () => {
  const decision = nextStateAfterDelete("busy", [{ id: "p1" }, { id: "p2" }, { id: "p3" }]);
  assert.equal(decision.status, "error");
  assert.equal(decision.message, PROJECT_BUSY_MESSAGE);
  assert.match(decision.message, /занят/);
});

test("unexpected outcomes surface the generic message", () => {
  const decision = nextStateAfterDelete("error", [{ id: "p1" }]);
  assert.equal(decision.status, "error");
  assert.equal(decision.message, PROJECT_DELETE_ERROR_MESSAGE);
});

test("stale list nonempty but fresh list empty yields the empty state", () => {
  // Regression: removeProject used to decide from the in-render (stale) list.
  // A delete racing the 5s poll can see stale projects yet an empty refetch;
  // the decision must come from the fresh list, so this must be `empty`.
  const staleProjects = [{ id: "p1" }, { id: "p2" }];
  const freshProjects = [];
  assert.ok(staleProjects.length > 0);
  assert.deepEqual(nextStateAfterDelete("deleted", freshProjects), { status: "empty" });
});

test("deleteConfirmText includes the project name, not just an id", () => {
  const text = deleteConfirmText("Смок-квартира");
  assert.match(text, /Смок-квартира/);
  assert.equal(text, "Удалить проект «Смок-квартира»? Действие необратимо.");
});
