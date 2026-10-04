// Zero-dependency unit tests for the Results-page timeline model (#107).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/resultsTimeline.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/resultsTimeline.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildTimelineEntries,
  comparePairFor,
  groupEntriesByDay,
  humanizeOperations
} from "../build/resultsTimeline.js";

const action = (id, createdAt, overrides = {}) => ({
  id,
  created_at: createdAt,
  status: "succeeded",
  job_type: "llm.complete",
  result: { commands: [{ operation: "add_object" }], final_revision_id: `rev-${id}` },
  error: null,
  ...overrides
});

const generation = (id, createdAt, overrides = {}) => ({
  id,
  created_at: createdAt,
  design_revision_id: `rev-${id}`,
  manifest: { output_asset_ids: [`asset-${id}`] },
  ...overrides
});

test("humanizeOperations maps known operations joined with a middle dot", () => {
  assert.equal(
    humanizeOperations(["add_object", "set_color", "set_light_intent"]),
    "добавление объекта · смена цвета · настройка света"
  );
  assert.equal(humanizeOperations(["remove_object"]), "удаление объекта");
  assert.equal(humanizeOperations(["replace_object_from_reference"]), "замена по референсу");
});

test("humanizeOperations skips create_design_revision bookkeeping", () => {
  assert.equal(
    humanizeOperations(["add_object", "create_design_revision"]),
    "добавление объекта"
  );
});

test("humanizeOperations humanizes unknown operations by replacing separators", () => {
  assert.equal(humanizeOperations(["rotate_object_90"]), "rotate object 90");
});

test("humanizeOperations falls back to «изменение сцены» when nothing is humanizable", () => {
  assert.equal(humanizeOperations([]), "изменение сцены");
  assert.equal(humanizeOperations(["create_design_revision"]), "изменение сцены");
});

test("buildTimelineEntries keeps only completed/failed instruction jobs", () => {
  const entries = buildTimelineEntries({
    jobs: [
      action("a", "2026-10-01T10:00:00"),
      action("b", "2026-10-01T11:00:00", { job_type: "render" }),
      action("c", "2026-10-01T12:00:00", { result: null, error: null }),
      action("d", "2026-10-01T13:00:00", { result: null, error: { code: "boom" } })
    ],
    generations: []
  });
  assert.deepEqual(entries.map((entry) => entry.id).sort(), ["a", "d"]);
});

test("buildTimelineEntries merges actions and results sorted by createdAt DESC", () => {
  const entries = buildTimelineEntries({
    jobs: [
      action("job-1", "2026-10-01T10:00:00"),
      action("job-2", "2026-10-03T10:00:00")
    ],
    generations: [generation("gen-1", "2026-10-02T10:00:00")]
  });
  assert.deepEqual(
    entries.map((entry) => [entry.kind, entry.id]),
    [
      ["action", "job-2"],
      ["result", "gen-1"],
      ["action", "job-1"]
    ]
  );
});

test("buildTimelineEntries extracts the first output asset and tolerates a missing manifest", () => {
  const entries = buildTimelineEntries({
    jobs: [],
    generations: [
      generation("gen-1", "2026-10-01T10:00:00"),
      generation("gen-2", "2026-10-02T10:00:00", { manifest: undefined })
    ]
  });
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  assert.equal(byId.get("gen-1").outputAssetId, "asset-gen-1");
  assert.equal(byId.get("gen-2").outputAssetId, null);
  assert.equal(byId.get("gen-1").label, "Рендер версии");
});

test("buildTimelineEntries formats errorText as code: detail with a job_failed fallback", () => {
  const entries = buildTimelineEntries({
    jobs: [
      action("job-1", "2026-10-01T10:00:00", {
        result: null,
        error: { code: "llm_timeout", detail: "timed out" }
      }),
      action("job-2", "2026-10-01T11:00:00", { result: null, error: "plain failure" })
    ],
    generations: []
  });
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  assert.equal(byId.get("job-1").errorText, "llm_timeout: timed out");
  assert.equal(byId.get("job-2").errorText, 'job_failed: "plain failure"');
  assert.equal(byId.get("job-1").finalRevisionId, null);
});

test("groupEntriesByDay labels today, yesterday and older days using injected now", () => {
  const now = new Date(2026, 9, 4, 12, 0, 0); // 2026-10-04
  const entries = [
    { kind: "action", id: "t1", createdAt: "2026-10-04T08:00:00", label: "", status: "", errorText: null, finalRevisionId: null, operations: [] },
    { kind: "action", id: "y1", createdAt: "2026-10-03T23:00:00", label: "", status: "", errorText: null, finalRevisionId: null, operations: [] },
    { kind: "result", id: "o1", createdAt: "2026-05-15T10:00:00", label: "", outputAssetId: null, designRevisionId: "r" }
  ];
  const groups = groupEntriesByDay(entries, now);
  const labels = new Map(groups.map((group) => [group.key, group.label]));
  assert.equal(labels.get("2026-10-04"), "Сегодня");
  assert.equal(labels.get("2026-10-03"), "Вчера");
  assert.equal(labels.get("2026-05-15"), "15 мая");
});

test("groupEntriesByDay orders groups DESC and skips empty days", () => {
  const now = new Date(2026, 9, 4, 12, 0, 0);
  const entries = [
    { kind: "action", id: "m", createdAt: "2026-05-15T10:00:00", label: "", status: "", errorText: null, finalRevisionId: null, operations: [] },
    { kind: "action", id: "t", createdAt: "2026-10-04T10:00:00", label: "", status: "", errorText: null, finalRevisionId: null, operations: [] },
    { kind: "action", id: "y", createdAt: "2026-10-03T10:00:00", label: "", status: "", errorText: null, finalRevisionId: null, operations: [] }
  ];
  const groups = groupEntriesByDay(entries, now);
  assert.deepEqual(
    groups.map((group) => group.key),
    ["2026-10-04", "2026-10-03", "2026-05-15"]
  );
  assert.deepEqual(
    groups.map((group) => group.entries.length),
    [1, 1, 1]
  );
});

test("comparePairFor picks the next-older generation for the after item", () => {
  const generations = [
    generation("c", "2026-01-01T10:00:00"),
    generation("a", "2026-03-01T10:00:00"),
    generation("b", "2026-02-01T10:00:00")
  ];
  assert.deepEqual(comparePairFor(generations, "b"), { beforeId: "c" });
  assert.deepEqual(comparePairFor(generations, "a"), { beforeId: "b" });
});

test("comparePairFor returns null for the oldest or an unknown generation", () => {
  const generations = [
    generation("a", "2026-03-01T10:00:00"),
    generation("b", "2026-02-01T10:00:00")
  ];
  assert.deepEqual(comparePairFor(generations, "b"), { beforeId: null });
  assert.deepEqual(comparePairFor(generations, "missing"), { beforeId: null });
});
