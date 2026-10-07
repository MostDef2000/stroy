// Zero-dependency unit tests for the Results master-detail selection model (#152).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/resultsTimeline.ts src/resultsSelection.ts … \
//     --outDir build --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/resultsSelection.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  defaultSelectionId,
  isRevisionSelection,
  newestGenerationForRevision,
  resolveSelection,
  revisionIdFromSelection,
  revisionSelectionId
} from "../build/resultsSelection.js";

const resultEntry = (id, createdAt, overrides = {}) => ({
  kind: "result",
  id,
  createdAt,
  label: "Рендер версии",
  outputAssetId: `asset-${id}`,
  designRevisionId: `rev-${id}`,
  ...overrides
});

const actionEntry = (id, createdAt, overrides = {}) => ({
  kind: "action",
  id,
  createdAt,
  label: "добавление объекта",
  status: "succeeded",
  errorText: null,
  finalRevisionId: `rev-${id}`,
  operations: ["add_object"],
  ...overrides
});

const generation = (id, createdAt, overrides = {}) => ({
  id,
  created_at: createdAt,
  design_revision_id: `rev-${id}`,
  manifest: { output_asset_ids: [`asset-${id}`] },
  ...overrides
});

const revision = (revisionId, createdAt) => ({
  revision_id: revisionId,
  created_at: createdAt
});

test("revision selection ids are namespaced and round-trip", () => {
  const id = revisionSelectionId("abc-123");
  assert.ok(id.startsWith("rev:"));
  assert.ok(isRevisionSelection(id));
  assert.equal(revisionIdFromSelection(id), "abc-123");
  assert.equal(isRevisionSelection("abc-123"), false);
});

test("resolveSelection returns the result entry with its generation", () => {
  const entries = [actionEntry("job-1", "2026-01-01T10:00:00Z"), resultEntry("gen-2", "2026-01-01T11:00:00Z")];
  const generations = [generation("gen-2", "2026-01-01T11:00:00Z")];
  const detail = resolveSelection({
    selectionId: "gen-2",
    entries,
    revisions: [],
    generations,
    currentRevisionId: null
  });
  assert.equal(detail?.kind, "result");
  assert.equal(detail.kind === "result" && detail.entry.id, "gen-2");
  assert.equal(detail.kind === "result" && detail.generation?.id, "gen-2");
});

test("resolveSelection returns the action entry without a generation", () => {
  const detail = resolveSelection({
    selectionId: "job-1",
    entries: [actionEntry("job-1", "2026-01-01T10:00:00Z")],
    revisions: [],
    generations: [],
    currentRevisionId: null
  });
  assert.equal(detail?.kind, "action");
  assert.equal(detail.kind === "action" && detail.entry.id, "job-1");
});

test("resolveSelection resolves a revision with current flag and newest generation", () => {
  const revisions = [revision("rev-a", "2026-01-01T09:00:00Z")];
  const generations = [
    generation("gen-old", "2026-01-01T10:00:00Z", { design_revision_id: "rev-a" }),
    generation("gen-new", "2026-01-01T12:00:00Z", { design_revision_id: "rev-a" }),
    generation("gen-other", "2026-01-01T13:00:00Z", { design_revision_id: "rev-b" })
  ];
  const detail = resolveSelection({
    selectionId: revisionSelectionId("rev-a"),
    entries: [],
    revisions,
    generations,
    currentRevisionId: "rev-a"
  });
  assert.equal(detail?.kind, "revision");
  if (detail?.kind === "revision") {
    assert.equal(detail.isCurrent, true);
    assert.equal(detail.generation?.id, "gen-new");
  }
});

test("resolveSelection marks a non-current revision as not current", () => {
  const detail = resolveSelection({
    selectionId: revisionSelectionId("rev-a"),
    entries: [],
    revisions: [revision("rev-a", "2026-01-01T09:00:00Z")],
    generations: [],
    currentRevisionId: "rev-b"
  });
  assert.equal(detail?.kind === "revision" && detail.isCurrent, false);
});

test("resolveSelection returns null for unknown or missing ids", () => {
  const input = {
    entries: [resultEntry("gen-2", "2026-01-01T11:00:00Z")],
    revisions: [revision("rev-a", "2026-01-01T09:00:00Z")],
    generations: [],
    currentRevisionId: null
  };
  assert.equal(resolveSelection({ ...input, selectionId: null }), null);
  assert.equal(resolveSelection({ ...input, selectionId: "gen-404" }), null);
  assert.equal(resolveSelection({ ...input, selectionId: revisionSelectionId("rev-404") }), null);
});

test("defaultSelectionId prefers the newest timeline entry", () => {
  const id = defaultSelectionId(
    [resultEntry("gen-old", "2026-01-01T10:00:00Z"), resultEntry("gen-new", "2026-01-01T11:00:00Z")],
    [revision("rev-a", "2026-01-01T09:00:00Z")]
  );
  assert.equal(id, "gen-new");
});

test("defaultSelectionId falls back to the newest revision when there are no entries", () => {
  const id = defaultSelectionId(
    [],
    [revision("rev-old", "2026-01-01T09:00:00Z"), revision("rev-new", "2026-01-02T09:00:00Z")]
  );
  assert.equal(id, revisionSelectionId("rev-new"));
});

test("defaultSelectionId is null with no entries and no revisions", () => {
  assert.equal(defaultSelectionId([], []), null);
});

test("newestGenerationForRevision picks the newest generation of that revision only", () => {
  const generations = [
    generation("gen-1", "2026-01-01T10:00:00Z", { design_revision_id: "rev-a" }),
    generation("gen-2", "2026-01-01T11:00:00Z", { design_revision_id: "rev-a" }),
    generation("gen-3", "2026-01-01T12:00:00Z", { design_revision_id: "rev-b" })
  ];
  assert.equal(newestGenerationForRevision(generations, "rev-a")?.id, "gen-2");
  assert.equal(newestGenerationForRevision(generations, "rev-none"), undefined);
});
