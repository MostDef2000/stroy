// Zero-dependency unit tests for the R4 sceneVariants helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/sceneVariants.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled modules land at ../build/sceneVariants.js (+ twinDesign.js via
// the import) — build/ is gitignored.

import test from "node:test";
import assert from "node:assert/strict";

import {
  budgetDeltaLabel,
  budgetFormat,
  budgetKindLabel,
  buildVariantCommandEnvelope,
  canTransition,
  DEFAULT_BUDGET_CURRENCY,
  diffSummary,
  formatMoney,
  incompleteLabel,
  lineageRevisions,
  STATUS_LABELS,
  variantSort,
  visibleVariants
} from "../build/sceneVariants.js";

const draft = {
  id: "v1",
  title: "Вариант 1",
  status: "draft",
  created_at: "2026-10-01T10:00:00Z"
};
const shortlisted = { ...draft, id: "v2", status: "shortlisted" };
const approved = { ...draft, id: "v3", status: "approved" };
const archived = { ...draft, id: "v4", status: "archived" };

test("STATUS_LABELS keeps the exact ru chip labels", () => {
  assert.deepEqual(STATUS_LABELS, {
    draft: "Черновик",
    shortlisted: "Шорт-лист",
    approved: "Утверждён",
    archived: "В архиве"
  });
});

test("canTransition allows exactly the five legal moves", () => {
  assert.equal(canTransition("draft", "shortlisted"), true);
  assert.equal(canTransition("shortlisted", "approved"), true);
  assert.equal(canTransition("draft", "archived"), true);
  assert.equal(canTransition("shortlisted", "archived"), true);
  assert.equal(canTransition("approved", "archived"), true);
});

test("canTransition rejects every illegal transition (full 4x4 matrix)", () => {
  const statuses = ["draft", "shortlisted", "approved", "archived"];
  for (const from of statuses) {
    for (const to of statuses) {
      const legal =
        (from === "draft" && to === "shortlisted") ||
        (from === "shortlisted" && to === "approved") ||
        (from === "draft" && to === "archived") ||
        (from === "shortlisted" && to === "archived") ||
        (from === "approved" && to === "archived");
      assert.equal(canTransition(from, to), legal, `${from} -> ${to}`);
    }
  }
});

test("variantSort orders by status weight (approved→shortlisted→draft→archived)", () => {
  const sorted = [draft, archived, shortlisted, approved].sort(variantSort);
  assert.deepEqual(
    sorted.map((variant) => variant.id),
    ["v3", "v2", "v1", "v4"]
  );
});

test("variantSort breaks status ties newest-created first", () => {
  const older = { ...draft, id: "old", created_at: "2026-09-01T10:00:00Z" };
  const newer = { ...draft, id: "new", created_at: "2026-10-05T10:00:00Z" };
  assert.deepEqual([older, newer].sort(variantSort).map((v) => v.id), [
    "new",
    "old"
  ]);
});

test("visibleVariants hides archived by default and shows them with the toggle", () => {
  const list = [draft, shortlisted, approved, archived];
  assert.deepEqual(
    visibleVariants(list, false).map((v) => v.id),
    ["v1", "v2", "v3"]
  );
  assert.deepEqual(
    visibleVariants(list, true).map((v) => v.id),
    ["v1", "v2", "v3", "v4"]
  );
  // Never mutates the polled input list.
  assert.equal(list.length, 4);
});

test("buildVariantCommandEnvelope emits the exact canonical envelope shape", () => {
  const envelope = buildVariantCommandEnvelope(
    {
      operation: "set_intent",
      target_id: "object.sofa.main",
      parameters: { intent: "keep" }
    },
    "rev-head-1",
    ["asset-1"]
  );
  assert.deepEqual(envelope, {
    schema_version: "0.1.0",
    command_id: envelope.command_id,
    base_revision_id: "rev-head-1",
    operation: "set_intent",
    target_id: "object.sofa.main",
    parameters: { intent: "keep" },
    reference_asset_ids: ["asset-1"],
    origin: "user",
    request_text: null
  });
  // command_id is the project-convention bare UUID v4.
  assert.match(
    envelope.command_id,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
  );
});

test("buildVariantCommandEnvelope defaults reference_asset_ids to [] and mints fresh ids", () => {
  const first = buildVariantCommandEnvelope(
    { operation: "set_locks", target_id: "w1", parameters: { locks: {} } },
    "rev-head-2"
  );
  const second = buildVariantCommandEnvelope(
    { operation: "set_locks", target_id: "w1", parameters: { locks: {} } },
    "rev-head-2"
  );
  assert.deepEqual(first.reference_asset_ids, []);
  assert.notEqual(first.command_id, second.command_id);
});

test("diffSummary renders the ru counts line", () => {
  assert.equal(
    diffSummary({
      entities: { added: ["a", "b"], removed: ["c"], modified: [{ id: "d" }] },
      materials: { added: ["m1"], removed: [] },
      validation: { added: [{ rule_id: "r", entity_ids: [] }], resolved: [] },
      renders: { left: [1], right: [1, 2] }
    }),
    "Объекты: +2 -1 ~1 · Материалы: +1 -0 · Проверки: +1 -0 · Рендеры: 1 / 2"
  );
  assert.equal(
    diffSummary({
      entities: { added: [], removed: [], modified: [] },
      materials: { added: [], removed: [] },
      validation: { added: [], resolved: [] },
      renders: { left: [], right: [] }
    }),
    "Объекты: +0 -0 ~0 · Материалы: +0 -0 · Проверки: +0 -0 · Рендеры: 0 / 0"
  );
});

test("formatMoney groups thousands with spaces and uses ₽ for RUB", () => {
  assert.equal(formatMoney(1250), "1 250 ₽");
  assert.equal(formatMoney(1234567.89), "1 234 567,89 ₽");
  assert.equal(formatMoney(0), "0 ₽");
  assert.equal(formatMoney(-2000.5), "-2 000,50 ₽");
  assert.equal(formatMoney(100, "USD"), "100 USD");
  assert.equal(formatMoney(100, DEFAULT_BUDGET_CURRENCY), "100 ₽");
});

test("budgetFormat renders the report grand total", () => {
  assert.equal(
    budgetFormat({ totals: { known: 1000, contingency: 250, grand_total: 1250 } }),
    "1 250 ₽"
  );
});

test("budgetFormat/budgetDeltaLabel pass the report currency through (₽ only as RUB fallback)", () => {
  assert.equal(
    budgetFormat({
      totals: { known: 1000, contingency: 0, grand_total: 1000 },
      currency: "USD"
    }),
    "1 000 USD"
  );
  assert.equal(
    budgetFormat({
      totals: { known: 1000, contingency: 250, grand_total: 1250 },
      currency: null
    }),
    "1 250 ₽"
  );
  assert.equal(
    budgetDeltaLabel(
      { delta: { known: 250, contingency: 0, grand_total: 250 } },
      "USD"
    ),
    "Δ +250 USD"
  );
  // Missing currency arg keeps the RUB-default fallback (existing behavior).
  assert.equal(
    budgetDeltaLabel({ delta: { known: 250, contingency: 0, grand_total: 250 } }),
    "Δ +250 ₽"
  );
});

test("incompleteLabel maps the backend fact codes to the ru list", () => {
  assert.equal(incompleteLabel([]), "");
  assert.equal(incompleteLabel(["missing_price"]), "нет цены");
  assert.equal(incompleteLabel(["missing_quantity"]), "нет количества");
  assert.equal(incompleteLabel(["missing_currency"]), "нет валюты");
  assert.equal(incompleteLabel(["mixed_currency"]), "несколько валют");
  assert.equal(
    incompleteLabel(["missing_price", "missing_quantity"]),
    "нет цены, нет количества"
  );
  // Unknown codes pass through defensively instead of vanishing.
  assert.equal(incompleteLabel(["mystery"]), "mystery");
});

test("budgetDeltaLabel shows the signed delta or «неполные данные»", () => {
  assert.equal(budgetDeltaLabel({ delta: null }), "неполные данные");
  assert.equal(
    budgetDeltaLabel({ delta: { known: 1000, contingency: 250, grand_total: 1250 } }),
    "Δ +1 250 ₽"
  );
  assert.equal(
    budgetDeltaLabel({ delta: { known: -1000, contingency: 0, grand_total: -1000 } }),
    "Δ -1 000 ₽"
  );
  assert.equal(
    budgetDeltaLabel({ delta: { known: 0, contingency: 0, grand_total: 0 } }),
    "Δ 0 ₽"
  );
});

test("budgetKindLabel maps the shipped kinds and falls back to the raw kind", () => {
  assert.equal(budgetKindLabel("manual"), "Вручную");
  assert.equal(budgetKindLabel("material"), "Материалы");
  assert.equal(budgetKindLabel("candidate"), "Товары");
  assert.equal(budgetKindLabel("custom"), "custom");
});

test("lineageRevisions walks head → base inclusive (head-first)", () => {
  const revisions = [
    { revision_id: "rev-a", parent_revision_id: null },
    { revision_id: "rev-b", parent_revision_id: "rev-a" },
    { revision_id: "rev-c", parent_revision_id: "rev-b" },
    { revision_id: "rev-canonical", parent_revision_id: "rev-a" }
  ];
  assert.deepEqual(
    lineageRevisions("rev-c", "rev-a", revisions).map((node) => node.revision_id),
    ["rev-c", "rev-b", "rev-a"]
  );
  // Head == base → single-element lineage (fresh variant/fork).
  assert.deepEqual(
    lineageRevisions("rev-c", "rev-c", revisions).map((node) => node.revision_id),
    ["rev-c"]
  );
  // Missing nodes stop the walk instead of throwing.
  assert.deepEqual(
    lineageRevisions("rev-x", "rev-a", revisions),
    []
  );
  // Cycle guard: a corrupt parent chain terminates.
  const cyclic = [
    { revision_id: "r1", parent_revision_id: "r2" },
    { revision_id: "r2", parent_revision_id: "r1" }
  ];
  assert.deepEqual(
    lineageRevisions("r1", "nope", cyclic).map((node) => node.revision_id),
    ["r1", "r2"]
  );
});
