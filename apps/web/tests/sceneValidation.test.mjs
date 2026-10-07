// Zero-dependency unit tests for the sceneValidation helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/sceneValidation.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/sceneValidation.js (build/ is
// gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  CHECK_SEVERITIES,
  groupResults,
  ruleLabel,
  summaryLine,
  topResults
} from "../build/sceneValidation.js";

function check(overrides = {}) {
  return {
    severity: "warning",
    entity_ids: ["object.sofa.main"],
    rule_id: "clearance.walkway_min",
    measured_mm: null,
    expected_min_mm: null,
    explanation: "",
    suggestion: null,
    ...overrides
  };
}

function report(results, summary = null) {
  return {
    schema_version: "0.1.0",
    scene_revision_id: "rev-1",
    scene_content_hash: "hash-1",
    config: {},
    summary: summary ?? { info: 0, warning: 0, error: 0 },
    results
  };
}

test("CHECK_SEVERITIES keeps the severity-first display order", () => {
  assert.deepEqual(CHECK_SEVERITIES, ["error", "warning", "info"]);
});

test("groupResults buckets by severity and preserves the server order", () => {
  const results = [
    check({ rule_id: "clearance.walkway_min", severity: "warning" }),
    check({ rule_id: "object.object_collision", severity: "error" }),
    check({ rule_id: "envelope.door_swing", severity: "warning" }),
    check({ rule_id: "opening.blocked", severity: "info" }),
    check({ rule_id: "object.wall_collision", severity: "error" })
  ];
  const grouped = groupResults(results);
  assert.deepEqual(
    grouped.error.map((entry) => entry.rule_id),
    ["object.object_collision", "object.wall_collision"]
  );
  assert.deepEqual(
    grouped.warning.map((entry) => entry.rule_id),
    ["clearance.walkway_min", "envelope.door_swing"]
  );
  assert.deepEqual(
    grouped.info.map((entry) => entry.rule_id),
    ["opening.blocked"]
  );
});

test("groupResults never omits buckets and drops unknown severities", () => {
  const empty = groupResults([]);
  assert.deepEqual(empty, { error: [], warning: [], info: [] });
  // The input array is never mutated.
  const input = [check({ severity: "error" })];
  groupResults(input);
  assert.equal(input.length, 1);

  const odd = groupResults([check({ severity: "bogus" }), check()]);
  assert.deepEqual(odd, {
    error: [],
    warning: [check()],
    info: []
  });
});

test("topResults truncates severity-first (error → warning → info)", () => {
  const results = [
    check({ rule_id: "info.1", severity: "info" }),
    check({ rule_id: "error.1", severity: "error" }),
    check({ rule_id: "warning.1", severity: "warning" }),
    check({ rule_id: "error.2", severity: "error" }),
    check({ rule_id: "warning.2", severity: "warning" })
  ];
  const top = topResults(report(results), 3);
  assert.deepEqual(
    top.map((entry) => entry.rule_id),
    ["error.1", "error.2", "warning.1"]
  );
  // Default n is 3.
  assert.deepEqual(
    topResults(report(results)).map((entry) => entry.rule_id),
    ["error.1", "error.2", "warning.1"]
  );
  // A large n keeps everything in severity order.
  assert.deepEqual(
    topResults(report(results), 10).map((entry) => entry.rule_id),
    ["error.1", "error.2", "warning.1", "warning.2", "info.1"]
  );
  // n=0 and negative n truncate to nothing instead of throwing.
  assert.deepEqual(topResults(report(results), 0), []);
  assert.deepEqual(topResults(report(results), -1), []);
});

test("summaryLine joins non-zero groups in severity order", () => {
  assert.equal(
    summaryLine(report([], { error: 2, warning: 3, info: 1 })),
    "2 ошибки · 3 предупреждения · 1 замечание"
  );
});

test("summaryLine omits zero groups", () => {
  assert.equal(
    summaryLine(report([], { error: 2, warning: 0, info: 0 })),
    "2 ошибки"
  );
  assert.equal(
    summaryLine(report([], { error: 0, warning: 3, info: 0 })),
    "3 предупреждения"
  );
  assert.equal(
    summaryLine(report([], { error: 0, warning: 0, info: 2 })),
    "2 замечания"
  );
});

test("summaryLine uses correct Russian plural forms", () => {
  assert.equal(
    summaryLine(report([], { error: 1, warning: 0, info: 0 })),
    "1 ошибка"
  );
  assert.equal(
    summaryLine(report([], { error: 5, warning: 11, info: 0 })),
    "5 ошибок · 11 предупреждений"
  );
  assert.equal(
    summaryLine(report([], { error: 21, warning: 0, info: 4 })),
    "21 ошибка · 4 замечания"
  );
});

test("summaryLine reads a clean report as no problems", () => {
  assert.equal(
    summaryLine(report([], { error: 0, warning: 0, info: 0 })),
    "Проблем не найдено"
  );
});

test("ruleLabel maps the v1 rule ids and passes unknown ids through", () => {
  assert.equal(ruleLabel("object.object_collision"), "Столкновение объектов");
  assert.equal(ruleLabel("object.wall_collision"), "Пересечение со стеной");
  assert.equal(ruleLabel("object.outside_room_bounds"), "Вне комнаты");
  assert.equal(ruleLabel("opening.blocked"), "Перекрыт проём");
  assert.equal(ruleLabel("clearance.walkway_min"), "Узкий проход");
  // envelope.* rules map onto the two access labels.
  assert.equal(ruleLabel("envelope.door_swing"), "Мешает открытию");
  assert.equal(ruleLabel("envelope.door_swing_unchecked"), "Мешает открытию");
  assert.equal(ruleLabel("envelope.cabinet_opening"), "Мешает открытию");
  assert.equal(ruleLabel("envelope.chair_pullout"), "Нет доступа");
  assert.equal(ruleLabel("envelope.bed_access"), "Нет доступа");
  assert.equal(ruleLabel("envelope.sofa_access"), "Нет доступа");
  // Unknown/future rule ids are shown as-is.
  assert.equal(ruleLabel("clearance.future_rule"), "clearance.future_rule");
  assert.equal(ruleLabel("totally.unknown"), "totally.unknown");
});
