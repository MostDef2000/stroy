// Zero-dependency unit tests for the scene math helpers (R7 #187 inverse
// transforms + auto viewpoint suggestion).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/sceneMath.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/sceneMath.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";
import { Matrix4 } from "three";

import {
  canonicalPositionToThree,
  canonicalRotationToThreeQuaternion,
  roomViewSuggestionFromBounds,
  threeToCanonicalPosition,
  threeToCanonicalRotation,
  viewpointIntrinsicsFromPerspectiveCamera
} from "../build/sceneMath.js";

const EPS = 1e-6;

function closeTo(actual, expected, eps = EPS, message = "") {
  assert.ok(
    Math.abs(actual - expected) <= eps,
    `${message}: expected |${actual} - ${expected}| <= ${eps}`
  );
}

test("position round-trip: threeToCanonicalPosition ∘ canonicalPositionToThree = identity", () => {
  const samples = [
    [0, 0, 0],
    [1200, -3400, 2500],
    [-77.5, 0.25, 3100]
  ];
  for (const [x, y, z] of samples) {
    const three = canonicalPositionToThree([x, y, z]);
    const back = threeToCanonicalPosition(three);
    closeTo(back[0], x, EPS, `x of (${x},${y},${z})`);
    closeTo(back[1], y, EPS, `y of (${x},${y},${z})`);
    closeTo(back[2], z, EPS, `z of (${x},${y},${z})`);
  }
});

test("position golden: canonical [1000, 2000, 3000] mm → three [1, 3, -2] m", () => {
  assert.deepEqual(canonicalPositionToThree([1000, 2000, 3000]), [1, 3, -2]);
  // and the inverse lands back on the same millimetre triplet
  assert.deepEqual(threeToCanonicalPosition([1, 3, -2]), [1000, 2000, 3000]);
});

test("rotation round-trip: threeToCanonicalRotation ∘ canonicalRotationToThreeQuaternion ≈ input", () => {
  // Non-gimbal-lock Euler triples (XYZ): the extracted Euler must reproduce
  // the canonical input angles to 1e-6 degrees.
  const samplesDeg = [
    [0, 0, 0],
    [10, 20, 30],
    [-45, 0, 15],
    [0, -30, 0],
    [179, 0, 0],
    [5, 60, -80]
  ];
  for (const deg of samplesDeg) {
    const quaternion = canonicalRotationToThreeQuaternion(deg);
    const extracted = threeToCanonicalRotation(quaternion);
    closeTo(extracted[0], deg[0], EPS, `rx of [${deg}]`);
    closeTo(extracted[1], deg[1], EPS, `ry of [${deg}]`);
    closeTo(extracted[2], deg[2], EPS, `rz of [${deg}]`);
  }
});

test("threeToCanonicalRotation accepts a Matrix4 equivalently to a Quaternion", () => {
  const quaternion = canonicalRotationToThreeQuaternion([15, -25, 40]);
  const matrix = new Matrix4().makeRotationFromQuaternion(quaternion);
  assert.deepEqual(
    threeToCanonicalRotation(matrix),
    threeToCanonicalRotation(quaternion)
  );
});

test("position reverse round-trip: canonicalPositionToThree ∘ threeToCanonicalPosition = identity", () => {
  const samples = [
    [0, 0, 0],
    [1250, -2200, 3150],
    [-0.25, 900.75, 12]
  ];
  for (const [x, y, z] of samples) {
    const three = threeToCanonicalPosition([x, y, z]);
    const back = canonicalPositionToThree(three);
    closeTo(back[0], x, EPS, `x of (${x},${y},${z})`);
    closeTo(back[1], y, EPS, `y of (${x},${y},${z})`);
    closeTo(back[2], z, EPS, `z of (${x},${y},${z})`);
  }
});

test("rotation reverse round-trip: re-derived quaternion is a fixed point", () => {
  // canonical→three→canonical→three must be a fixed point within 1e-6:
  // extract angles from the three.js quaternion, rebuild the quaternion from
  // the extracted Euler, and compare component-wise.
  const samplesDeg = [
    [10, 20, 30],
    [-45, 0, 15],
    [0, 90, 0],
    [5, 60, -80]
  ];
  for (const deg of samplesDeg) {
    const quaternion = canonicalRotationToThreeQuaternion(deg);
    const extracted = threeToCanonicalRotation(quaternion);
    const again = canonicalRotationToThreeQuaternion(extracted);
    closeTo(again.x, quaternion.x, EPS, `qx of [${deg}]`);
    closeTo(again.y, quaternion.y, EPS, `qy of [${deg}]`);
    closeTo(again.z, quaternion.z, EPS, `qz of [${deg}]`);
    closeTo(again.w, quaternion.w, EPS, `qw of [${deg}]`);
  }
});

test("intrinsics golden: fov 45° / height 1000 px → fx = fy ≈ 1207.1067811865476", () => {
  const intrinsics = viewpointIntrinsicsFromPerspectiveCamera({
    fovDeg: 45,
    widthPx: 1600,
    heightPx: 1000
  });
  closeTo(intrinsics.fx, 1207.1067811865476, 1e-6, "fx");
  closeTo(intrinsics.fy, 1207.1067811865476, 1e-6, "fy");
  assert.equal(intrinsics.cx, 800);
  assert.equal(intrinsics.cy, 500);
});

test("intrinsics inverse: fov = 2·atan(height / (2·fy)) returns the input fov", () => {
  const { fy } = viewpointIntrinsicsFromPerspectiveCamera({
    fovDeg: 60,
    widthPx: 1920,
    heightPx: 1080
  });
  const fovDeg = 2 * Math.atan((1080 / 2) / fy) * (180 / Math.PI);
  closeTo(fovDeg, 60, 1e-9, "recovered fov");
});

test("roomViewSuggestionFromBounds golden: 4×3 m room → diagonal stand-off pose", () => {
  // span = 4000 → distance = 3800, elevation = 3200; centre (2000, 1500).
  const suggestion = roomViewSuggestionFromBounds({
    minX: 0,
    minY: 0,
    maxX: 4000,
    maxY: 3000
  });
  assert.deepEqual(suggestion.positionMm, [4660, 4160, 3200]);
  assert.deepEqual(suggestion.looksAtMm, [2000, 1500, 1100]);
});

test("roomViewSuggestionFromBounds clamps distance/elevation for small rooms", () => {
  // span = 1000 → distance = max(2000, 950) = 2000, elevation = max(1500, 800)
  const suggestion = roomViewSuggestionFromBounds({
    minX: 0,
    minY: 0,
    maxX: 1000,
    maxY: 500
  });
  // centre (500, 250); distance 2000 → +1400 per axis; elevation 1500.
  assert.deepEqual(suggestion.positionMm, [1900, 1650, 1500]);
  assert.deepEqual(suggestion.looksAtMm, [500, 250, 1100]);
});
