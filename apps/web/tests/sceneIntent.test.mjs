// Zero-dependency unit tests for the sceneIntent helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/sceneIntent.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/sceneIntent.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildSetIntentCommand,
  buildSetLocksCommand,
  entityIntent,
  intentActionBlocked,
  INTENT_LABELS
} from "../build/sceneIntent.js";

const sofa = {
  id: "object.sofa.main",
  kind: "furniture"
};

test("INTENT_LABELS keeps the exact rail labels", () => {
  assert.deepEqual(INTENT_LABELS, {
    keep: "Сохранить",
    replace: "Заменить",
    remove: "Убрать"
  });
});

test("entityIntent resolves known intents and defaults unknown/missing to null", () => {
  assert.equal(entityIntent({ ...sofa, intent: "keep" }), "keep");
  assert.equal(entityIntent({ ...sofa, intent: "remove" }), "remove");
  assert.equal(entityIntent({ ...sofa, intent: "replace" }), "replace");
  // Missing, null and unknown intents are "not expressed" — never a default.
  assert.equal(entityIntent(sofa), null);
  assert.equal(entityIntent({ ...sofa, intent: null }), null);
  assert.equal(entityIntent({ ...sofa, intent: "bogus" }), null);
  assert.equal(entityIntent(undefined), null);
  assert.equal(entityIntent(null), null);
});

test("buildSetIntentCommand emits the exact set_intent core payload", () => {
  assert.deepEqual(buildSetIntentCommand("object.sofa.main", "keep"), {
    operation: "set_intent",
    target_id: "object.sofa.main",
    parameters: { intent: "keep" }
  });
  assert.deepEqual(buildSetIntentCommand("object.sofa.main", "replace"), {
    operation: "set_intent",
    target_id: "object.sofa.main",
    parameters: { intent: "replace" }
  });
  assert.deepEqual(buildSetIntentCommand("object.sofa.main", "remove"), {
    operation: "set_intent",
    target_id: "object.sofa.main",
    parameters: { intent: "remove" }
  });
});

test("buildSetIntentCommand passes a null intent through as the parameter value", () => {
  // «Сбросить» is a real command: null must be sent, not omitted.
  const command = buildSetIntentCommand("object.sofa.main", null);
  assert.deepEqual(command, {
    operation: "set_intent",
    target_id: "object.sofa.main",
    parameters: { intent: null }
  });
  assert.equal("intent" in command.parameters, true);
  assert.equal(command.parameters.intent, null);
});

test("buildSetLocksCommand copies only the supplied keys", () => {
  assert.deepEqual(
    buildSetLocksCommand("object.sofa.main", { existence: true }),
    {
      operation: "set_locks",
      target_id: "object.sofa.main",
      parameters: { locks: { existence: true } }
    }
  );
  const geometry = buildSetLocksCommand("object.sofa.main", { geometry: true });
  assert.deepEqual(geometry.parameters, { locks: { geometry: true } });
  assert.equal("existence" in geometry.parameters.locks, false);
  assert.equal("transform" in geometry.parameters.locks, false);
  assert.equal("material" in geometry.parameters.locks, false);

  // Explicit false values are real values, not omissions.
  assert.deepEqual(
    buildSetLocksCommand("object.sofa.main", { transform: false }).parameters,
    { locks: { transform: false } }
  );
});

test("buildSetLocksCommand supports all four lock keys and fresh objects", () => {
  const full = buildSetLocksCommand("object.sofa.main", {
    existence: true,
    transform: true,
    material: false,
    geometry: true
  });
  assert.deepEqual(full.parameters, {
    locks: { existence: true, transform: true, material: false, geometry: true }
  });
  // No leakage between calls.
  const next = buildSetLocksCommand("object.sofa.main", {});
  assert.deepEqual(next.parameters, { locks: {} });
  assert.notEqual(full.parameters.locks, next.parameters.locks);
});

test("intentActionBlocked: keep-intent blocks remove and replace", () => {
  const entity = { ...sofa, intent: "keep" };
  assert.equal(intentActionBlocked(entity, "remove"), true);
  assert.equal(intentActionBlocked(entity, "replace"), true);
});

test("intentActionBlocked: replace-intent blocks nothing", () => {
  const entity = { ...sofa, intent: "replace" };
  assert.equal(intentActionBlocked(entity, "remove"), false);
  assert.equal(intentActionBlocked(entity, "replace"), false);
});

test("intentActionBlocked: remove-intent blocks replace but not remove", () => {
  const entity = { ...sofa, intent: "remove" };
  assert.equal(intentActionBlocked(entity, "remove"), false);
  assert.equal(intentActionBlocked(entity, "replace"), true);
});

test("intentActionBlocked: no intent blocks nothing", () => {
  assert.equal(intentActionBlocked(sofa, "remove"), false);
  assert.equal(intentActionBlocked(sofa, "replace"), false);
});

test("intentActionBlocked: existence lock blocks both actions", () => {
  const entity = { ...sofa, locks: { existence: true } };
  assert.equal(intentActionBlocked(entity, "remove"), true);
  assert.equal(intentActionBlocked(entity, "replace"), true);
});

test("intentActionBlocked: geometry lock blocks both actions", () => {
  const entity = { ...sofa, locks: { geometry: true } };
  assert.equal(intentActionBlocked(entity, "remove"), true);
  assert.equal(intentActionBlocked(entity, "replace"), true);
});

test("intentActionBlocked: transform/material locks never block", () => {
  const transform = { ...sofa, locks: { transform: true } };
  assert.equal(intentActionBlocked(transform, "remove"), false);
  const material = { ...sofa, locks: { material: true } };
  assert.equal(intentActionBlocked(material, "replace"), false);
});

test("intentActionBlocked: structure state allows only keep (both actions blocked)", () => {
  const wall = { id: "wall.1", kind: "wall", state: "structure" };
  assert.equal(intentActionBlocked(wall, "remove"), true);
  assert.equal(intentActionBlocked(wall, "replace"), true);
  // Even an explicit remove-intent does not unlock a structural entity.
  const wallRemoving = { ...wall, intent: "remove" };
  assert.equal(intentActionBlocked(wallRemoving, "remove"), true);
});

test("intentActionBlocked tolerates null/undefined entities", () => {
  assert.equal(intentActionBlocked(null, "remove"), false);
  assert.equal(intentActionBlocked(undefined, "replace"), false);
});
