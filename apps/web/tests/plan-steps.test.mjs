// Zero-dependency unit tests for the plan stage flow model (#104).
//
// Compile first (build/ is gitignored):
//   apps/web/node_modules/.bin/tsc src/plan-steps.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"

import test from "node:test";
import assert from "node:assert/strict";

import { computePlanStages } from "../build/plan-steps.js";

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
