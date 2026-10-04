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
  buildRemoveObjectCommand,
  buildRotateZCommand,
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

test("buildMoveObjectCommand accepts a rotation-only payload with no stale translation", () => {
  // The contextual «Повернуть на 90°» action must not resend translation_mm:
  // the backend only overwrites supplied keys, so a stale pre-drag translation
  // would silently revert a just-committed drag.
  const command = buildMoveObjectCommand({
    commandId: "command-rotate-only",
    baseRevisionId: "rev-42",
    entity: furniture,
    parameters: { rotation_deg: [0, 0, 90] }
  });
  assert.deepEqual(command.parameters, { rotation_deg: [0, 0, 90] });
  assert.equal("translation_mm" in command.parameters, false);
  assert.equal("scale" in command.parameters, false);
});

test("buildMoveObjectCommand rotation-only keeps the exact move_object envelope", () => {
  const command = buildMoveObjectCommand({
    commandId: "command-rotate-envelope",
    baseRevisionId: "rev-7",
    entity: furniture,
    parameters: { rotation_deg: [0, 0, 180] }
  });
  assert.deepEqual(command, {
    schema_version: "0.1.0",
    command_id: "command-rotate-envelope",
    base_revision_id: "rev-7",
    operation: "move_object",
    target_id: "object.sofa.main",
    parameters: { rotation_deg: [0, 0, 180] },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  });
});

test("buildRotateZCommand ignores a stale translation and emits rotation only", () => {
  // The selected entity prop can be one drag behind: here it still carries the
  // pre-drag [500,0,0] translation while a drag to another spot has already
  // committed. The builder must not read translation_mm, otherwise it would
  // revert that drag at the payload level.
  const stale = {
    id: "object.sofa.main",
    kind: "furniture",
    transform: {
      translation_mm: [500, 0, 0],
      rotation_deg: [0, 0, 0]
    },
    locks: { geometry: false, transform: false }
  };
  const command = buildRotateZCommand({
    commandId: "command-rotate-1",
    baseRevisionId: "rev-11",
    entity: stale
  });
  assert.deepEqual(command.parameters, { rotation_deg: [0, 0, 90] });
  assert.equal("translation_mm" in command.parameters, false);
  assert.equal("scale" in command.parameters, false);
});

test("buildRotateZCommand honours a custom degrees step", () => {
  const zeroed = {
    id: "object.sofa.main",
    kind: "furniture",
    transform: { rotation_deg: [0, 0, 0] }
  };
  const command = buildRotateZCommand({
    commandId: "command-rotate-2",
    baseRevisionId: "rev-11",
    entity: zeroed,
    degrees: 180
  });
  assert.deepEqual(command.parameters, { rotation_deg: [0, 0, 180] });
  assert.equal("translation_mm" in command.parameters, false);
});

test("buildRotateZCommand defaults a missing transform to a 90 degree turn", () => {
  const command = buildRotateZCommand({
    commandId: "command-rotate-3",
    baseRevisionId: "rev-11",
    entity: { id: "object.chair.left", kind: "furniture" }
  });
  assert.deepEqual(command.parameters, { rotation_deg: [0, 0, 90] });
});

test("buildRotateZCommand adds to the entity's existing Z rotation", () => {
  const command = buildRotateZCommand({
    commandId: "command-rotate-4",
    baseRevisionId: "rev-11",
    entity: furniture
  });
  // furniture.rotation_deg is [0, 0, 10] -> +90 = 100.
  assert.deepEqual(command.parameters, { rotation_deg: [0, 0, 100] });
});

test("buildRotateZCommand rejects empty or whitespace-only entity ids", () => {
  for (const id of ["", "   ", "\t\n"]) {
    assert.throws(
      () =>
        buildRotateZCommand({
          commandId: "c",
          baseRevisionId: "r",
          entity: { ...furniture, id }
        }),
      /non-empty string/
    );
  }
});

test("buildRotateZCommand emits the exact move_object envelope", () => {
  const command = buildRotateZCommand({
    commandId: "command-rotate-envelope",
    baseRevisionId: "rev-7",
    entity: { id: "object.sofa.main", kind: "furniture" }
  });
  assert.deepEqual(command, {
    schema_version: "0.1.0",
    command_id: "command-rotate-envelope",
    base_revision_id: "rev-7",
    operation: "move_object",
    target_id: "object.sofa.main",
    parameters: { rotation_deg: [0, 0, 90] },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  });
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

test("buildRemoveObjectCommand emits the exact remove_object payload", () => {
  const command = buildRemoveObjectCommand({
    commandId: "command-remove-1",
    baseRevisionId: "rev-9",
    targetId: "object.sofa.main"
  });
  assert.deepEqual(command, {
    schema_version: "0.1.0",
    command_id: "command-remove-1",
    base_revision_id: "rev-9",
    operation: "remove_object",
    target_id: "object.sofa.main",
    parameters: {},
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  });
});

test("buildRemoveObjectCommand rejects empty or whitespace-only target ids", () => {
  for (const targetId of ["", "   ", "\t\n"]) {
    assert.throws(
      () =>
        buildRemoveObjectCommand({
          commandId: "c",
          baseRevisionId: "r",
          targetId
        }),
      /non-empty string/
    );
  }
});

test("buildRemoveObjectCommand accepts a valid id unchanged", () => {
  const command = buildRemoveObjectCommand({
    commandId: "c",
    baseRevisionId: "r",
    targetId: "object.chair.left"
  });
  assert.equal(command.target_id, "object.chair.left");
  assert.equal(command.operation, "remove_object");
});

test("buildRemoveObjectCommand does not leak parameters mutations between calls", () => {
  const first = buildRemoveObjectCommand({
    commandId: "c1",
    baseRevisionId: "r",
    targetId: "object.sofa.main"
  });
  first.parameters.injected = true;
  const second = buildRemoveObjectCommand({
    commandId: "c2",
    baseRevisionId: "r",
    targetId: "object.sofa.main"
  });
  assert.deepEqual(second.parameters, {});
  assert.deepEqual(first.reference_asset_ids, []);
  assert.deepEqual(second.reference_asset_ids, []);
});

test("buildRemoveObjectCommand returns fresh objects per call", () => {
  const first = buildRemoveObjectCommand({
    commandId: "c1",
    baseRevisionId: "r",
    targetId: "object.sofa.main"
  });
  const second = buildRemoveObjectCommand({
    commandId: "c1",
    baseRevisionId: "r",
    targetId: "object.sofa.main"
  });
  assert.notEqual(first, second);
  assert.notEqual(first.parameters, second.parameters);
  assert.notEqual(first.reference_asset_ids, second.reference_asset_ids);
  assert.deepEqual(first, second);
});
