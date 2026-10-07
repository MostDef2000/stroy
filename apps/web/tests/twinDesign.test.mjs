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
  buildRedesignInput,
  cameraOptionLabel,
  clampStrength,
  entityIdFromName,
  formatRenderSummary,
  redesignResultAssetId,
  referenceImageAssets,
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
  assert.equal(entity.state, "design");
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
  assert.equal(entity.state, "design");
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


test("clampStrength bounds to [0.2, 0.95] and defaults non-finite input", () => {
  assert.equal(clampStrength(0.6), 0.6);
  assert.equal(clampStrength(0.1), 0.2);
  assert.equal(clampStrength(0.99), 0.95);
  assert.equal(clampStrength(Number.NaN), 0.6);
  assert.equal(clampStrength(Number.POSITIVE_INFINITY), 0.6);
});

test("buildRedesignInput trims the prompt and clamps strength", () => {
  const payload = buildRedesignInput({
    baseRevisionId: "rev-1",
    baseAssetId: "asset-rgb",
    prompt: "  warm scandinavian living room  ",
    strength: 0.7
  });
  assert.deepEqual(payload, {
    base_revision_id: "rev-1",
    base_asset_id: "asset-rgb",
    prompt: "warm scandinavian living room",
    strength: 0.7
  });
  assert.equal("reference_asset_id" in payload, false);
});

test("buildRedesignInput includes a present reference and omits null/empty", () => {
  const withReference = buildRedesignInput({
    baseRevisionId: "rev-2",
    baseAssetId: "asset-rgb",
    prompt: "restyle",
    strength: 0.4,
    referenceAssetId: "asset-ref"
  });
  assert.equal(withReference.reference_asset_id, "asset-ref");
  assert.equal(withReference.strength, 0.4);

  const nullReference = buildRedesignInput({
    baseRevisionId: "rev-2",
    baseAssetId: "asset-rgb",
    prompt: "restyle",
    strength: 0.4,
    referenceAssetId: null
  });
  assert.equal("reference_asset_id" in nullReference, false);

  const emptyReference = buildRedesignInput({
    baseRevisionId: "rev-2",
    baseAssetId: "asset-rgb",
    prompt: "restyle",
    strength: 0.4,
    referenceAssetId: ""
  });
  assert.equal("reference_asset_id" in emptyReference, false);
});

test("buildRedesignInput clamps out-of-range strength", () => {
  assert.equal(
    buildRedesignInput({
      baseRevisionId: "rev-3",
      baseAssetId: "asset-rgb",
      prompt: "x",
      strength: 0.05
    }).strength,
    0.2
  );
  assert.equal(
    buildRedesignInput({
      baseRevisionId: "rev-3",
      baseAssetId: "asset-rgb",
      prompt: "x",
      strength: 1.5
    }).strength,
    0.95
  );
});

test("redesignResultAssetId reads top-level output_asset_ids first", () => {
  assert.equal(
    redesignResultAssetId({ status: "succeeded", result: { output_asset_ids: ["out-1", "out-2"] } }),
    "out-1"
  );
  assert.equal(
    redesignResultAssetId({
      status: "succeeded",
      result: { output_asset_ids: [], generation_manifest: { output_asset_ids: ["manifest-out"] } }
    }),
    "manifest-out"
  );
  assert.equal(redesignResultAssetId({ status: "running", result: null }), null);
  assert.equal(redesignResultAssetId({ status: "succeeded", result: {} }), null);
  assert.equal(redesignResultAssetId(null), null);
});

test("referenceImageAssets keeps only image assets with role=reference", () => {
  const assets = [
    { id: "a", role: "reference", media_type: "image/png" },
    { id: "b", role: "reference", media_type: "application/pdf" },
    { id: "c", role: "apartment", media_type: "image/jpeg" },
    { id: "d", role: "derived", media_type: "image/png" }
  ];
  assert.deepEqual(referenceImageAssets(assets).map((asset) => asset.id), ["a"]);
  assert.deepEqual(referenceImageAssets([]), []);
});

test("apiErrorText unwraps the backend detail envelope", () => {
  assert.equal(
    apiErrorText(new Error('409: {"detail": {"code": "revision_conflict", "detail": "stale base"}}')),
    "revision_conflict: stale base"
  );
  assert.equal(apiErrorText(new Error('422: {"detail": "bad camera"}')), "bad camera");
  assert.equal(apiErrorText(new Error("network error")), "network error");
});
