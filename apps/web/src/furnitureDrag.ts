// Pure drag helpers for interactive furniture manipulation in SceneViewer.
//
// Zero dependencies (no three.js, no React) so the module compiles with a bare
// `tsc` and is unit-tested in isolation by node:test (see
// tests/furnitureDrag.test.mjs). Everything here works on canonical scene
// coordinates: millimetres, right-handed, +Z up. The three.js world mapping
// (canonical x,y,z -> three x,z,-y in metres) and all raycasting live in
// SceneViewer.tsx, which feeds canonical floor points into these helpers.

/** Millimetre grid step applied to floor-plane translation. */
export const SNAP_MM = 50;

/** Rotation step (around world Z) applied while Shift-dragging. */
export const ROTATION_STEP_DEG = 5;

/** v0 drag scope: only floor-standing furniture may be manipulated. */
export const DRAGGABLE_KIND = "furniture";

export type EntityLocksLike = {
  geometry?: boolean;
  transform?: boolean;
  material?: boolean;
};

export type EntityTransformLike = {
  translation_mm?: [number, number, number];
  rotation_deg?: [number, number, number];
  scale?: [number, number, number];
};

/** Structural view of a scene entity, independent of the browser-only api.ts. */
export type DragEntityLike = {
  id: string;
  kind: string;
  transform?: EntityTransformLike;
  locks?: EntityLocksLike | null;
};

/** A floor-plane point in canonical millimetres (x, y). */
export type FloorPointMm = {
  x: number;
  y: number;
};

export type MoveObjectParameters = {
  translation_mm?: [number, number, number];
  rotation_deg?: [number, number, number];
  scale?: [number, number, number];
};

/** Exact `move_object` DesignCommand envelope accepted by the backend. */
export type MoveObjectCommandPayload = {
  schema_version: "0.1.0";
  command_id: string;
  base_revision_id: string;
  operation: "move_object";
  target_id: string;
  parameters: MoveObjectParameters;
  reference_asset_ids: string[];
  origin: "user";
  request_text: null;
};

export type MoveObjectInput = {
  commandId: string;
  baseRevisionId: string;
  entity: DragEntityLike;
  parameters: MoveObjectParameters;
};

/** Exact `remove_object` DesignCommand envelope accepted by the backend. */
export type RemoveObjectCommandPayload = {
  schema_version: "0.1.0";
  command_id: string;
  base_revision_id: string;
  operation: "remove_object";
  target_id: string;
  parameters: Record<string, never>;
  reference_asset_ids: string[];
  origin: "user";
  request_text: null;
};

export type RemoveObjectInput = {
  commandId: string;
  baseRevisionId: string;
  targetId: string;
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isFiniteTriplet(value: unknown): value is [number, number, number] {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((entry) => isFiniteNumber(entry))
  );
}

function requireTriplet(
  name: string,
  value: unknown
): [number, number, number] {
  if (!isFiniteTriplet(value)) {
    throw new Error(`move_object ${name} must be a finite triplet`);
  }
  return [value[0], value[1], value[2]];
}

/** Round a canonical mm value to the nearest grid step (grid <= 0 is a no-op). */
export function snapMm(value: number, gridMm: number = SNAP_MM): number {
  if (!(gridMm > 0)) return value;
  return Math.round(value / gridMm) * gridMm;
}

/**
 * Draggable when the entity is floor-standing furniture and neither its
 * transform nor its geometry is locked. This mirrors the backend `move_object`
 * guard (domain/commands.py rejects `locks.transform or locks.geometry`), so
 * the UI never offers a handle the server would refuse. Walls, floors,
 * ceilings, doors, windows and the remaining kinds are never draggable.
 */
export function canDragEntity(
  entity: DragEntityLike | null | undefined
): boolean {
  if (!entity) return false;
  if (entity.kind !== DRAGGABLE_KIND) return false;
  if (entity.locks?.transform) return false;
  if (entity.locks?.geometry) return false;
  return true;
}

/**
 * New canonical translation for a floor-plane drag. The grab offset between the
 * entity origin and the first floor point is preserved so the object does not
 * jump under the cursor, then x/y are snapped to the grid. z is carried over
 * unchanged: floor-standing furniture keeps its height.
 */
export function translationFromFloorPoints(input: {
  startFloor: FloorPointMm;
  currentFloor: FloorPointMm;
  startTranslationMm: [number, number, number];
  gridMm?: number;
}): [number, number, number] {
  const [startX, startY, startZ] = input.startTranslationMm;
  const offsetX = startX - input.startFloor.x;
  const offsetY = startY - input.startFloor.y;
  const grid = input.gridMm ?? SNAP_MM;
  return [
    snapMm(input.currentFloor.x + offsetX, grid),
    snapMm(input.currentFloor.y + offsetY, grid),
    startZ
  ];
}

/** Floor-projected footprint of one entity, canonical millimetres. */
export type DragPrecheckDims = {
  width_mm: number;
  depth_mm: number;
};

/** Room bounds for the pre-check, floor-projected, canonical millimetres. */
export type DragPrecheckRoomBBox = {
  min_x_mm: number;
  min_y_mm: number;
  max_x_mm: number;
  max_y_mm: number;
};

/**
 * Structural view of another entity for the collision pre-check: its current
 * floor position plus the same footprint shape as the dragged entity.
 */
export type DragPrecheckOtherLike = {
  id: string;
  transform?: EntityTransformLike | null;
  width_mm?: number | null;
  depth_mm?: number | null;
};

/** Client-side placement hint (never authoritative; see dragPrecheckAABB). */
export type DragPrecheckResult = {
  inside: boolean;
  collides: boolean;
};

function positiveHalf(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number / 2 : null;
}

/**
 * R2 client-side placement hint for a drag: is the floor-projected AABB of
 * the dragged entity (approximated as an axis-aligned box centred at the
 * given translation) inside the room bounds and clear of the other entities'
 * footprints? Rotation is deliberately ignored (a rotated footprint reads as
 * slightly larger than its AABB — an acceptable false positive for a styling
 * hint) and heights are not considered at all.
 *
 * This is a ghost/warn styling hint ONLY: the authoritative inside/collision
 * decisions stay with the backend. With missing or degenerate inputs (no
 * room bounds, non-finite or non-positive footprint sides) the helper has no
 * opinion and reports a clean result, so a hint can never block a drag.
 */
export function dragPrecheckAABB(
  dims: DragPrecheckDims,
  translation: [number, number, number],
  roomBbox: DragPrecheckRoomBBox | null,
  others: readonly DragPrecheckOtherLike[]
): DragPrecheckResult {
  const result: DragPrecheckResult = { inside: true, collides: false };
  const [tx, ty] = translation;
  const halfWidth = positiveHalf(dims?.width_mm);
  const halfDepth = positiveHalf(dims?.depth_mm);
  if (
    halfWidth === null ||
    halfDepth === null ||
    !Number.isFinite(tx) ||
    !Number.isFinite(ty)
  ) {
    return result;
  }

  const minX = tx - halfWidth;
  const maxX = tx + halfWidth;
  const minY = ty - halfDepth;
  const maxY = ty + halfDepth;

  if (roomBbox) {
    result.inside =
      minX >= roomBbox.min_x_mm &&
      maxX <= roomBbox.max_x_mm &&
      minY >= roomBbox.min_y_mm &&
      maxY <= roomBbox.max_y_mm;
  }

  for (const other of others) {
    const point = other?.transform?.translation_mm;
    const otherHalfWidth = positiveHalf(other?.width_mm);
    const otherHalfDepth = positiveHalf(other?.depth_mm);
    if (
      !point ||
      otherHalfWidth === null ||
      otherHalfDepth === null ||
      !Number.isFinite(point[0]) ||
      !Number.isFinite(point[1])
    ) {
      continue;
    }
    const overlapsX = minX < point[0] + otherHalfWidth && maxX > point[0] - otherHalfWidth;
    const overlapsY = minY < point[1] + otherHalfDepth && maxY > point[1] - otherHalfDepth;
    if (overlapsX && overlapsY) {
      result.collides = true;
      break;
    }
  }

  return result;
}

/** Angle (radians) of `point` around `anchor` in the canonical XY plane. */
export function pointerAngleRad(
  anchor: FloorPointMm,
  point: FloorPointMm
): number {
  return Math.atan2(point.y - anchor.y, point.x - anchor.x);
}

/**
 * New Z rotation (degrees) for a Shift-drag. The pointer angle delta around the
 * entity anchor is added to the entity's start rotation and quantized to the
 * 5 degree step. The delta is normalized into (-180, 180] so a drag across the
 * +/-pi seam reads as a small turn instead of a near-full spin.
 */
export function rotationFromPointerAngles(input: {
  startRotationZdeg: number;
  startPointerAngleRad: number;
  currentPointerAngleRad: number;
  stepDeg?: number;
}): number {
  const step = input.stepDeg ?? ROTATION_STEP_DEG;
  const rawDelta =
    ((input.currentPointerAngleRad - input.startPointerAngleRad) * 180) /
    Math.PI;
  const deltaDeg = (((rawDelta % 360) + 540) % 360) - 180;
  const rawRotation = input.startRotationZdeg + deltaDeg;
  if (!(step > 0)) return rawRotation;
  return Math.round(rawRotation / step) * step;
}

/**
 * Build the authoritative `move_object` DesignCommand payload. Rejects entities
 * outside the v0 drag scope (non-furniture or locked) and non-finite transform
 * triplets, matching what the backend would otherwise answer with a 409.
 */
export function buildMoveObjectCommand(
  input: MoveObjectInput
): MoveObjectCommandPayload {
  const entity = input.entity;
  if (!canDragEntity(entity)) {
    throw new Error(
      `entity is not draggable (kind=${entity?.kind ?? "unknown"}): ${
        entity?.id ?? "unknown"
      }`
    );
  }

  const parameters: MoveObjectParameters = {};
  if (input.parameters.translation_mm !== undefined) {
    parameters.translation_mm = requireTriplet(
      "translation_mm",
      input.parameters.translation_mm
    );
  }
  if (input.parameters.rotation_deg !== undefined) {
    parameters.rotation_deg = requireTriplet(
      "rotation_deg",
      input.parameters.rotation_deg
    );
  }
  if (input.parameters.scale !== undefined) {
    parameters.scale = requireTriplet("scale", input.parameters.scale);
  }
  if (Object.keys(parameters).length === 0) {
    throw new Error("move_object requires at least one transform field");
  }

  return {
    schema_version: "0.1.0",
    command_id: input.commandId,
    base_revision_id: input.baseRevisionId,
    operation: "move_object",
    target_id: entity.id,
    parameters,
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  };
}

export type RotateZInput = {
  commandId: string;
  baseRevisionId: string;
  entity: DragEntityLike;
  degrees?: number;
};

/**
 * Build the authoritative `move_object` payload for the contextual rotate
 * action. This is the exact construction `handleRotateSelected` calls, kept
 * pure and unit-tested so the regression guard exercises the real payload
 * rather than a hand-supplied one.
 *
 * The new rotation is `entity.transform.rotation_deg` (default `[0, 0, 0]`)
 * with `degrees` (default 90) added to Z. `translation_mm` is deliberately
 * never read here: the backend merges only the supplied transform keys, and the
 * selected entity prop can be one in-flight drag behind, so resending a stale
 * translation would revert a just-committed move. Emitting rotation only makes
 * that revert impossible at the payload level.
 */
export function buildRotateZCommand(
  input: RotateZInput
): MoveObjectCommandPayload {
  const entity = input.entity;
  if (typeof entity?.id !== "string" || entity.id.trim().length === 0) {
    throw new Error("move_object target_id must be a non-empty string");
  }

  const baseRotation = entity.transform?.rotation_deg ?? [0, 0, 0];
  const degrees = input.degrees ?? 90;
  const rotation: [number, number, number] = [
    baseRotation[0],
    baseRotation[1],
    baseRotation[2] + degrees
  ];

  return buildMoveObjectCommand({
    commandId: input.commandId,
    baseRevisionId: input.baseRevisionId,
    entity,
    parameters: { rotation_deg: rotation }
  });
}

/**
 * Build the authoritative `remove_object` DesignCommand payload. Mirrors the
 * `move_object` envelope field-for-field; the backend only reads `target_id`
 * (domain/commands.py REMOVE_OBJECT), but the command schema always carries
 * `parameters`, so it is emitted as an empty object. Rejects whitespace-only
 * target ids, matching the other builders' fail-fast style.
 */
export function buildRemoveObjectCommand(
  input: RemoveObjectInput
): RemoveObjectCommandPayload {
  if (typeof input.targetId !== "string" || input.targetId.trim().length === 0) {
    throw new Error("remove_object target_id must be a non-empty string");
  }

  return {
    schema_version: "0.1.0",
    command_id: input.commandId,
    base_revision_id: input.baseRevisionId,
    operation: "remove_object",
    target_id: input.targetId,
    parameters: {},
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  };
}
