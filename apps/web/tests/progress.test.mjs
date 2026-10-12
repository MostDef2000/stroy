// Zero-dependency unit tests for the R12 progress pure module (#162).
//
// Node 22 has no native TypeScript support, so the source is compiled first
// (same ritual as the other pure-module tests):
//   apps/web/node_modules/.bin/tsc src/progress.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/progress.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  CONFIDENCE_LABELS,
  DEVIATION_STATUS_LABELS,
  OBSERVED_KIND_LABELS,
  PROVENANCE_LABELS,
  STAGE_LABELS,
  UNRESOLVED_DEVIATION_STATUSES,
  acceptChangeFor,
  buildAcceptCommand,
  compareTargetDescription,
  confidenceLabel,
  deviationDisplayLine,
  deviationStatusLabel,
  filterUnresolvedDeviations,
  isUnresolvedDeviation,
  observedKindLabel,
  provenanceLabel,
  stageLabel,
  summarizeDeviations
} from "../build/progress.js";

// --- Label maps: every wire enum value round-trips -------------------------

test("stage labels cover the six wire stages (RU)", () => {
  assert.deepEqual(Object.keys(STAGE_LABELS).sort(), [
    "completed",
    "current",
    "demolition",
    "fitout",
    "original",
    "rough"
  ]);
  assert.equal(STAGE_LABELS.original, "Оригинал");
  assert.equal(STAGE_LABELS.demolition, "Демонтаж");
  assert.equal(STAGE_LABELS.rough, "Черновые работы");
  assert.equal(STAGE_LABELS.fitout, "Отделка");
  assert.equal(STAGE_LABELS.current, "Текущее");
  assert.equal(STAGE_LABELS.completed, "Завершено");
  // label function round-trips every value and falls back to the raw value
  for (const [key, label] of Object.entries(STAGE_LABELS)) {
    assert.equal(stageLabel(key), label);
  }
});

test("provenance labels cover the four wire provenances (RU)", () => {
  assert.deepEqual(Object.keys(PROVENANCE_LABELS).sort(), [
    "confirmed",
    "manually_measured",
    "model_inferred",
    "observed"
  ]);
  assert.equal(PROVENANCE_LABELS.observed, "Наблюдение");
  assert.equal(PROVENANCE_LABELS.manually_measured, "Ручной замер");
  assert.equal(PROVENANCE_LABELS.model_inferred, "Модель");
  assert.equal(PROVENANCE_LABELS.confirmed, "Подтверждено владельцем");
  for (const [key, label] of Object.entries(PROVENANCE_LABELS)) {
    assert.equal(provenanceLabel(key), label);
  }
});

test("confidence labels cover low/medium/high (RU)", () => {
  assert.deepEqual(Object.keys(CONFIDENCE_LABELS).sort(), ["high", "low", "medium"]);
  assert.equal(CONFIDENCE_LABELS.low, "Низкая");
  assert.equal(CONFIDENCE_LABELS.medium, "Средняя");
  assert.equal(CONFIDENCE_LABELS.high, "Высокая");
  for (const [key, label] of Object.entries(CONFIDENCE_LABELS)) {
    assert.equal(confidenceLabel(key), label);
  }
});

test("deviation status labels cover the four wire statuses (RU)", () => {
  assert.deepEqual(Object.keys(DEVIATION_STATUS_LABELS).sort(), [
    "accepted",
    "confirmed",
    "discarded",
    "open"
  ]);
  assert.equal(DEVIATION_STATUS_LABELS.open, "Открыто");
  assert.equal(DEVIATION_STATUS_LABELS.confirmed, "Подтверждено");
  assert.equal(DEVIATION_STATUS_LABELS.accepted, "Принято");
  assert.equal(DEVIATION_STATUS_LABELS.discarded, "Отклонено");
  for (const [key, label] of Object.entries(DEVIATION_STATUS_LABELS)) {
    assert.equal(deviationStatusLabel(key), label);
  }
});

test("observed kind labels cover the five wire kinds (RU)", () => {
  assert.deepEqual(Object.keys(OBSERVED_KIND_LABELS).sort(), [
    "missing",
    "note",
    "property_diff",
    "state_diff",
    "unexpected"
  ]);
  assert.equal(OBSERVED_KIND_LABELS.missing, "Отсутствует");
  assert.equal(OBSERVED_KIND_LABELS.unexpected, "Лишнее");
  assert.equal(OBSERVED_KIND_LABELS.property_diff, "Отличие параметра");
  assert.equal(OBSERVED_KIND_LABELS.state_diff, "Отличие состояния");
  assert.equal(OBSERVED_KIND_LABELS.note, "Заметка");
  for (const [key, label] of Object.entries(OBSERVED_KIND_LABELS)) {
    assert.equal(observedKindLabel(key), label);
  }
});

// --- Unresolved filter (open + confirmed) ----------------------------------

test("unresolved filter keeps only open+confirmed and returns a copy", () => {
  assert.deepEqual(UNRESOLVED_DEVIATION_STATUSES, ["open", "confirmed"]);
  assert.equal(isUnresolvedDeviation("open"), true);
  assert.equal(isUnresolvedDeviation("confirmed"), true);
  assert.equal(isUnresolvedDeviation("accepted"), false);
  assert.equal(isUnresolvedDeviation("discarded"), false);
  const list = [
    { id: "a", status: "open" },
    { id: "b", status: "confirmed" },
    { id: "c", status: "accepted" },
    { id: "d", status: "discarded" }
  ];
  const filtered = filterUnresolvedDeviations(list);
  assert.deepEqual(filtered.map((item) => item.id), ["a", "b"]);
  assert.notEqual(filtered, list); // filtered copy, input untouched
  assert.equal(list.length, 4);
});

// --- Summary counts builder ------------------------------------------------

test("summarizeDeviations counts all four statuses plus total", () => {
  const summary = summarizeDeviations([
    { status: "open" },
    { status: "open" },
    { status: "confirmed" },
    { status: "accepted" },
    { status: "discarded" },
    { status: "discarded" },
    { status: "discarded" }
  ]);
  assert.deepEqual(summary, {
    open: 2,
    confirmed: 1,
    accepted: 1,
    discarded: 3,
    total: 7
  });
});

test("summarizeDeviations zero-fills empty and unknown statuses", () => {
  assert.deepEqual(summarizeDeviations([]), {
    open: 0,
    confirmed: 0,
    accepted: 0,
    discarded: 0,
    total: 0
  });
  assert.deepEqual(summarizeDeviations([{ status: "weird" }, { status: "open" }]), {
    open: 1,
    confirmed: 0,
    accepted: 0,
    discarded: 0,
    total: 1
  });
});

// --- Compare-target description --------------------------------------------

test("compareTargetDescription: canonical reads «Каноничная сцена»", () => {
  assert.equal(compareTargetDescription({ kind: "canonical" }), "Каноничная сцена");
  assert.equal(
    compareTargetDescription({ kind: "canonical", variant_id: null, revision_id: "r-1" }),
    "Каноничная сцена"
  );
});

test("compareTargetDescription: null variant id reads «без привязки»", () => {
  assert.equal(
    compareTargetDescription({ kind: "variant", variant_id: null }),
    "Вариант „без привязки“"
  );
  assert.equal(
    compareTargetDescription({ kind: "variant" }),
    "Вариант „без привязки“"
  );
});

test("compareTargetDescription: variant prefers the resolved title", () => {
  const titles = new Map([["v-1", "Гостиная тёплая"]]);
  assert.equal(
    compareTargetDescription({ kind: "variant", variant_id: "v-1" }, titles),
    "Вариант „Гостиная тёплая“"
  );
  // No titles map → the variant id is the honest fallback.
  assert.equal(
    compareTargetDescription({ kind: "variant", variant_id: "v-2" }),
    "Вариант „v-2“"
  );
  // Unknown id in the map → id fallback (never a wrong name).
  assert.equal(
    compareTargetDescription({ kind: "variant", variant_id: "v-2" }, titles),
    "Вариант „v-2“"
  );
});

// --- Deviation display line -------------------------------------------------

const UUID = "0e2f1a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b";

test("display line renders planned → observed values with the property path", () => {
  const line = deviationDisplayLine({
    planned: {
      target_type: "entity",
      entity_id: "object.sofa.main",
      property_path: "color",
      value: "бежевый"
    },
    observed: { kind: "property_diff", value: "серый" }
  });
  assert.equal(line, "Объект sofa.main · color: бежевый → серый");
});

test("display line falls back to the observed kind label without values", () => {
  assert.equal(
    deviationDisplayLine({
      planned: { target_type: "entity", entity_id: "object.chair.1" },
      observed: { kind: "missing", value: null }
    }),
    "Объект chair.1: Отсутствует"
  );
  assert.equal(
    deviationDisplayLine({
      planned: { target_type: "scene" },
      observed: { kind: "note", value: "штробление идёт по плану" }
    }),
    "Сцена: Заметка — штробление идёт по плану"
  );
});

test("display line labels rooms and scenes without raw enum ids", () => {
  assert.equal(
    deviationDisplayLine({
      planned: { target_type: "room", room_id: "room.1", value: "плитка" },
      observed: { kind: "state_diff", value: "ламинат" }
    }),
    "Комната 1: плитка → ламинат"
  );
});

test("display line appends the dangling warning", () => {
  const line = deviationDisplayLine({
    planned: { target_type: "entity", entity_id: "object.sofa.main" },
    observed: { kind: "missing", value: null },
    dangling: true
  });
  assert.equal(
    line,
    "Объект sofa.main: Отсутствует · Запланированный объект больше не существует"
  );
  // no dangling flag → no clause
  assert.equal(
    deviationDisplayLine({
      planned: { target_type: "entity", entity_id: "object.sofa.main" },
      observed: { kind: "missing", value: null }
    }),
    "Объект sofa.main: Отсутствует"
  );
});

test("display line appends the dangling warning for rooms too", () => {
  const line = deviationDisplayLine({
    planned: { target_type: "room", room_id: "room.2", value: "плитка" },
    observed: { kind: "state_diff", value: "ламинат" },
    dangling: true
  });
  assert.equal(
    line,
    "Комната 2: плитка → ламинат · Запланированный объект больше не существует"
  );
});

test("display line truncates long ids past 12 chars with an ellipsis", () => {
  assert.equal(
    deviationDisplayLine({
      planned: {
        target_type: "entity",
        entity_id: "object.abcdefghijklmn",
        value: "белый"
      },
      observed: { kind: "property_diff", value: "серый" }
    }),
    // No property_path → no «·» segment: the colon follows the subject
    // directly (documented deviationDisplayLine format).
    "Объект abcdefghijkl…: белый → серый"
  );
  // exactly 12 chars → no truncation
  assert.equal(
    deviationDisplayLine({
      planned: { target_type: "entity", entity_id: "object.abcdefghijkl" },
      observed: { kind: "missing", value: null }
    }),
    "Объект abcdefghijkl: Отсутствует"
  );
});

test("display line shows the property path for room property diffs", () => {
  const line = deviationDisplayLine({
    planned: {
      target_type: "room",
      room_id: "room.1",
      property_path: "wall.finish",
      value: "плитка"
    },
    observed: { kind: "property_diff", value: "покраска" }
  });
  assert.equal(line, "Комната 1 · wall.finish: плитка → покраска");
});

test("display line never leaks snapshot/variant/revision ids or raw enums", () => {
  const line = deviationDisplayLine({
    id: UUID,
    snapshot_id: UUID,
    planned_revision_id: UUID,
    planned: {
      target_type: "entity",
      entity_id: "object.sofa.main",
      property_path: "material_ref",
      value: "oak"
    },
    observed: { kind: "property_diff", value: "pine" },
    accepted_revision_id: UUID
  });
  assert.equal(line, "Объект sofa.main · material_ref: oak → pine");
  assert.equal(line.includes(UUID), false);
  assert.equal(line.includes("property_diff"), false);
  // the raw target_type enum never appears — it reads as «Объект»
  assert.equal(line.includes("entity"), false);
});

// --- Accept flow helpers -----------------------------------------------------

test("acceptChangeFor offers remove_object only for missing + entity_id", () => {
  assert.deepEqual(
    acceptChangeFor({
      planned: { target_type: "entity", entity_id: "object.sofa.main" },
      observed: { kind: "missing" }
    }),
    { operation: "remove_object", target_id: "object.sofa.main", parameters: {} }
  );
  // no honest preset for the other observed kinds — accept stays disabled
  assert.equal(
    acceptChangeFor({
      planned: { target_type: "entity", entity_id: "object.sofa.main" },
      observed: { kind: "unexpected" }
    }),
    null
  );
  assert.equal(
    acceptChangeFor({
      planned: { target_type: "entity", entity_id: "object.sofa.main" },
      observed: { kind: "property_diff" }
    }),
    null
  );
  assert.equal(
    acceptChangeFor({
      planned: { target_type: "entity", entity_id: "object.sofa.main" },
      observed: { kind: "note" }
    }),
    null
  );
  // missing without a planned entity id cannot be turned into a command
  assert.equal(
    acceptChangeFor({
      planned: { target_type: "scene" },
      observed: { kind: "missing" }
    }),
    null
  );
});

test("buildAcceptCommand wraps a FULL change command in the #162 envelope", () => {
  const change = { operation: "remove_object", target_id: "object.sofa.main", parameters: {} };
  const command = buildAcceptCommand({
    deviationId: "dev-1",
    change,
    baseRevisionId: "rev-head"
  });
  assert.equal(command.schema_version, "0.1.0");
  assert.equal(command.operation, "accept_confirmed_deviation");
  assert.equal(command.base_revision_id, "rev-head");
  assert.equal(command.target_id, "object.sofa.main");
  assert.deepEqual(command.reference_asset_ids, []);
  assert.equal(command.origin, "user");
  assert.equal(command.request_text, null);
  // Wrapper command_id is a UUID v4 (project convention via uniqueId)
  assert.match(
    command.command_id,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
  );
  // The embedded change is a FULL DesignCommand (BE runtime re-validates it
  // with pydantic): schema_version + fresh command_id + same head + origin.
  const embedded = command.parameters.change;
  assert.deepEqual(
    Object.keys(embedded).sort(),
    [
      "base_revision_id",
      "command_id",
      "operation",
      "origin",
      "parameters",
      "reference_asset_ids",
      "schema_version",
      "target_id"
    ]
  );
  assert.equal(embedded.schema_version, "0.1.0");
  assert.equal(embedded.base_revision_id, "rev-head");
  assert.equal(embedded.operation, "remove_object");
  assert.equal(embedded.target_id, "object.sofa.main");
  assert.deepEqual(embedded.parameters, {});
  assert.deepEqual(embedded.reference_asset_ids, []);
  assert.equal(embedded.origin, "user");
  assert.match(
    embedded.command_id,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
  );
  // Embedded id is fresh — distinct from the wrapper's id.
  assert.notEqual(embedded.command_id, command.command_id);
  // Linkage pinned by the BE envelope validation.
  assert.equal(command.parameters.deviation_id, "dev-1");
});

test("buildAcceptCommand uses fresh uuids per call (no reuse)", () => {
  const change = { operation: "remove_object", target_id: "object.x", parameters: {} };
  const first = buildAcceptCommand({ deviationId: "d", change, baseRevisionId: "r" });
  const second = buildAcceptCommand({ deviationId: "d", change, baseRevisionId: "r" });
  assert.notEqual(first.command_id, second.command_id);
  assert.notEqual(
    first.parameters.change.command_id,
    second.parameters.change.command_id
  );
});
