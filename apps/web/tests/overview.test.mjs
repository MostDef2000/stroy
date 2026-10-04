// Zero-dependency unit tests for the overview readiness model.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/overview.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/overview.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import { computeProjectReadiness } from "../build/overview.js";

// A fully-ready project: every item must come back "ok".
const FULLY_READY = {
  hasScene: true,
  sceneCameraCount: 2,
  calibratedCameraCount: 1,
  apartmentAssetCount: 1,
  referenceAssetCount: 3,
  revisionCount: 2,
  activeJobCount: 0
};

const input = (overrides = {}) => ({ ...FULLY_READY, ...overrides });

const byId = (items, id) => {
  const item = items.find((i) => i.id === id);
  assert.ok(item, `item with id "${id}" is present`);
  return item;
};

test("fully-ready project yields all five items ok, in fixed order, no action pages", () => {
  const items = computeProjectReadiness(input());
  assert.equal(items.length, 5);
  assert.deepEqual(
    items.map((i) => i.id),
    ["plan", "scene", "camera", "design", "jobs"]
  );
  assert.deepEqual(
    items.map((i) => i.state),
    ["ok", "ok", "ok", "ok", "ok"]
  );
  assert.ok(items.every((i) => i.actionPage === null));
  // plan detail mentions both asset counts
  assert.equal(byId(items, "plan").detail, "Планов квартиры: 1. Референсов: 3.");
});

test("no apartment asset: plan is pending pointing at the plan page", () => {
  const items = computeProjectReadiness(input({ apartmentAssetCount: 0 }));
  const plan = byId(items, "plan");
  assert.equal(plan.state, "pending");
  assert.equal(plan.actionPage, "plan");
  // everything else stays ready
  assert.equal(byId(items, "scene").state, "ok");
  assert.equal(byId(items, "camera").state, "ok");
});

test("no scene: scene and camera are both attention pointing at the overview page", () => {
  const items = computeProjectReadiness(input({ hasScene: false }));
  const scene = byId(items, "scene");
  const camera = byId(items, "camera");
  assert.equal(scene.state, "attention");
  assert.equal(scene.actionPage, "overview");
  assert.equal(camera.state, "attention");
  assert.equal(camera.actionPage, "overview");
});

test("scene with cameras but none calibrated: camera is pending pointing at design", () => {
  const items = computeProjectReadiness(input({ sceneCameraCount: 2, calibratedCameraCount: 0 }));
  const camera = byId(items, "camera");
  assert.equal(camera.state, "pending");
  assert.equal(camera.actionPage, "design");
});

test("scene with some calibrated cameras: camera is ok, detail shows calibrated/total counts", () => {
  const items = computeProjectReadiness(input({ sceneCameraCount: 3, calibratedCameraCount: 2 }));
  const camera = byId(items, "camera");
  assert.equal(camera.state, "ok");
  assert.equal(camera.actionPage, null);
  assert.equal(camera.detail, "Откалибровано камер: 2 из 3.");
});

test("scene present but without any camera: camera is attention pointing at design", () => {
  const items = computeProjectReadiness(input({ sceneCameraCount: 0, calibratedCameraCount: 0 }));
  const camera = byId(items, "camera");
  assert.equal(camera.state, "attention");
  assert.equal(camera.actionPage, "design");
});

test("design tracks revisionCount: 0 and 1 are pending (different pages), 3 is ok", () => {
  const cases = [
    [0, "pending", "overview"],
    [1, "pending", "design"],
    [3, "ok", null]
  ];
  for (const [revisionCount, state, actionPage] of cases) {
    const design = byId(computeProjectReadiness(input({ revisionCount })), "design");
    assert.equal(design.state, state, `revisionCount=${revisionCount} state`);
    assert.equal(design.actionPage, actionPage, `revisionCount=${revisionCount} actionPage`);
  }
  // ok detail mentions the revision count
  const okDesign = byId(computeProjectReadiness(input({ revisionCount: 3 })), "design");
  assert.equal(okDesign.detail, "Ревизий дизайна: 3.");
});

test("active jobs: jobs is pending pointing at diagnostics; zero jobs keeps it ok", () => {
  const busy = byId(computeProjectReadiness(input({ activeJobCount: 2 })), "jobs");
  assert.equal(busy.state, "pending");
  assert.equal(busy.actionPage, "diagnostics");
  assert.equal(busy.detail, "В работе задач: 2.");

  const idle = byId(computeProjectReadiness(input({ activeJobCount: 0 })), "jobs");
  assert.equal(idle.state, "ok");
  assert.equal(idle.actionPage, null);
});

test("totality: the all-false/zero input does not throw and still returns five items", () => {
  const items = computeProjectReadiness({
    hasScene: false,
    sceneCameraCount: 0,
    calibratedCameraCount: 0,
    apartmentAssetCount: 0,
    referenceAssetCount: 0,
    revisionCount: 0,
    activeJobCount: 0
  });
  assert.equal(items.length, 5);
  assert.deepEqual(
    items.map((i) => i.id),
    ["plan", "scene", "camera", "design", "jobs"]
  );
  assert.deepEqual(
    items.map((i) => i.state),
    ["pending", "attention", "attention", "pending", "ok"]
  );
});
