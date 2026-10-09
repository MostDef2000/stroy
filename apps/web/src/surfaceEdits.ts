// Pure helpers for the R10 surface appearance panel (#186): the color and
// finish preset data, the wall/floor/ceiling editability check, the exact
// `set_color` / `set_material` command cores and the design-instruction
// selection context. No React and no network so the module can be unit-tested
// in isolation (see tests/surfaceEdits.test.mjs). Structural input types keep
// this module free of the browser-only api.ts so it compiles with a bare
// `tsc` (same pattern as sceneIntent.ts / sceneLayers.ts).

/**
 * Calm editorial wall accents (#186). Labels are Russian per the rail
 * contract; hexes are #rrggbb and match the warm-graphite editorial palette
 * (R9): they are also the exact colors the BE render pipeline uses when
 * metadata.color is set (rendering/blender.py prefers metadata.color).
 */
export type SurfaceColorPreset = {
  hex: string;
  label: string;
};

export const SURFACE_COLOR_PRESETS: readonly SurfaceColorPreset[] = [
  { hex: "#e7e1d6", label: "Тёплый белый" },
  { hex: "#d8b37a", label: "Песочный" },
  { hex: "#a7b5a2", label: "Шалфейный" },
  { hex: "#bf8a6d", label: "Терракотовый" },
  { hex: "#8494a7", label: "Серо-синий" },
  { hex: "#4d525b", label: "Графитовый" }
];

/**
 * Floor/ceiling finish presets (#186): the hex is what the owner sees (sent
 * via set_color — the FE viewer and the BE render both key off
 * metadata.color), material_ref is the stable dotted material key following
 * the BE convention (material.<family>.<shade>, cf. services/adapters.py
 * "material.wood.light-oak"). material_ref is exported as data for the
 * material-aware pipeline but the panel sends set_color only — material_ref
 * has no visible effect of its own (the render prefers the hex), and one
 * click must stay one scene revision.
 */
export type SurfaceFinishPreset = {
  hex: string;
  label: string;
  material_ref: string;
};

export const SURFACE_FINISH_PRESETS: readonly SurfaceFinishPreset[] = [
  { hex: "#c9a876", label: "Светлый дуб", material_ref: "material.wood.light-oak" },
  { hex: "#d8c7a8", label: "Тёплый бежевый", material_ref: "material.plaster.warm-beige" },
  { hex: "#a6a29a", label: "Серый камень", material_ref: "material.stone.grey" },
  { hex: "#ece9e2", label: "Светлый потолок", material_ref: "material.plaster.ceiling-light" }
];

/** Minimal structural view of a scene entity for surface decisions. */
export type SurfaceEntityLike = {
  id: string;
  kind: string;
  display_name?: string | null;
};

/**
 * True for the shell surfaces the appearance panel edits. Kind strings mirror
 * the backend EntityKind enum (domain/models.py): wall | floor | ceiling.
 * Null-safe: a missing entity is never an editable surface.
 */
export function isAppearanceEditableSurface(
  entity: { kind?: string | null } | null | undefined
): boolean {
  const kind = entity?.kind;
  return kind === "wall" || kind === "floor" || kind === "ceiling";
}

/** Exact `set_color` command core accepted by the backend (parameters.color
 * lands in entity.metadata["color"]; rejected when locks.material is set). */
export type SetColorCommandPayload = {
  operation: "set_color";
  target_id: string;
  parameters: { color: string };
};

/**
 * Build the exact `set_color` command payload. The dispatcher on the design
 * page wraps this core into the full DesignCommand envelope (fresh
 * base_revision_id, user origin) exactly like the set_intent/set_locks
 * builders in sceneIntent.ts.
 */
export function buildSetColorCommand(
  entityId: string,
  color: string
): SetColorCommandPayload {
  return {
    operation: "set_color",
    target_id: entityId,
    parameters: { color }
  };
}

/** Exact `set_material` command core accepted by the backend (parameters.
 * material_ref lands in entity.material_ref; rejected when locks.material). */
export type SetMaterialCommandPayload = {
  operation: "set_material";
  target_id: string;
  parameters: { material_ref: string };
};

/**
 * Build the exact `set_material` command payload. Not sent by the panel
 * today (set_color carries the visible effect; one click = one revision) —
 * kept here so the FE payload shape stays pinned to the backend contract and
 * unit-tested against it.
 */
export function buildSetMaterialCommand(
  entityId: string,
  materialRef: string
): SetMaterialCommandPayload {
  return {
    operation: "set_material",
    target_id: entityId,
    parameters: { material_ref: materialRef }
  };
}

/**
 * R10 (#186): the optional "what is selected in the UI" hint attached to a
 * design instruction, so deictic wording («этот/его/здесь») resolves to the
 * selected entity. Field names mirror the backend DesignSelectionContext
 * model (api/routes.py): entity_id 1-300, kind 1-80, title ≤300 or null.
 */
export type SelectionContext = {
  entity_id: string;
  kind: string;
  title?: string | null;
};

/**
 * Build the selection context from a scene entity, following the rail's
 * display naming convention (display_name, falling back to the id exactly
 * like the «Выбранный объект» card). Null-safe: returns undefined when the
 * entity is missing so the instruction call stays payload-compatible with
 * the pre-R10 shape (no selection_context key at all).
 */
export function buildSelectionContext(
  entity: SurfaceEntityLike | null | undefined
): SelectionContext | undefined {
  const entityId = typeof entity?.id === "string" ? entity.id.trim() : "";
  const kind = typeof entity?.kind === "string" ? entity.kind.trim() : "";
  if (!entityId || !kind) return undefined;
  const displayName =
    typeof entity?.display_name === "string" ? entity.display_name.trim() : "";
  return {
    entity_id: entityId,
    kind,
    title: displayName || entityId
  };
}
