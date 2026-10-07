// Zero-dependency unit tests for the shared copy helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/copy.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/copy.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  DESIGN_CONFLICT_HINT,
  WORKER_OFFLINE_NOTICE,
  entityKindLabel,
  fileLabel,
  openingKindLabel,
  planAnalyzeQueueText,
  renderImageTag,
  rendererProfileLabel,
  statusLabel,
  styleAnalysisMaxMessage,
  styleAnalysisMinMessage
} from "../build/copy.js";

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

// ---------------------------------------------------------------------------
// #188 R5 glossary helpers.
// ---------------------------------------------------------------------------

test("#188: entity kinds map to RU labels, unknown kinds pass through", () => {
  assert.equal(entityKindLabel("wall"), "стена");
  assert.equal(entityKindLabel("floor"), "пол");
  assert.equal(entityKindLabel("room"), "комната");
  assert.equal(entityKindLabel("furniture"), "мебель");
  assert.equal(entityKindLabel("opening"), "проём");
  // Wire values stay English; unknown/future kinds are never hidden.
  assert.equal(entityKindLabel("column"), "column");
});

test("#188: opening kinds map to RU labels, unknown kinds pass through", () => {
  assert.equal(openingKindLabel("door"), "дверь");
  assert.equal(openingKindLabel("window"), "окно");
  assert.equal(openingKindLabel("arch"), "арка");
  assert.equal(openingKindLabel("doorway"), "doorway");
});

test("#188: renderer profile shows «Фоторендер», unknown passes through", () => {
  assert.equal(rendererProfileLabel("blender-cycles-v0"), "Фоторендер");
  assert.equal(rendererProfileLabel("future-profile"), "future-profile");
  assert.equal(rendererProfileLabel(null), "");
  assert.equal(rendererProfileLabel(undefined), "");
});

test("#188: render pass tag never mentions rgb", () => {
  assert.equal(renderImageTag(true), "изображение");
  assert.equal(renderImageTag(false), "нет изображения");
});

test("#188: nameless files never surface raw ids in pickers", () => {
  assert.equal(fileLabel("plan.pdf"), "plan.pdf");
  assert.equal(fileLabel(null), "Файл без названия");
  assert.equal(fileLabel(undefined), "Файл без названия");
  assert.equal(fileLabel("   "), "Файл без названия");
});

test("#188: 409 conflict hint explains without the raw code", () => {
  assert.ok(DESIGN_CONFLICT_HINT.includes("не применилось"));
  assert.ok(DESIGN_CONFLICT_HINT.includes("проверку дизайна"));
  assert.ok(!DESIGN_CONFLICT_HINT.includes("409"));
});

test("#188: worker notice says «очередь обработки», never GPU", () => {
  assert.ok(WORKER_OFFLINE_NOTICE.includes("очередь обработки"));
  assert.ok(WORKER_OFFLINE_NOTICE.includes("будут ждать"));
  assert.ok(!WORKER_OFFLINE_NOTICE.includes("GPU"));
});

test("#188: analyze queue line uses queue wording with the status label", () => {
  assert.equal(
    planAnalyzeQueueText("running"),
    "выполняется · план разбирается в очереди обработки, обычно 1–4 минуты (лимит 15)"
  );
  assert.ok(!planAnalyzeQueueText("queued").includes("GPU"));
});

test("#188: style-analysis limits reference the «референс» role", () => {
  assert.equal(
    styleAnalysisMinMessage(2),
    "Для анализа стиля нужно минимум 3 изображения-референса (роль «референс»). Сейчас загружено: 2."
  );
  assert.equal(
    styleAnalysisMaxMessage(6),
    "Для анализа стиля можно использовать максимум 5 изображений-референсов. Сейчас выбрано: 6."
  );
});
