// Zero-dependency unit tests for the camera readiness summary (#105).
//
// Compile first (build/ is gitignored):
//   apps/web/node_modules/.bin/tsc src/cameraReadiness.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"

import test from "node:test";
import assert from "node:assert/strict";

import { computeCameraReadiness } from "../build/cameraReadiness.js";

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
