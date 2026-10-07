// Zero-dependency unit tests for the attachments helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/attachments.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/attachments.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  attachmentLabel,
  buildAttachmentPayload,
  groupAttachmentsByTarget,
  isDanglingTarget,
  isTaskOverdue
} from "../build/attachments.js";

// Local noon on 2026-10-07 — localDateKey() must resolve to "2026-10-07" in
// every timezone, so overdue boundaries stay deterministic.
const TODAY = new Date(2026, 9, 7, 12, 0, 0);

function attachment(overrides = {}) {
  return {
    id: "att-1",
    project_id: "project-1",
    target_type: "project",
    target_id: null,
    kind: "note",
    asset_id: null,
    body: "тело",
    due_date: null,
    done: false,
    metadata: {},
    created_at: "2026-10-01T10:00:00Z",
    updated_at: "2026-10-01T10:00:00Z",
    ...overrides
  };
}

test("groupAttachmentsByTarget groups by target_type+target_id preserving order", () => {
  const groups = groupAttachmentsByTarget([
    attachment({ id: "1", target_type: "room", target_id: "room.1" }),
    attachment({ id: "2", target_type: "project", target_id: null }),
    attachment({ id: "3", target_type: "room", target_id: "room.1" }),
    attachment({ id: "4", target_type: "entity", target_id: "object.sofa.main" })
  ]);
  assert.equal(groups.size, 3);
  assert.deepEqual(
    groups.get("room:room.1").map((item) => item.id),
    ["1", "3"]
  );
  assert.deepEqual(
    groups.get("project:").map((item) => item.id),
    ["2"]
  );
  assert.deepEqual(
    groups.get("entity:object.sofa.main").map((item) => item.id),
    ["4"]
  );
});

test("isTaskOverdue: due today is NOT overdue, due before today is", () => {
  const dueToday = attachment({ kind: "task", due_date: "2026-10-07", done: false });
  const dueYesterday = attachment({ kind: "task", due_date: "2026-10-06", done: false });
  const dueTimestampToday = attachment({
    kind: "task",
    due_date: "2026-10-07T21:00:00Z",
    done: false
  });
  const dueTimestampYesterday = attachment({
    kind: "task",
    due_date: "2026-10-06T01:00:00Z",
    done: false
  });
  assert.equal(isTaskOverdue(dueToday, TODAY), false);
  assert.equal(isTaskOverdue(dueYesterday, TODAY), true);
  assert.equal(isTaskOverdue(dueTimestampToday, TODAY), false);
  assert.equal(isTaskOverdue(dueTimestampYesterday, TODAY), true);
});

// The runtime zone of this suite is unknown (UTC in CI, +N on a dev box), so
// the datetime assertions below are pinned in two ways: (a) zone-invariant
// bounds — instants whose local due date is on-or-after/before the "today"
// argument in EVERY timezone — and (b) a computed boundary that derives the
// expected verdict from the runtime's own local date, so the old
// lexicographic UTC-string compare (wrong east of GMT) can never pass.

/** Local YYYY-MM-DD key of an instant, computed with the runtime zone. */
function localKeyOf(instant) {
  return (
    `${instant.getFullYear()}-` +
    `${String(instant.getMonth() + 1).padStart(2, "0")}-` +
    `${String(instant.getDate()).padStart(2, "0")}`
  );
}

test("isTaskOverdue normalizes UTC datetimes to the local calendar date", () => {
  const due = "2026-10-06T23:00:00Z";
  // 23:00Z is local 2026-10-06 west of GMT and local 2026-10-07 east of it —
  // against local today 2026-10-06 it is due today-or-later everywhere,
  // therefore NOT overdue in every zone.
  assert.equal(
    isTaskOverdue(attachment({ kind: "task", due_date: due }), new Date(2026, 9, 6)),
    false
  );
  // Against local today 2026-10-08 the due day (2026-10-06/07 everywhere) is
  // strictly before today — overdue in every zone.
  assert.equal(
    isTaskOverdue(attachment({ kind: "task", due_date: due }), new Date(2026, 9, 8)),
    true
  );
  // Boundary (today = local 2026-10-07): the verdict must equal
  // "parsed local due date < today", not a lexicographic UTC-string compare.
  assert.equal(
    isTaskOverdue(attachment({ kind: "task", due_date: due }), new Date(2026, 9, 7)),
    localKeyOf(new Date(due)) < "2026-10-07"
  );
});

test("isTaskOverdue: offset due dates normalize too; garbage never marks overdue", () => {
  const dueOffset = "2026-10-06T23:00:00+05:30";
  // Same computed-boundary pinning as the UTC case, for numeric offsets.
  assert.equal(
    isTaskOverdue(attachment({ kind: "task", due_date: dueOffset }), new Date(2026, 9, 7)),
    localKeyOf(new Date(dueOffset)) < "2026-10-07"
  );
  // Zone-invariant: the offset instant is local 2026-10-06..07 everywhere —
  // not overdue against local 2026-10-06.
  assert.equal(
    isTaskOverdue(attachment({ kind: "task", due_date: dueOffset }), new Date(2026, 9, 6)),
    false
  );
  // Defensive: an unparseable datetime value must never flag a task overdue.
  assert.equal(
    isTaskOverdue(attachment({ kind: "task", due_date: "2026-13-99T99:99:99Z" }), TODAY),
    false
  );
});

test("isTaskOverdue ignores non-tasks, done tasks and missing due dates", () => {
  assert.equal(
    isTaskOverdue(attachment({ kind: "task", done: true, due_date: "2026-01-01" }), TODAY),
    false
  );
  assert.equal(
    isTaskOverdue(attachment({ kind: "note", due_date: "2026-01-01" }), TODAY),
    false
  );
  assert.equal(isTaskOverdue(attachment({ kind: "task", due_date: null }), TODAY), false);
  assert.equal(isTaskOverdue(attachment({ kind: "task", due_date: "" }), TODAY), false);
});

test("attachmentLabel is a compact kind + target label", () => {
  assert.equal(
    attachmentLabel(attachment({ kind: "note", target_type: "project", body: null })),
    "Заметка · проект"
  );
  assert.equal(
    attachmentLabel(attachment({ kind: "task", target_type: "room", target_id: "room.1", body: "Купить краску" })),
    "Задача · комната 1 — Купить краску"
  );
  assert.equal(
    attachmentLabel(attachment({ kind: "photo", target_type: "entity", target_id: "object.sofa.main", body: null })),
    "Фото · объект sofa.main"
  );
});

test("buildAttachmentPayload: photo/file require an assetId (typed error, no throw)", () => {
  const photo = buildAttachmentPayload({ targetType: "room", targetId: "room.1", kind: "photo" });
  assert.equal(photo.ok, false);
  assert.equal(typeof photo.error, "string");
  assert.equal(photo.error.length > 0, true);

  const file = buildAttachmentPayload({ targetType: "entity", targetId: "object.sofa.main", kind: "file" });
  assert.equal(file.ok, false);

  const photoWithAsset = buildAttachmentPayload({
    targetType: "room",
    targetId: "room.1",
    kind: "photo",
    assetId: "asset-1"
  });
  assert.equal(photoWithAsset.ok, true);
  assert.deepEqual(photoWithAsset.payload, {
    target_type: "room",
    target_id: "room.1",
    kind: "photo",
    body: null,
    asset_id: "asset-1",
    due_date: null,
    metadata: {}
  });
});

test("buildAttachmentPayload: note/task must not invent an assetId", () => {
  const note = buildAttachmentPayload({
    targetType: "project",
    targetId: null,
    kind: "note",
    body: "текст",
    assetId: "asset-1"
  });
  assert.equal(note.ok, false);
});

test("buildAttachmentPayload: total shape, trims body, nulls empty optionals", () => {
  const task = buildAttachmentPayload({
    targetType: "room",
    targetId: "room.2",
    kind: "task",
    body: "  Покрасить стены  ",
    dueDate: "2026-11-01"
  });
  assert.equal(task.ok, true);
  assert.deepEqual(task.payload, {
    target_type: "room",
    target_id: "room.2",
    kind: "task",
    body: "Покрасить стены",
    asset_id: null,
    due_date: "2026-11-01",
    metadata: {}
  });

  const note = buildAttachmentPayload({ targetType: "project", targetId: null, kind: "note", body: "  " });
  assert.equal(note.ok, true);
  assert.equal(note.payload.body, null);
  assert.equal(note.payload.target_id, null);
  assert.equal(note.payload.asset_id, null);
  assert.equal(note.payload.due_date, null);
});

test("buildAttachmentPayload: non-project target without targetId is an error", () => {
  const missing = buildAttachmentPayload({ targetType: "room", targetId: null, kind: "note", body: "x" });
  assert.equal(missing.ok, false);
});

test("isDanglingTarget flags room/entity targets missing from the scene", () => {
  const scene = { entities: [{ id: "room.1" }, { id: "object.sofa.main" }] };
  assert.equal(
    isDanglingTarget(attachment({ target_type: "entity", target_id: "object.sofa.main" }), scene),
    false
  );
  assert.equal(
    isDanglingTarget(attachment({ target_type: "entity", target_id: "object.gone.1" }), scene),
    true
  );
  assert.equal(isDanglingTarget(attachment({ target_type: "project", target_id: null }), scene), false);
  // No scene loaded → cannot verify → not dangling.
  assert.equal(
    isDanglingTarget(attachment({ target_type: "room", target_id: "room.9" }), null),
    false
  );
  // Malformed room/entity row without an id is dangling.
  assert.equal(isDanglingTarget(attachment({ target_type: "room", target_id: null }), scene), true);
});
