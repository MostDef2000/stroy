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
  BRIEF_NOT_BUILDING_DOC,
  BRIEF_PRIVACY_NOTE,
  BRIEF_SHARE_HINT,
  briefBannerLines,
  briefIntentGroupLabel,
  briefNotesSaveIndicator,
  briefScaleLabel,
  briefSectionTitle,
  DESIGN_CONFLICT_HINT,
  WORKER_OFFLINE_NOTICE,
  entityKindLabel,
  fileLabel,
  mappingConfidenceLabel,
  openingKindLabel,
  planAnalyzeQueueText,
  renderStageLabel,
  roomHeading,
  saveIndicator,
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

// ---------------------------------------------------------------------------
// R7 additions (#184/#187 + Q3/Q4 quick-wins).
// ---------------------------------------------------------------------------

test("roomHeading prefers the room name, falls back to the id", () => {
  assert.equal(roomHeading({ id: "room-1", name: "Спальня" }), "Спальня");
  assert.equal(roomHeading({ id: "room-1", name: null }), "Комната room-1");
  assert.equal(roomHeading({ id: "room-1" }), "Комната room-1");
  assert.equal(roomHeading({ id: "room-1", name: "   " }), "Комната room-1");
});

test("saveIndicator precedence: saving > dirty > committed > saved draft > default", () => {
  assert.equal(saveIndicator({ saving: true, dirty: true, draftStatus: "committed" }), "Сохранение…");
  assert.equal(saveIndicator({ saving: false, dirty: true, draftStatus: "committed" }), "Есть несохранённые правки");
  assert.equal(saveIndicator({ saving: false, dirty: false, draftStatus: "committed" }), "3D-сцена создана");
  assert.equal(saveIndicator({ saving: false, dirty: false, draftStatus: "edited" }), "План сохранён");
  assert.equal(saveIndicator({ saving: false, dirty: false, draftStatus: null }), "Есть несохранённые правки");
});

test("renderStageLabel: draft → «Черновик», everything else → «Финальный»", () => {
  assert.equal(renderStageLabel("draft"), "Черновик");
  assert.equal(renderStageLabel("final"), "Финальный");
  assert.equal(renderStageLabel(null), "Финальный");
  assert.equal(renderStageLabel(undefined), "Финальный");
});

test("mappingConfidenceLabel: wire values → badge labels", () => {
  assert.equal(mappingConfidenceLabel("approx"), "приблизительно");
  assert.equal(mappingConfidenceLabel("confirmed"), "подтверждено владельцем");
  assert.equal(mappingConfidenceLabel("calibrated"), "откалибровано");
  assert.equal(mappingConfidenceLabel(null), "приблизительно");
  assert.equal(mappingConfidenceLabel("garbage"), "приблизительно");
});

// ---------------------------------------------------------------------------
// R8 designer brief (#198): labels for the brief document.
// ---------------------------------------------------------------------------

test("#198: intent groups map to Сохранить/Убрать/Заменить/Закреплено", () => {
  assert.equal(briefIntentGroupLabel("keep"), "Сохранить");
  assert.equal(briefIntentGroupLabel("remove"), "Убрать");
  assert.equal(briefIntentGroupLabel("replace"), "Заменить");
  assert.equal(briefIntentGroupLabel("locked"), "Закреплено");
  // Unknown/future group keys pass through (never hidden).
  assert.equal(briefIntentGroupLabel("repaint"), "repaint");
});

test("#198: section titles cover every brief section id", () => {
  assert.equal(briefSectionTitle("notes"), "Потребности и пожелания");
  assert.equal(briefSectionTitle("plan"), "План квартиры");
  assert.equal(briefSectionTitle("rooms"), "Комнаты");
  assert.equal(briefSectionTitle("furniture"), "Мебель и объекты");
  assert.equal(briefSectionTitle("renders"), "Рендеры");
  assert.equal(briefSectionTitle("photos"), "Фотографии квартиры");
  assert.equal(briefSectionTitle("style"), "Стилевое решение");
  assert.equal(briefSectionTitle("budget"), "Смета");
  assert.equal(briefSectionTitle("questions"), "Вопросы для обсуждения");
});

test("#198: scale line — BE label wins, fallbacks only for a blank label", () => {
  assert.equal(
    briefScaleLabel({ status: "confirmed", source: "manual", label: "Масштаб задан вручную" }),
    "Масштаб задан вручную"
  );
  assert.equal(briefScaleLabel({ status: "confirmed", label: "" }), "Масштаб подтверждён");
  assert.equal(briefScaleLabel({ status: "approximate", label: null }), "Масштаб приблизительный");
  assert.equal(briefScaleLabel({ status: "unknown", label: undefined }), "Масштаб неизвестен");
  assert.equal(briefScaleLabel(null), "Масштаб неизвестен");
});

test("#198: warnings render verbatim; an empty list falls back to the disclaimer", () => {
  const warnings = [
    "Бриф не является строительной или рабочей документацией; детали требуют проверки специалистом.",
    "Сгенерированные изображения — концепты, а не фотографии."
  ];
  assert.deepEqual(briefBannerLines(warnings), warnings);
  assert.deepEqual(briefBannerLines([]), [BRIEF_NOT_BUILDING_DOC]);
  assert.equal(BRIEF_NOT_BUILDING_DOC, "Бриф не является строительной или рабочей документацией.");
});

test("#198: notes save indicator — saving > dirty > saved", () => {
  assert.equal(briefNotesSaveIndicator({ saving: true, dirty: true }), "Сохранение…");
  assert.equal(briefNotesSaveIndicator({ saving: false, dirty: true }), "Есть несохранённые правки");
  assert.equal(briefNotesSaveIndicator({ saving: false, dirty: false }), "Сохранено");
});

test("#198: privacy note and share hint carry the owner-facing wording", () => {
  assert.equal(
    BRIEF_PRIVACY_NOTE,
    "Референсы стиля скрыты по умолчанию: изображения принадлежат их авторам. " +
      "Отметьте те, что можно показать дизайнеру."
  );
  assert.equal(BRIEF_SHARE_HINT, "Сначала выберите или утвердите вариант");
});
