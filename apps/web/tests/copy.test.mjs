// Zero-dependency unit tests for the shared copy helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/copy.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/copy.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import { statusLabel } from "../build/copy.js";

test("queued maps to «в очереди»", () => {
  assert.equal(statusLabel("queued"), "в очереди");
});

test("pending maps to «ожидает»", () => {
  assert.equal(statusLabel("pending"), "ожидает");
});

test("running and leased map to «выполняется»", () => {
  assert.equal(statusLabel("running"), "выполняется");
  assert.equal(statusLabel("leased"), "выполняется");
});

test("succeeded maps to «выполнено»", () => {
  assert.equal(statusLabel("succeeded"), "выполнено");
});

test("failed maps to «ошибка»", () => {
  assert.equal(statusLabel("failed"), "ошибка");
});

test("cancelled maps to «отменено»", () => {
  assert.equal(statusLabel("cancelled"), "отменено");
});

test("unknown and empty statuses pass through raw", () => {
  assert.equal(statusLabel("retrying"), "retrying");
  assert.equal(statusLabel(""), "");
});
