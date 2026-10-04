// Zero-dependency unit tests for twinDesign helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/twinDesign.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/twinDesign.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  apiErrorText,
  buildAddFurnitureCommand,
  cameraOptionLabel,
  entityIdFromName,
  formatRenderSummary,
  renderRgbAssetId,
  sortedRendersNewestFirst,
  uniqueId
} from "../build/twinDesign.js";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;


test("renderRgbAssetId reads passes.rgb and rejects empty/missing passes", () => {
  assert.equal(renderRgbAssetId({ passes: { rgb: "asset-rgb", depth: "asset-d" } }), "asset-rgb");
  assert.equal(renderRgbAssetId({ passes: { rgb: "" } }), null);
  assert.equal(renderRgbAssetId({ passes: {} }), null);
  assert.equal(renderRgbAssetId({}), null);
  assert.equal(renderRgbAssetId(null), null);
});

test("formatRenderSummary reports the renderer profile and optional wall seconds", () => {
  assert.equal(
    formatRenderSummary({ renderer_profile: "blender-cycles-v0", wall_seconds: 84.24 }),
    "blender-cycles-v0 · 84.2 s"
  );
  assert.equal(formatRenderSummary({ renderer_profile: "blender-cycles-v0" }), "blender-cycles-v0");
  assert.equal(formatRenderSummary({}), "unknown profile");
  assert.equal(formatRenderSummary(null), "no manifest");
});

test("cameraOptionLabel surfaces calibration method and residual", () => {
  assert.equal(
    cameraOptionLabel({ id: "camera.main", calibration: { method: "correspondences", residual: 1.234 } }),
    "camera.main · correspondences · residual 1.23 px"
  );
  assert.equal(
    cameraOptionLabel({ id: "camera.main", calibration: { method: "manual", observations: [] } }),
    "camera.main · manual"
  );
  assert.equal(cameraOptionLabel({ id: "camera.main" }), "camera.main (uncalibrated)");
  assert.equal(cameraOptionLabel({ id: "camera.main", calibration: {} }), "camera.main (uncalibrated)");
});

test("sortedRendersNewestFirst orders by created_at desc without mutating input", () => {
  const input = [
    { id: "a", created_at: "2026-01-01T00:00:00Z" },
    { id: "c", created_at: "2026-03-01T00:00:00Z" },
    { id: "b", created_at: "2026-02-01T00:00:00Z" }
  ];
  assert.deepEqual(sortedRendersNewestFirst(input).map((item) => item.id), ["c", "b", "a"]);
  assert.deepEqual(input.map((item) => item.id), ["a", "c", "b"]);
});

test("buildAddFurnitureCommand emits the exact add_object payload", () => {
  const command = buildAddFurnitureCommand({
    commandId: "command-1",
    baseRevisionId: "rev-1",
    entityId: "object.sofa.main",
    label: "Sofa",
    roomId: "room.living",
    dimensionsMm: [2200, 900, 800],
    positionMm: [100, 200, 450],
    rotationZdeg: 30,
    color: "#b8b0a4"
  });
  assert.equal(command.operation, "add_object");
  assert.equal(command.target_id, "object.sofa.main");
  assert.equal(command.origin, "user");
  assert.equal(command.base_revision_id, "rev-1");
  const entity = command.parameters.entity;
  assert.equal(entity.id, command.target_id);
  assert.equal(entity.kind, "furniture");
  assert.equal(entity.display_name, "Sofa");
  assert.equal(entity.room_id, "room.living");
  assert.deepEqual(entity.geometry, { dimensions_mm: [2200, 900, 800] });
  assert.deepEqual(entity.transform, {
    translation_mm: [100, 200, 450],
    rotation_deg: [0, 0, 30],
    scale: [1, 1, 1]
  });
  assert.deepEqual(entity.provenance, {
    source: "user",
    asset_ids: [],
    note: "added from twin design panel"
  });
  assert.deepEqual(entity.metadata, { color: "#b8b0a4" });
});

test("buildAddFurnitureCommand omits optional room/color/material fields", () => {
  const command = buildAddFurnitureCommand({
    commandId: "command-2",
    baseRevisionId: "rev-2",
    entityId: "object.chair.accent",
    label: "Chair",
    dimensionsMm: [500, 500, 900],
    positionMm: [0, 0, 0],
    rotationZdeg: 0
  });
  const entity = command.parameters.entity;
  assert.equal("room_id" in entity, false);
  assert.equal("metadata" in entity, false);
  assert.equal("material_ref" in entity, false);
  assert.deepEqual(command.reference_asset_ids, []);
  assert.equal(command.request_text, null);
});

test("entityIdFromName slugifies and falls back for non-ASCII labels", () => {
  assert.equal(entityIdFromName("Accent Chair", "abc123"), "object.accent-chair.abc123");
  assert.equal(entityIdFromName("  Диван  ", "abc123"), "object.furniture.abc123");
  assert.equal(entityIdFromName("Sofa", "x-y/z"), "object.sofa.xyz");
});

test("uniqueId returns valid, unique UUID v4 strings (no prefix)", () => {
  const ids = new Set();
  for (let index = 0; index < 64; index += 1) {
    const id = uniqueId();
    assert.match(id, UUID_V4);
    assert.equal(id.includes("web:"), false);
    ids.add(id);
  }
  assert.equal(ids.size, 64);
});


test("apiErrorText unwraps the backend detail envelope", () => {
  assert.equal(
    apiErrorText(new Error('409: {"detail": {"code": "revision_conflict", "detail": "stale base"}}')),
    "revision_conflict: stale base"
  );
  assert.equal(apiErrorText(new Error('422: {"detail": "bad camera"}')), "bad camera");
  assert.equal(apiErrorText(new Error("network error")), "network error");
});
