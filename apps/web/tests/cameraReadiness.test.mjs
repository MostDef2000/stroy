// Zero-dependency unit tests for the camera readiness summary (#105).
//
// Compile first (build/ is gitignored):
//   apps/web/node_modules/.bin/tsc src/cameraReadiness.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"

import test from "node:test";
import assert from "node:assert/strict";

import {
  computeCameraReadiness,
  computeRenderReadiness,
  groupViewpointOptions
} from "../build/cameraReadiness.js";

const camera = (calibration) => ({
  id: "camera.main",
  width_px: 1600,
  height_px: 1000,
  intrinsics: { fx: 1200, fy: 1200, cx: 800, cy: 500 },
  transform: { translation_mm: [0, -5000, 1600], rotation_deg: [90, 0, 0] },
  ...(calibration === undefined ? {} : { calibration })
});

test("no cameras: state none with the not-configured label and no detail", () => {
  const readiness = computeCameraReadiness([]);
  assert.equal(readiness.state, "none");
  assert.equal(readiness.label, "Камера не настроена");
  assert.equal(readiness.detail, null);
});

test("manual camera: state uncalibrated, not photo-calibrated label, no detail", () => {
  const readiness = computeCameraReadiness([camera({ method: "manual", observations: [] })]);
  assert.equal(readiness.state, "uncalibrated");
  assert.equal(readiness.label, "Камера не откалибрована по фото");
  assert.equal(readiness.detail, null);
});

test("correspondences camera with quality 0.87: calibrated with rounded detail", () => {
  const readiness = computeCameraReadiness([
    camera({ method: "correspondences", quality: 0.87, residual: 1.2, observations: [] })
  ]);
  assert.equal(readiness.state, "calibrated");
  assert.equal(readiness.label, "Камера откалибрована по фото");
  assert.equal(readiness.detail, "качество 87%");
});

test("correspondences camera without quality: calibrated, detail null", () => {
  const readiness = computeCameraReadiness([
    camera({ method: "correspondences", observations: [] })
  ]);
  assert.equal(readiness.state, "calibrated");
  assert.equal(readiness.label, "Камера откалибрована по фото");
  assert.equal(readiness.detail, null);
});

test("manual camera before a correspondences camera: first calibrated wins", () => {
  const readiness = computeCameraReadiness([
    camera({ method: "manual", observations: [] }),
    camera({ method: "correspondences", quality: 0.5, observations: [] })
  ]);
  assert.equal(readiness.state, "calibrated");
  assert.equal(readiness.label, "Камера откалибрована по фото");
  assert.equal(readiness.detail, "качество 50%");
});

test("correspondences camera with quality 0: calibrated with качество 0%", () => {
  const readiness = computeCameraReadiness([
    camera({ method: "correspondences", quality: 0, observations: [] })
  ]);
  assert.equal(readiness.state, "calibrated");
  assert.equal(readiness.label, "Камера откалибрована по фото");
  assert.equal(readiness.detail, "качество 0%");
});

// ---------------------------------------------------------------------------
// R7 additions (#187/#188): grouped viewpoint picker + render readiness gate.
// ---------------------------------------------------------------------------

const viewpointCamera = (overrides = {}) => ({
  id: "cam-1",
  label: null,
  viewpoint_kind: null,
  calibration: { method: "manual", quality: null },
  provenance: null,
  ...overrides
});

test("groupViewpointOptions: saved/photo grouping with per-group «Камера N» fallbacks", () => {
  const groups = groupViewpointOptions([
    { id: "cam-a", label: "Гостиная", viewpoint_kind: "saved" },
    { id: "cam-b", label: null, viewpoint_kind: "photo" },
    { id: "cam-c", label: null, viewpoint_kind: "saved" },
    { id: "cam-d", label: "Спальня", viewpoint_kind: "photo" },
    { id: "cam-e", viewpoint_kind: null } // legacy camera → saved
  ]);
  assert.deepEqual(
    groups.saved.map((option) => [option.id, option.label]),
    [
      ["cam-a", "Гостиная"],
      ["cam-c", "Камера 2"],
      ["cam-e", "Камера 3"]
    ]
  );
  assert.deepEqual(
    groups.photo.map((option) => [option.id, option.label]),
    [
      ["cam-b", "Камера 1"],
      ["cam-d", "Спальня"]
    ]
  );
  // #188: raw ids stay reachable via option titles, never as labels.
  assert.equal(groups.saved[2].title, "cam-e");
});

test("groupViewpointOptions: empty input → both groups empty", () => {
  assert.deepEqual(groupViewpointOptions([]), { saved: [], photo: [] });
});

test("computeRenderReadiness: calibrated camera → full draft+final flow", () => {
  const readiness = computeRenderReadiness([
    viewpointCamera({ id: "cam-1", calibration: { method: "correspondences" } })
  ]);
  assert.equal(readiness.state, "calibrated");
  assert.equal(readiness.cameraId, "cam-1");
  assert.equal(readiness.allowFinal, true);
  assert.equal(readiness.warning, null);
});

test("computeRenderReadiness: estimated camera → draft only with the approx warning", () => {
  for (const source of ["estimated", "model_inferred"]) {
    const readiness = computeRenderReadiness([
      viewpointCamera({ id: "cam-2", provenance: { source } })
    ]);
    assert.equal(readiness.state, "estimated", source);
    assert.equal(readiness.cameraId, "cam-2", source);
    assert.equal(readiness.allowFinal, false, source);
    assert.equal(readiness.warning, "Финальный рендер требует откалиброванной камеры.", source);
  }
});

test("computeRenderReadiness: user-provenance saved viewpoint → draft only, NOT none (contract §2.3)", () => {
  // Acceptance finding: an owner-saved overview viewpoint (provenance user,
  // manual calibration) must stay renderable — draft with the approx caveat.
  const readiness = computeRenderReadiness([
    viewpointCamera({ id: "cam-3", provenance: { source: "user" } })
  ]);
  assert.equal(readiness.state, "estimated");
  assert.equal(readiness.cameraId, "cam-3");
  assert.equal(readiness.allowFinal, false);
  assert.equal(readiness.warning, "Финальный рендер требует откалиброванной камеры.");
});

test("computeRenderReadiness: camera with no provenance at all → draft only", () => {
  const readiness = computeRenderReadiness([
    viewpointCamera({ id: "cam-4", provenance: null })
  ]);
  assert.equal(readiness.state, "estimated");
  assert.equal(readiness.cameraId, "cam-4");
  assert.equal(readiness.allowFinal, false);
});

test("computeRenderReadiness: no cameras → disabled with the insufficient-precision copy", () => {
  const readiness = computeRenderReadiness([]);
  assert.equal(readiness.state, "none");
  assert.equal(readiness.cameraId, null);
  assert.equal(readiness.warning, "Недостаточно точности для финального рендера");
});

test("computeRenderReadiness: calibrated wins over any other camera", () => {
  const readiness = computeRenderReadiness([
    viewpointCamera({ id: "cam-est", provenance: { source: "estimated" } }),
    viewpointCamera({ id: "cam-cal", calibration: { method: "correspondences" } })
  ]);
  assert.equal(readiness.state, "calibrated");
  assert.equal(readiness.cameraId, "cam-cal");
});

test("computeRenderReadiness: first non-calibrated camera is picked when none calibrated", () => {
  const readiness = computeRenderReadiness([
    viewpointCamera({ id: "cam-a" }),
    viewpointCamera({ id: "cam-b", provenance: { source: "user" } })
  ]);
  assert.equal(readiness.state, "estimated");
  assert.equal(readiness.cameraId, "cam-a");
});
