// Zero-dependency unit tests for the R8 designer-brief view-model helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/*.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/briefView.js (build/ is gitignored).
// briefView is pure: api.ts is imported as `import type` only (erased at
// emit), so the module loads under Node without a browser environment.

import test from "node:test";
import assert from "node:assert/strict";

import {
  briefEntityLabel,
  briefImageKey,
  briefPhotoLabel,
  briefPhotoMappingNote,
  briefRenderDisclaimer,
  briefRoomLabel,
  briefScaleLine,
  briefSectionIds,
  briefShortHash,
  collectBriefImages,
  createNotesPersister,
  downloadBriefPdf,
  filterIncludedImages,
  groupIntentEntities
} from "../build/briefView.js";

// ---------------------------------------------------------------------------
// Fixtures (wire shapes mirror the R8 BE contract; ids look like real ones so
// the no-raw-ids assertions below are meaningful).
// ---------------------------------------------------------------------------

const variant = {
  id: "var.1",
  title: "Вариант А",
  status: "approved",
  base_scene_revision_id: "rev.base000",
  head_scene_revision_id: "rev.head000",
  head_scene_revision_hash: "abcdef1234567890",
  head_scene_revision_created_at: "2026-02-01T10:00:00Z"
};

const baseBrief = (overrides = {}) => ({
  schema_version: "1.0",
  project: { id: "p.1", name: "Квартира на Ленинском", created_at: "2026-01-01T10:00:00Z" },
  variant,
  warnings: ["Бриф не является строительной документацией."],
  ...overrides
});

const briefWithImages = baseBrief({
  renders: [
    {
      id: "render.1",
      asset_id: "asset.render1",
      camera_id: "camera.1",
      stage: "draft",
      label: "Концепт, не фотография",
      created_at: "2026-02-01T11:00:00Z"
    },
    {
      id: "render.2",
      asset_id: "asset.render2",
      camera_id: "camera.1",
      stage: "final",
      label: "Концепт, не фотография",
      created_at: "2026-02-01T12:00:00Z"
    }
  ],
  photos: [
    { attachment_id: "att.1", asset_id: "asset.photo1", caption: "Гостиная днём" },
    { attachment_id: "att.2", asset_id: "asset.photo2", caption: null }
  ],
  style_direction: {
    source_text: "Тёплый минимализм, дерево",
    reference_asset_ids: ["asset.ref1", "asset.ref2"]
  }
});

// ---------------------------------------------------------------------------
// Privacy checklist: default states + inclusion filtering (#198).
// ---------------------------------------------------------------------------

test("#198: default checkbox states — renders checked, photos and style references unchecked", () => {
  const items = collectBriefImages(briefWithImages);
  // 2 renders + 2 photos + 2 style references = 6 checklist images.
  assert.equal(items.length, 6);
  assert.deepEqual(
    items.map((item) => [item.kind, item.checked]),
    [
      ["render", true],
      ["render", true],
      ["photo", false],
      ["photo", false],
      ["reference", false],
      ["reference", false]
    ]
  );
});

test("#198: checklist labels are numbered and id-free", () => {
  const items = collectBriefImages(briefWithImages);
  assert.deepEqual(
    items.map((item) => item.label),
    [
      "Рендер 1 · Черновик",
      "Рендер 2 · Финальный",
      // The owner caption wins when present (recognizability in the privacy
      // checklist); BE derives it from the asset's original name (#198).
      "Гостиная днём",
      "Фото 2",
      "Референс стиля 1",
      "Референс стиля 2"
    ]
  );
});

test("#198: checkbox filtering excludes unchecked images", () => {
  const items = collectBriefImages(briefWithImages);
  // Defaults: only renders included.
  assert.deepEqual(
    filterIncludedImages(items).map((item) => item.kind),
    ["render", "render"]
  );
  // The owner checks one photo and one reference, unchecks a render.
  const toggled = items.map((item) => ({
    ...item,
    checked:
      item.kind === "photo"
        ? item.assetId === "asset.photo2"
        : item.kind === "reference"
          ? item.assetId === "asset.ref2"
          : item.assetId !== "asset.render2"
  }));
  assert.deepEqual(
    filterIncludedImages(toggled).map((item) => item.assetId),
    ["asset.render1", "asset.photo2", "asset.ref2"]
  );
});

// ---------------------------------------------------------------------------
// Section presence: missing wire sections never render an empty header (#198).
// ---------------------------------------------------------------------------

test("#198: an empty brief produces no sections (no empty headers)", () => {
  const ids = briefSectionIds({
    brief: baseBrief(),
    includedKeys: new Set(),
    hasNeedsWishes: false,
    hasQuestions: false
  });
  assert.deepEqual(ids, []);
});

test("#198: only present wire sections appear, in print order", () => {
  const ids = briefSectionIds({
    brief: baseBrief({
      plan: {
        asset_id: "asset.plan1",
        scale: { status: "confirmed", source: "manual", label: "Масштаб задан вручную" }
      },
      rooms: [{ id: "room.1", name: "Гостиная" }],
      budget: { disclaimer: "Приблизительно", items: [{ id: "b.1", kind: "material", label: "Ламинат" }] }
    }),
    includedKeys: new Set(),
    hasNeedsWishes: true,
    hasQuestions: true
  });
  assert.deepEqual(ids, ["notes", "plan", "rooms", "budget", "questions"]);
});

test("#198: unchecked images remove their sections from the preview", () => {
  const items = collectBriefImages(briefWithImages);
  const noneIncluded = new Set();
  const idsNone = briefSectionIds({
    brief: briefWithImages,
    includedKeys: noneIncluded,
    hasNeedsWishes: false,
    hasQuestions: false
  });
  // style survives via source_text; renders/photos vanish entirely.
  assert.deepEqual(idsNone, ["style"]);

  const allIncluded = new Set(items.map((item) => item.key));
  const idsAll = briefSectionIds({
    brief: briefWithImages,
    includedKeys: allIncluded,
    hasNeedsWishes: false,
    hasQuestions: false
  });
  assert.deepEqual(idsAll, ["renders", "photos", "style"]);
});

test("#198: style section with references only stays hidden until one is included", () => {
  const brief = baseBrief({
    style_direction: { reference_asset_ids: ["asset.ref1"] }
  });
  const ids = briefSectionIds({
    brief,
    includedKeys: new Set(),
    hasNeedsWishes: false,
    hasQuestions: false
  });
  assert.deepEqual(ids, []);
  const idsIncluded = briefSectionIds({
    brief,
    includedKeys: new Set([briefImageKey("reference", 0, "asset.ref1")]),
    hasNeedsWishes: false,
    hasQuestions: false
  });
  assert.deepEqual(ids, []);
  assert.deepEqual(idsIncluded, ["style"]);
});

test("#198: budget with neither items nor disclaimer is omitted", () => {
  const ids = briefSectionIds({
    brief: baseBrief({ budget: { disclaimer: "", items: [] } }),
    includedKeys: new Set(),
    hasNeedsWishes: false,
    hasQuestions: false
  });
  assert.deepEqual(ids, []);
});

// ---------------------------------------------------------------------------
// Furniture intents: grouped, ordered, id-free labels (#188/#198).
// ---------------------------------------------------------------------------

test("#198: intent groups render in fixed order, empty groups omitted", () => {
  const groups = groupIntentEntities(
    {
      keep: [{ id: "entity.3", kind: "furniture", name: "Диван", room_id: "room.7" }],
      remove: [],
      replace: [{ id: "entity.9", kind: "furniture", name: null, room_id: "room.404" }],
      locked: [{ id: "entity.3", kind: "furniture", name: "Диван", room_id: "room.7" }]
    },
    [
      { id: "room.7", name: "Гостиная" },
      { id: "room.8", name: null }
    ]
  );
  assert.deepEqual(
    groups.map((group) => group.label),
    ["Сохранить", "Заменить", "Закреплено"]
  );
  assert.equal(groups[0].entities[0].label, "Диван");
  assert.equal(groups[0].entities[0].roomLabel, "Гостиная");
  // Unknown room id → no room label (never a raw id).
  assert.equal(groups[1].entities[0].roomLabel, null);
  // Locked overlaps keep (same entity listed in both groups).
  assert.deepEqual(groups[2].entities, [
    { id: "entity.3", label: "Диван", roomLabel: "Гостиная" }
  ]);
});

test("#198: unnamed entities and rooms fall back without raw ids", () => {
  assert.equal(briefEntityLabel({ kind: "furniture", name: null }), "мебель");
  assert.equal(briefEntityLabel({ kind: "wall", name: "  " }), "стена");
  assert.equal(briefRoomLabel({ name: null }), "Помещение");
  assert.equal(briefRoomLabel({ name: "  Спальня  " }), "Спальня");
  assert.equal(briefPhotoLabel(null, 2), "Фото 3");
  assert.equal(briefPhotoLabel("  ", 0), "Фото 1");
  assert.equal(briefPhotoLabel("Балкон", 0), "Балкон");
});

test("#198: no raw ids in any rendered view-model label (snapshot-ish)", () => {
  const brief = baseBrief({
    rooms: [
      { id: "room.7", name: "Гостиная" },
      { id: "room.8", name: null }
    ],
    furniture_intents: {
      keep: [{ id: "entity.3", kind: "furniture", name: "Диван", room_id: "room.7" }],
      locked: [{ id: "entity.9", kind: "floor", name: null }]
    },
    plan: {
      asset_id: "asset.plan1",
      scale: { status: "confirmed", source: "manual", label: "Масштаб задан вручную", mm_per_px: 5 }
    },
    ...briefWithImages
  });
  const images = collectBriefImages(brief);
  const groups = groupIntentEntities(brief.furniture_intents, brief.rooms);
  const visible = {
    imageLabels: images.map((item) => item.label),
    groupLabels: groups.map((group) => group.label),
    entityLabels: groups.flatMap((group) =>
      group.entities.map((entity) => ({ label: entity.label, room: entity.roomLabel }))
    ),
    scaleLine: briefScaleLine(brief)
  };
  const rendered = JSON.stringify(visible);
  for (const raw of ["room.7", "room.8", "entity.3", "entity.9", "asset.", "render.1", "att.", "var.1", "rev."]) {
    assert.ok(!rendered.includes(raw), `rendered labels must not contain ${raw}`);
  }
});

// ---------------------------------------------------------------------------
// Scale / provenance / disclaimers.
// ---------------------------------------------------------------------------

test("#198: scale line uses the BE label plus mm_per_px when known", () => {
  assert.equal(
    briefScaleLine(
      baseBrief({
        plan: {
          asset_id: "asset.plan1",
          scale: { status: "confirmed", source: "manual", label: "Масштаб задан вручную", mm_per_px: 5 }
        }
      })
    ),
    "Масштаб: Масштаб задан вручную · 5 мм на пиксель"
  );
  assert.equal(
    briefScaleLine(
      baseBrief({
        plan: { scale: { status: "unknown", source: "unknown", label: "Масштаб не задан" } }
      })
    ),
    "Масштаб: Масштаб не задан"
  );
  assert.equal(briefScaleLine(baseBrief()), null);
});

test("#198: short hash + render/photo disclaimers", () => {
  assert.equal(briefShortHash("abcdef1234567890"), "abcdef12");
  assert.equal(briefShortHash("abc"), "abc");
  assert.equal(briefShortHash(null), null);
  assert.equal(briefRenderDisclaimer("Концепт, не фотография"), "Концепт, не фотография");
  assert.equal(briefRenderDisclaimer(""), "Концепт, не фотография");
  assert.equal(briefRenderDisclaimer(null), "Концепт, не фотография");
  assert.equal(briefPhotoMappingNote({ confidence: "confirmed" }), "подтверждено владельцем");
  assert.equal(briefPhotoMappingNote({ confidence: "approx" }), "приблизительно");
  assert.equal(briefPhotoMappingNote({}), null);
  assert.equal(briefPhotoMappingNote(null), null);
});

// ---------------------------------------------------------------------------
// Notes persister: one debounced PATCH per burst (#198).
// ---------------------------------------------------------------------------

test("#198: rapid input produces a single debounced PATCH with the latest notes", async () => {
  const calls = [];
  const persister = createNotesPersister({
    patch: async (notes) => {
      calls.push(notes);
    },
    debounceMs: 20
  });
  persister.schedule({ needs_wishes: "а", questions_to_discuss: null });
  persister.schedule({ needs_wishes: "аб", questions_to_discuss: null });
  persister.schedule({ needs_wishes: "абв", questions_to_discuss: "q" });
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { needs_wishes: "абв", questions_to_discuss: "q" });
  persister.dispose();
});

test("#198: flush() persists pending notes immediately (blur/unmount path)", async () => {
  const calls = [];
  const persister = createNotesPersister({
    patch: async (notes) => {
      calls.push(notes);
    },
    debounceMs: 60_000
  });
  persister.schedule({ needs_wishes: "тёплый свет", questions_to_discuss: null });
  await persister.flush();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { needs_wishes: "тёплый свет", questions_to_discuss: null });
  // dispose() drops whatever is pending — no stray PATCH afterwards.
  persister.schedule({ needs_wishes: "не успел", questions_to_discuss: null });
  persister.dispose();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(calls.length, 1);
});

// ---------------------------------------------------------------------------
// M1 regression: the flush/dispose race. probe sequence — a PATCH is held in
// flight (controlled deferred), a second edit is scheduled behind it, then
// the page unmounts (flush awaited). Before the fix flush() no-oped past the
// in-flight PATCH, dispose() dropped the queued edit → 1 PATCH, data lost.
// ---------------------------------------------------------------------------

const deferredPatchFactory = () => {
  const calls = [];
  let callCount = 0;
  let releaseFirst;
  const firstGate = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  const persister = createNotesPersister({
    patch: async (notes) => {
      callCount += 1;
      calls.push(notes);
      if (callCount === 1) await firstGate; // hold PATCH #1 in flight
    },
    debounceMs: 5
  });
  return { persister, calls, releaseFirst };
};

test("#198 M1: flush() drains — edit queued behind an in-flight PATCH is delivered", async () => {
  const { persister, calls, releaseFirst } = deferredPatchFactory();
  persister.schedule({ needs_wishes: "первая", questions_to_discuss: null });
  await new Promise((resolve) => setTimeout(resolve, 30)); // debounce fires; PATCH #1 in flight
  assert.equal(calls.length, 1);
  persister.schedule({ needs_wishes: "вторая", questions_to_discuss: null }); // queued behind it

  // BriefPage cleanup order: flush (awaited) then dispose.
  const flushed = persister.flush();
  releaseFirst(); // PATCH #1 settles; the queued edit must go out
  await flushed;
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1], { needs_wishes: "вторая", questions_to_discuss: null });

  persister.dispose();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(calls.length, 2); // nothing extra after dispose
});

test("#198 M1: dispose() during an in-flight PATCH keeps the queued edit deliverable", async () => {
  const { persister, calls, releaseFirst } = deferredPatchFactory();
  persister.schedule({ needs_wishes: "первая", questions_to_discuss: null });
  await new Promise((resolve) => setTimeout(resolve, 30)); // PATCH #1 in flight
  persister.schedule({ needs_wishes: "вторая", questions_to_discuss: null }); // queued
  persister.dispose(); // unmount WITHOUT flush — the chain must still deliver
  releaseFirst();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1], { needs_wishes: "вторая", questions_to_discuss: null });
});

// ---------------------------------------------------------------------------
// Print trigger.
// ---------------------------------------------------------------------------

test("#198: downloadBriefPdf calls window.print (injected stub)", () => {
  let calls = 0;
  globalThis.window = { print: () => { calls += 1; } };
  try {
    downloadBriefPdf();
  } finally {
    delete globalThis.window;
  }
  assert.equal(calls, 1);
});
