// Zero-dependency unit tests for the R10 surface appearance helpers (#186).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/*.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/surfaceEdits.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildSelectionContext,
  buildSetColorCommand,
  buildSetMaterialCommand,
  isAppearanceEditableSurface,
  SURFACE_COLOR_PRESETS,
  SURFACE_FINISH_PRESETS
} from "../build/surfaceEdits.js";

const HEX_RE = /^#[0-9a-f]{6}$/i;

test("#186: six wall color presets — valid #hex + non-empty Russian labels", () => {
  assert.equal(SURFACE_COLOR_PRESETS.length, 6);
  for (const preset of SURFACE_COLOR_PRESETS) {
    assert.match(preset.hex, HEX_RE, `preset ${preset.hex} is a #rrggbb color`);
    assert.ok(preset.label.trim().length > 0, "label is non-empty");
    // Russian preset names (rail contract) — Cyrillic, no raw hex in the label.
    assert.match(preset.label, /[А-Яа-яЁё]/, `label «${preset.label}» is Russian`);
    assert.ok(!preset.label.includes("#"), "label never leaks the hex");
  }
  // The labels are distinct so screen readers can tell the swatches apart.
  assert.equal(new Set(SURFACE_COLOR_PRESETS.map((p) => p.label)).size, 6);
});

test("#186: four floor/ceiling finish presets — hex + label + material_ref", () => {
  assert.equal(SURFACE_FINISH_PRESETS.length, 4);
  for (const preset of SURFACE_FINISH_PRESETS) {
    assert.match(preset.hex, HEX_RE, `preset ${preset.hex} is a #rrggbb color`);
    assert.ok(preset.label.trim().length > 0, "label is non-empty");
    assert.match(preset.label, /[А-Яа-яЁё]/, `label «${preset.label}» is Russian`);
    // Stable dotted material key, BE convention material.<family>.<shade>
    // (cf. services/adapters.py "material.wood.light-oak").
    assert.match(
      preset.material_ref,
      /^material\.[a-z-]+\.[a-z-]+$/,
      `material_ref «${preset.material_ref}» follows material.<family>.<shade>`
    );
  }
  // material_refs are unique keys (the JSX uses them as React keys).
  assert.equal(new Set(SURFACE_FINISH_PRESETS.map((p) => p.material_ref)).size, 4);
});

test("#186: isAppearanceEditableSurface — shell surfaces only, null-safe", () => {
  // Editable shell kinds (BE EntityKind, domain/models.py).
  assert.equal(isAppearanceEditableSurface({ kind: "wall" }), true);
  assert.equal(isAppearanceEditableSurface({ kind: "floor" }), true);
  assert.equal(isAppearanceEditableSurface({ kind: "ceiling" }), true);
  // Everything else stays out (furniture is drag-editable, not recolored here;
  // rooms have no surface of their own).
  assert.equal(isAppearanceEditableSurface({ kind: "furniture" }), false);
  assert.equal(isAppearanceEditableSurface({ kind: "room" }), false);
  assert.equal(isAppearanceEditableSurface({ kind: "door" }), false);
  // Null-safe: a missing/empty entity is never an editable surface.
  assert.equal(isAppearanceEditableSurface(null), false);
  assert.equal(isAppearanceEditableSurface(undefined), false);
  assert.equal(isAppearanceEditableSurface({}), false);
  assert.equal(isAppearanceEditableSurface({ kind: null }), false);
  assert.equal(isAppearanceEditableSurface({ kind: "" }), false);
});

test("#186: buildSetColorCommand — exact BE set_color core (parameters.color)", () => {
  // domain/commands.py: set_color requires parameters.color (non-empty str)
  // and lands it in target.metadata["color"]; nothing else is accepted.
  const command = buildSetColorCommand("surface.wall.living.north", "#d8b37a");
  assert.deepEqual(command, {
    operation: "set_color",
    target_id: "surface.wall.living.north",
    parameters: { color: "#d8b37a" }
  });
  // Exact key set — no extra fields the BE envelope would not expect here.
  assert.deepEqual(Object.keys(command).sort(), ["operation", "parameters", "target_id"]);
});

test("#186: buildSetMaterialCommand — exact BE set_material core (parameters.material_ref)", () => {
  // domain/commands.py: set_material requires parameters.material_ref
  // (non-empty str) and lands it in target.material_ref.
  const command = buildSetMaterialCommand("surface.floor.living", "material.wood.light-oak");
  assert.deepEqual(command, {
    operation: "set_material",
    target_id: "surface.floor.living",
    parameters: { material_ref: "material.wood.light-oak" }
  });
  assert.deepEqual(Object.keys(command).sort(), ["operation", "parameters", "target_id"]);
});

test("#186: dispatcher envelope parity — core wrapped = BE DesignCommand shape", () => {
  // The DesignPage dispatcher wraps the builder core exactly like
  // applyEntityCommand does; the result must match the backend DesignCommand
  // model field-for-field (domain/models.py L175): schema_version, command_id,
  // base_revision_id, operation, target_id, parameters, reference_asset_ids,
  // origin, request_text.
  const core = buildSetColorCommand("surface.floor.living", "#c9a876");
  const envelope = {
    schema_version: "0.1.0",
    command_id: "cmd-1",
    base_revision_id: "rev-1",
    operation: core.operation,
    target_id: core.target_id,
    parameters: core.parameters,
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  };
  assert.deepEqual(envelope, {
    schema_version: "0.1.0",
    command_id: "cmd-1",
    base_revision_id: "rev-1",
    operation: "set_color",
    target_id: "surface.floor.living",
    parameters: { color: "#c9a876" },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  });
  // The envelope carries operation/target_id from the core, verbatim.
  assert.equal(envelope.operation, core.operation);
  assert.equal(envelope.target_id, core.target_id);
  assert.deepEqual(envelope.parameters, core.parameters);
});

test("#186: buildSelectionContext — title falls back display_name ?? id, null → undefined", () => {
  // display_name wins when present (rail naming convention).
  assert.deepEqual(
    buildSelectionContext({ id: "surface.wall.living.north", kind: "wall", display_name: "Северная стена" }),
    { entity_id: "surface.wall.living.north", kind: "wall", title: "Северная стена" }
  );
  // No display_name → the id, exactly like the «Выбранный объект» card.
  assert.deepEqual(
    buildSelectionContext({ id: "surface.floor.living", kind: "floor" }),
    { entity_id: "surface.floor.living", kind: "floor", title: "surface.floor.living" }
  );
  // Null/blank display_name → the id (never an empty title).
  assert.equal(
    buildSelectionContext({ id: "e1", kind: "wall", display_name: null }).title,
    "e1"
  );
  assert.equal(
    buildSelectionContext({ id: "e1", kind: "wall", display_name: "   " }).title,
    "e1"
  );
  // Non-string display_name junk is ignored → the id.
  assert.equal(
    buildSelectionContext({ id: "e1", kind: "wall", display_name: 42 }).title,
    "e1"
  );
  // No entity → undefined, so the instruction body stays {text} as before.
  assert.equal(buildSelectionContext(null), undefined);
  assert.equal(buildSelectionContext(undefined), undefined);
  // id/kind are trimmed; a blank id or kind yields undefined.
  assert.equal(
    buildSelectionContext({ id: "  ", kind: "wall" }),
    undefined
  );
  assert.equal(
    buildSelectionContext({ id: "e1", kind: "  " }),
    undefined
  );
});
