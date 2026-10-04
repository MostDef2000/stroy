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
