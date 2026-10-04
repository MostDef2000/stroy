// Zero-dependency unit tests for furnitureDrag helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/furnitureDrag.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/furnitureDrag.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildMoveObjectCommand,
  canDragEntity,
  pointerAngleRad,
  ROTATION_STEP_DEG,
  rotationFromPointerAngles,
  SNAP_MM,
  snapMm,
  translationFromFloorPoints
} from "../build/furnitureDrag.js";

const furniture = {
  id: "object.sofa.main",
  kind: "furniture",
  transform: {
    translation_mm: [1000, 500, 450],
    rotation_deg: [0, 0, 10],
    scale: [1, 1, 1]
  },
  locks: { geometry: false, transform: false, material: false }
};

test("canDragEntity accepts only unlocked furniture", () => {
  assert.equal(canDragEntity(furniture), true);
  assert.equal(canDragEntity({ ...furniture, locks: undefined }), true);
  assert.equal(
    canDragEntity({ ...furniture, locks: { transform: true } }),
    false
  );
  assert.equal(
    canDragEntity({ ...furniture, locks: { geometry: true } }),
    false
  );
  assert.equal(canDragEntity({ ...furniture, kind: "wall" }), false);
  assert.equal(canDragEntity({ ...furniture, kind: "light" }), false);
  assert.equal(canDragEntity({ ...furniture, kind: "utility" }), false);
  assert.equal(canDragEntity(null), false);
  assert.equal(canDragEntity(undefined), false);
});

test("snapMm rounds to the 50 mm grid and honours a custom grid", () => {
  assert.equal(SNAP_MM, 50);
  assert.equal(snapMm(0), 0);
  assert.equal(snapMm(24), 0);
  assert.equal(snapMm(26), 50);
  assert.equal(snapMm(76), 100);
  assert.equal(snapMm(120), 100);
  assert.equal(snapMm(130), 150);
  assert.equal(snapMm(-120), -100);
  assert.equal(snapMm(123, 25), 125);
  assert.equal(snapMm(7, 0), 7);
});

test("translationFromFloorPoints preserves grab offset, snaps x/y and keeps z", () => {
  const result = translationFromFloorPoints({
    startFloor: { x: 0, y: 0 },
    currentFloor: { x: 137, y: -88 },
    startTranslationMm: [1000, 500, 450]
  });
  // offset (1000, 500) + pointer (137, -88) = (1137, 412) -> grid (1150, 400)
  assert.deepEqual(result, [1150, 400, 450]);

  const custom = translationFromFloorPoints({
    startFloor: { x: 0, y: 0 },
    currentFloor: { x: 123, y: 0 },
    startTranslationMm: [0, 0, 300],
    gridMm: 25
  });
  assert.deepEqual(custom, [125, 0, 300]);
});

test("pointerAngleRad measures the canonical XY angle around the anchor", () => {
  const anchor = { x: 0, y: 0 };
  assert.equal(pointerAngleRad(anchor, { x: 1, y: 0 }), 0);
  assert.ok(
    Math.abs(pointerAngleRad(anchor, { x: 0, y: 1 }) - Math.PI / 2) < 1e-12
  );
  assert.ok(
    Math.abs(Math.abs(pointerAngleRad(anchor, { x: -1, y: 0 })) - Math.PI) <
      1e-12
  );
  // Anchor offset is respected.
  assert.equal(pointerAngleRad({ x: 5, y: 5 }, { x: 6, y: 5 }), 0);
});

test("rotationFromPointerAngles applies the 5 degree step", () => {
  assert.equal(ROTATION_STEP_DEG, 5);
  // No pointer movement keeps the start rotation.
  assert.equal(
    rotationFromPointerAngles({
      startRotationZdeg: 10,
      startPointerAngleRad: 0,
      currentPointerAngleRad: 0
    }),
    10
  );
  // +90 degrees -> start 0 + 90 quantized to 90.
  assert.equal(
    rotationFromPointerAngles({
      startRotationZdeg: 0,
      startPointerAngleRad: 0,
      currentPointerAngleRad: Math.PI / 2
    }),
    90
  );
  // +7 degrees quantizes to +5.
  assert.equal(
    rotationFromPointerAngles({
      startRotationZdeg: 0,
      startPointerAngleRad: 0,
      currentPointerAngleRad: (7 * Math.PI) / 180
    }),
    5
  );
  // Crossing the +/-pi seam is a small turn, not a near-360 spin.
  assert.equal(
    rotationFromPointerAngles({
      startRotationZdeg: 0,
      startPointerAngleRad: Math.PI * 0.99,
      currentPointerAngleRad: -Math.PI * 0.99
    }),
    5
  );
});

test("buildMoveObjectCommand emits the exact move_object payload", () => {
  const command = buildMoveObjectCommand({
    commandId: "command-move-1",
    baseRevisionId: "rev-9",
    entity: furniture,
    parameters: {
      translation_mm: [2000, -300, 450],
      rotation_deg: [0, 0, 15]
    }
  });
  assert.deepEqual(command, {
    schema_version: "0.1.0",
    command_id: "command-move-1",
    base_revision_id: "rev-9",
    operation: "move_object",
    target_id: "object.sofa.main",
    parameters: {
      translation_mm: [2000, -300, 450],
      rotation_deg: [0, 0, 15]
    },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  });
});

test("buildMoveObjectCommand copies only provided transform fields", () => {
  const translationOnly = buildMoveObjectCommand({
    commandId: "command-move-2",
    baseRevisionId: "rev-9",
    entity: furniture,
    parameters: { translation_mm: [50, 50, 450] }
  });
  assert.deepEqual(translationOnly.parameters, {
    translation_mm: [50, 50, 450]
  });
  assert.equal("rotation_deg" in translationOnly.parameters, false);
  assert.equal("scale" in translationOnly.parameters, false);

  const rotationOnly = buildMoveObjectCommand({
    commandId: "command-move-3",
    baseRevisionId: "rev-9",
    entity: furniture,
    parameters: { rotation_deg: [0, 0, 45] }
  });
  assert.deepEqual(rotationOnly.parameters, { rotation_deg: [0, 0, 45] });
});

test("buildMoveObjectCommand rejects non-furniture and locked entities", () => {
  assert.throws(
    () =>
      buildMoveObjectCommand({
        commandId: "c",
        baseRevisionId: "r",
        entity: { id: "surface.wall.1", kind: "wall" },
        parameters: { translation_mm: [0, 0, 0] }
      }),
    /not draggable/
  );
  assert.throws(
    () =>
      buildMoveObjectCommand({
        commandId: "c",
        baseRevisionId: "r",
        entity: { ...furniture, locks: { transform: true } },
        parameters: { translation_mm: [0, 0, 0] }
      }),
    /not draggable/
  );
  assert.throws(
    () =>
      buildMoveObjectCommand({
        commandId: "c",
        baseRevisionId: "r",
        entity: { ...furniture, locks: { geometry: true } },
        parameters: { translation_mm: [0, 0, 0] }
      }),
    /not draggable/
  );
});

test("buildMoveObjectCommand rejects empty or non-finite parameters", () => {
  assert.throws(
    () =>
      buildMoveObjectCommand({
        commandId: "c",
        baseRevisionId: "r",
        entity: furniture,
        parameters: {}
      }),
    /at least one transform field/
  );
  assert.throws(
    () =>
      buildMoveObjectCommand({
        commandId: "c",
        baseRevisionId: "r",
        entity: furniture,
        parameters: { translation_mm: [0, Number.NaN, 0] }
      }),
    /finite triplet/
  );
  assert.throws(
    () =>
      buildMoveObjectCommand({
        commandId: "c",
        baseRevisionId: "r",
        entity: furniture,
        parameters: { rotation_deg: [0, 0] }
      }),
    /finite triplet/
  );
});
