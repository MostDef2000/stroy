// Pure helpers for the R1 entity-state layers ("as-is" / "structure" /
// "design"): entity state resolution, layer filtering, per-layer counts and
// the exact `set_state` scene-command payload. No React and no network so the
// module can be unit-tested in isolation (see tests/sceneLayers.test.mjs).
// Structural input types keep this module free of the browser-only api.ts so
// it compiles with a bare `tsc` (same pattern as twinDesign.ts).

/** The three R1 design layers, in display order. */
export type EntityState = "asis" | "structure" | "design";

/** All layers, always in display order (asis → structure → design). */
export const ENTITY_STATES: readonly EntityState[] = ["asis", "structure", "design"];

/** Human-readable layer labels — RU labels are the #188 contract; the enum
 * values (asis | structure | design) are wire values and stay unchanged. */
export const ENTITY_STATE_LABELS: Record<EntityState, string> = {
  asis: "Как есть",
  structure: "Конструктив",
  design: "Дизайн"
};

/** Minimal structural view of a scene entity carrying an optional state. */
export type EntityLike = {
  id: string;
  kind: string;
  state?: string | null;
};

/** Minimal structural view of a scene document (entities only are touched). */
export type SceneLike = {
  entities: EntityLike[];
  [key: string]: unknown;
};

/** Narrow an unknown value to an EntityState, or null when it is not one. */
function toEntityState(value: unknown): EntityState | null {
  return value === "asis" || value === "structure" || value === "design"
    ? value
    : null;
}

/**
 * Resolve an entity's layer. A missing (or unknown/null) `state` means the
 * entity predates the R1 layers and belongs to the as-is layer.
 */
export function entityState(entity: EntityLike | null | undefined): EntityState {
  return toEntityState(entity?.state) ?? "asis";
}

/**
 * Pure layer filter: returns a NEW scene object whose `entities` array keeps
 * only the entities whose resolved state is listed in `visibleStates`.
 * Everything else on the scene (cameras, ids, …) is copied unchanged and the
 * input scene is never mutated.
 */
export function filterEntitiesByState<S extends SceneLike>(
  scene: S,
  visibleStates: readonly EntityState[]
): S {
  const visible = new Set<EntityState>(visibleStates);
  return {
    ...scene,
    entities: scene.entities.filter((entity) => visible.has(entityState(entity)))
  };
}

/**
 * Per-layer entity counts, total over all entities. Always returns all three
 * keys (missing layers count 0) so the UI line never needs fallbacks.
 */
export function countEntitiesByState(scene: SceneLike): Record<EntityState, number> {
  const counts: Record<EntityState, number> = { asis: 0, structure: 0, design: 0 };
  for (const entity of scene.entities) {
    counts[entityState(entity)] += 1;
  }
  return counts;
}

export type SetStateCommandInput = {
  commandId: string;
  baseRevisionId: string;
  targetId: string;
  state: EntityState;
};

export type SetStateCommandPayload = {
  schema_version: "0.1.0";
  command_id: string;
  base_revision_id: string;
  operation: "set_state";
  target_id: string;
  parameters: { state: EntityState };
  reference_asset_ids: string[];
  origin: "user";
  request_text: null;
};

/**
 * Build the authoritative `set_state` DesignCommand payload, mirroring the
 * field style of buildAddFurnitureCommand in twinDesign.ts: only schema-known
 * fields, snake_case, no extras.
 */
export function buildSetStateCommand(
  input: SetStateCommandInput
): SetStateCommandPayload {
  return {
    schema_version: "0.1.0",
    command_id: input.commandId,
    base_revision_id: input.baseRevisionId,
    operation: "set_state",
    target_id: input.targetId,
    parameters: { state: input.state },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  };
}
