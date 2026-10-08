// Zero-dependency unit tests for the plan stage flow model (#104).
//
// Compile first (build/ is gitignored):
//   apps/web/node_modules/.bin/tsc src/plan-steps.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"

import test from "node:test";
import assert from "node:assert/strict";

import { computePlanStages, planAutosaveDecision } from "../build/plan-steps.js";

const input = (overrides = {}) => ({
  apartmentImageCount: 0,
  hasDraft: false,
  scaleKnown: false,
  committed: false,
  ...overrides
});

const currentAmount = (stages) => stages.filter((stage) => stage.state === "current").length;

const assertInvariant = (stages, label) => {
  assert.ok(
    currentAmount(stages) <= 1,
    `${label}: at most one current stage (got ${currentAmount(stages)})`
  );
};

const byId = (stages, id) => {
  const stage = stages.find((item) => item.id === id);
  assert.ok(stage, `stage "${id}" is present`);
  return stage;
};

test("fresh project (no images): upload is current, everything else todo", () => {
  const stages = computePlanStages(input());
  assert.deepEqual(
    stages.map((stage) => stage.id),
    ["upload", "markup", "scale", "build"]
  );
  assert.deepEqual(
    stages.map((stage) => stage.state),
    ["current", "todo", "todo", "todo"]
  );
  assert.equal(currentAmount(stages), 1);
  assertInvariant(stages, "fresh");
});

test("images but no draft: markup is current, scale and build todo", () => {
  const stages = computePlanStages(input({ apartmentImageCount: 1 }));
  assert.equal(byId(stages, "upload").state, "done");
  assert.equal(byId(stages, "markup").state, "current");
  assert.equal(byId(stages, "scale").state, "todo");
  assert.equal(byId(stages, "build").state, "todo");
  assertInvariant(stages, "images-no-draft");
});

test("draft without scale: scale is current and its hint names the 3D blocker", () => {
  const stages = computePlanStages(input({ apartmentImageCount: 1, hasDraft: true }));
  assert.deepEqual(
    stages.map((stage) => stage.state),
    ["done", "done", "current", "todo"]
  );
  const hint = byId(stages, "scale").hint;
  assert.match(hint, /масштаб/i);
  assert.match(hint, /3D/);
  assertInvariant(stages, "draft-no-scale");
});

test("draft with known scale: build is current, first three done", () => {
  const stages = computePlanStages(
    input({ apartmentImageCount: 1, hasDraft: true, scaleKnown: true })
  );
  assert.deepEqual(
    stages.map((stage) => stage.state),
    ["done", "done", "done", "current"]
  );
  assertInvariant(stages, "draft-scale");
});

test("committed: all four stages done with no current stage", () => {
  const stages = computePlanStages(
    input({ apartmentImageCount: 1, hasDraft: true, scaleKnown: true, committed: true })
  );
  assert.deepEqual(
    stages.map((stage) => stage.state),
    ["done", "done", "done", "done"]
  );
  assert.equal(currentAmount(stages), 0);
  assertInvariant(stages, "committed");
});

test("committed without scale: build still done, scale remains the single current", () => {
  const stages = computePlanStages(
    input({ apartmentImageCount: 1, hasDraft: true, committed: true })
  );
  assert.equal(byId(stages, "upload").state, "done");
  assert.equal(byId(stages, "markup").state, "done");
  assert.equal(byId(stages, "scale").state, "current");
  assert.equal(byId(stages, "build").state, "done");
  assertInvariant(stages, "committed-no-scale");
});

test("every stage carries a non-empty label and Russian hint", () => {
  const stages = computePlanStages(input());
  const labels = {
    upload: "Загрузить план",
    markup: "Проверить разметку",
    scale: "Проверить масштаб",
    build: "Создать / обновить 3D"
  };
  for (const stage of stages) {
    assert.equal(stage.label, labels[stage.id]);
    assert.ok(stage.hint.length > 0, `${stage.id} hint is non-empty`);
  }
});

test("invariant holds across every combination of inputs", () => {
  for (const apartmentImageCount of [0, 1, 4]) {
    for (const hasDraft of [false, true]) {
      for (const scaleKnown of [false, true]) {
        for (const committed of [false, true]) {
          const stages = computePlanStages({
            apartmentImageCount,
            hasDraft,
            scaleKnown,
            committed
          });
          const label = `count=${apartmentImageCount} draft=${hasDraft} scale=${scaleKnown} committed=${committed}`;
          assert.equal(stages.length, 4, label);
          assertInvariant(stages, label);
          for (const stage of stages) {
            assert.ok(
              stage.state === "done" || stage.state === "current" || stage.state === "todo",
              `${label}: valid state`
            );
          }
        }
      }
    }
  }
});

// ---------------------------------------------------------------------------
// R6 plan autosave decision (#183): pure debounce policy —
//   !dirty → wait; inflight → queue; idle ≥ debounce OR dirty ≥ maxDelay →
//   save; otherwise wait. Boundaries are inclusive.
// ---------------------------------------------------------------------------

const decision = (overrides = {}) =>
  planAutosaveDecision({
    dirty: true,
    inflight: false,
    msSinceLastEdit: 0,
    msSinceDirtyStart: 0,
    debounceMs: 1500,
    maxDelayMs: 10000,
    ...overrides
  });

test("autosave: clean draft waits, never saves", () => {
  assert.equal(decision({ dirty: false, msSinceLastEdit: 999999 }), "wait");
  assert.equal(decision({ dirty: false, msSinceDirtyStart: 999999 }), "wait");
});

test("autosave: quiet period past the debounce saves", () => {
  assert.equal(decision({ msSinceLastEdit: 1500, msSinceDirtyStart: 1500 }), "save");
  assert.equal(decision({ msSinceLastEdit: 5000, msSinceDirtyStart: 5000 }), "save");
});

test("autosave: inside the debounce window waits", () => {
  assert.equal(decision({ msSinceLastEdit: 1499, msSinceDirtyStart: 1499 }), "wait");
  assert.equal(decision({ msSinceLastEdit: 200, msSinceDirtyStart: 3000 }), "wait");
});

test("autosave: in-flight save queues a follow-up even past the debounce", () => {
  assert.equal(decision({ inflight: true, msSinceLastEdit: 20000 }), "queue");
  assert.equal(decision({ inflight: true, msSinceDirtyStart: 20000 }), "queue");
});

test("autosave: continuous editing is capped by the max flush delay", () => {
  // User keeps editing (last edit 200ms ago) but the draft has been dirty
  // for 10s+ — the hard cap forces a save.
  assert.equal(decision({ msSinceLastEdit: 200, msSinceDirtyStart: 10000 }), "save");
  assert.equal(decision({ msSinceLastEdit: 0, msSinceDirtyStart: 45000 }), "save");
});

test("autosave: max-delay cap not reached and debounce not reached → wait", () => {
  assert.equal(decision({ msSinceLastEdit: 100, msSinceDirtyStart: 9000 }), "wait");
});

test("autosave: boundaries are inclusive (== debounce, == maxDelay both save)", () => {
  assert.equal(decision({ msSinceLastEdit: 1500, msSinceDirtyStart: 0 }), "save");
  assert.equal(decision({ msSinceLastEdit: 0, msSinceDirtyStart: 10000 }), "save");
});
