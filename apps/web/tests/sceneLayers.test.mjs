// Zero-dependency unit tests for the sceneLayers helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/sceneLayers.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/sceneLayers.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildSetStateCommand,
  countEntitiesByState,
  entityState,
  ENTITY_STATES,
  ENTITY_STATE_LABELS,
  filterEntitiesByState
} from "../build/sceneLayers.js";

function sceneWith(entities) {
  return {
    scene_id: "scene.1",
    project_id: "project.1",
    entities,
    cameras: [{ id: "camera.main" }]
  };
}

test("entityState defaults missing/null/unknown state to asis", () => {
  assert.equal(entityState({ id: "a", kind: "furniture" }), "asis");
  assert.equal(entityState({ id: "a", kind: "furniture", state: null }), "asis");
  assert.equal(entityState({ id: "a", kind: "furniture", state: "bogus" }), "asis");
  assert.equal(entityState({ id: "a", kind: "furniture", state: undefined }), "asis");
  assert.equal(entityState(null), "asis");
  assert.equal(entityState({ id: "a", kind: "furniture", state: "structure" }), "structure");
  assert.equal(entityState({ id: "a", kind: "furniture", state: "design" }), "design");
  assert.equal(entityState({ id: "a", kind: "furniture", state: "asis" }), "asis");
});

test("ENTITY_STATES and labels keep the display order", () => {
  assert.deepEqual(ENTITY_STATES, ["asis", "structure", "design"]);
  assert.deepEqual(ENTITY_STATE_LABELS, {
    asis: "As-is",
    structure: "Structure",
    design: "Design"
  });
});

test("filterEntitiesByState keeps only visible layers and does not mutate", () => {
  const scene = sceneWith([
    { id: "wall.1", kind: "wall", state: "structure" },
    { id: "sofa.1", kind: "furniture", state: "design" },
    { id: "legacy.1", kind: "furniture" }
  ]);
  const filtered = filterEntitiesByState(scene, ["design", "asis"]);
  assert.deepEqual(
    filtered.entities.map((entity) => entity.id),
    ["sofa.1", "legacy.1"]
  );
  // Cameras and scene fields are copied unchanged; input is untouched.
  assert.deepEqual(filtered.cameras, scene.cameras);
  assert.equal(filtered.scene_id, "scene.1");
  assert.equal(scene.entities.length, 3);
  assert.notEqual(filtered, scene);
  assert.notEqual(filtered.entities, scene.entities);
});

test("filterEntitiesByState with all layers returns every entity", () => {
  const scene = sceneWith([
    { id: "a", kind: "wall", state: "structure" },
    { id: "b", kind: "furniture", state: "design" },
    { id: "c", kind: "furniture" }
  ]);
  const filtered = filterEntitiesByState(scene, ["asis", "structure", "design"]);
  assert.equal(filtered.entities.length, 3);
});

test("countEntitiesByState totals per layer and never omits keys", () => {
  const counts = countEntitiesByState(
    sceneWith([
      { id: "a", kind: "wall", state: "structure" },
      { id: "b", kind: "wall", state: "structure" },
      { id: "c", kind: "furniture", state: "design" },
      { id: "d", kind: "furniture" }
    ])
  );
  assert.deepEqual(counts, { asis: 1, structure: 2, design: 1 });
  const empty = countEntitiesByState(sceneWith([]));
  assert.deepEqual(empty, { asis: 0, structure: 0, design: 0 });
});

test("buildSetStateCommand emits the exact set_state payload", () => {
  const command = buildSetStateCommand({
    commandId: "command-1",
    baseRevisionId: "rev-1",
    targetId: "object.sofa.main",
    state: "structure"
  });
  assert.deepEqual(command, {
    schema_version: "0.1.0",
    command_id: "command-1",
    base_revision_id: "rev-1",
    operation: "set_state",
    target_id: "object.sofa.main",
    parameters: { state: "structure" },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  });
});
