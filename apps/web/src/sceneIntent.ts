// Pure helpers for the R2 design intents ("keep" / "remove" / "replace") and
// entity locks: intent resolution, the exact `set_intent` / `set_locks`
// command cores, and the client-side mirror of the backend action-block
// matrix. No React and no network so the module can be unit-tested in
// isolation (see tests/sceneIntent.test.mjs). Structural input types keep
// this module free of the browser-only api.ts so it compiles with a bare
// `tsc` (same pattern as sceneLayers.ts / twinDesign.ts).

/** The three R2 design intents, in rail display order. */
export type DesignIntent = "keep" | "remove" | "replace";

/** Human-readable intent labels (Russian, per the R2 rail contract). */
export const INTENT_LABELS: Record<DesignIntent, string> = {
  keep: "Сохранить",
  replace: "Заменить",
  remove: "Убрать"
};

/** Lock flags an entity may carry; R2 adds the `existence` lock. */
export type EntityLocksLike = {
  existence?: boolean;
  transform?: boolean;
  material?: boolean;
  geometry?: boolean;
};

/** Minimal structural view of a scene entity for intent decisions. */
export type IntentEntityLike = {
  id: string;
  kind: string;
  state?: string | null;
  intent?: string | null;
  locks?: EntityLocksLike | null;
};

/** Narrow an unknown value to a DesignIntent, or null when it is not one. */
function toDesignIntent(value: unknown): DesignIntent | null {
  return value === "keep" || value === "remove" || value === "replace"
    ? value
    : null;
}

/**
 * Resolve an entity's design intent. A missing (or unknown/null) `intent`
 * means the user has not expressed one — null, never a default.
 */
export function entityIntent(
  entity: IntentEntityLike | null | undefined
): DesignIntent | null {
  return toDesignIntent(entity?.intent);
}

/** Exact `set_intent` command core accepted by the backend. */
export type SetIntentCommandPayload = {
  operation: "set_intent";
  target_id: string;
  parameters: { intent: DesignIntent | null };
};

/**
 * Build the exact `set_intent` command payload. A null intent is a real
 * value (the rail's «Сбросить» action), so it is passed through as the
 * parameter value rather than omitted.
 */
export function buildSetIntentCommand(
  entityId: string,
  intent: DesignIntent | null
): SetIntentCommandPayload {
  return {
    operation: "set_intent",
    target_id: entityId,
    parameters: { intent }
  };
}

/** Lock flags that may be merged via `set_locks`; keys follow the backend. */
export type EntityLocksPatch = Partial<{
  existence: boolean;
  transform: boolean;
  material: boolean;
  geometry: boolean;
}>;

/** Exact `set_locks` command core accepted by the backend. */
export type SetLocksCommandPayload = {
  operation: "set_locks";
  target_id: string;
  parameters: { locks: EntityLocksPatch };
};

/**
 * Build the exact `set_locks` command payload. Only the supplied keys are
 * copied (the backend merges the patch into the entity's existing locks), so
 * an untouched flag is never overwritten by the UI.
 */
export function buildSetLocksCommand(
  entityId: string,
  partial: EntityLocksPatch
): SetLocksCommandPayload {
  const locks: EntityLocksPatch = {};
  if (partial.existence !== undefined) locks.existence = partial.existence;
  if (partial.transform !== undefined) locks.transform = partial.transform;
  if (partial.material !== undefined) locks.material = partial.material;
  if (partial.geometry !== undefined) locks.geometry = partial.geometry;
  return {
    operation: "set_locks",
    target_id: entityId,
    parameters: { locks }
  };
}

/** Actions the intent matrix guards (setting/clearing a keep is never blocked). */
export type IntentAction = "remove" | "replace";

/**
 * Client-side mirror of the backend guard matrix (a hint only; the server
 * re-checks every command):
 * - a `structure`-state entity is structural shell and may only be kept;
 * - `remove` is blocked by a keep-intent, the existence lock or the
 *   geometry lock;
 * - `replace` is blocked by everything above plus a remove-intent
 *   (replacing an object the user plans to remove makes no sense).
 */
export function intentActionBlocked(
  entity: IntentEntityLike | null | undefined,
  action: IntentAction
): boolean {
  if (entity?.state === "structure") return true;
  if (entity?.locks?.existence) return true;
  if (entity?.locks?.geometry) return true;
  const intent = entityIntent(entity);
  if (intent === "keep") return true;
  if (action === "replace" && intent === "remove") return true;
  return false;
}
