// Zero-dependency unit tests for the R6 guided setup model (#183).
//
// Node 22 has no native TypeScript support, so the source is compiled first
// (part of the 19-module tsc ritual):
//   apps/web/node_modules/.bin/tsc src/... src/setup.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/setup.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildSetupCopy,
  classifyAssetRole,
  ctaFromSetup,
  resolveSetupSteps,
  SETUP_STEP_LABELS,
  SETUP_STEP_ORDER,
  setupStepLinkLabel,
  setupStepPage
} from "../build/setup.js";

// ---------------------------------------------------------------------------
// Fixtures mirroring the backend flag matrix (R6 contract). Every fixture is a
// total SetupStatus-shaped record; overrides patch the interesting bits.
// ---------------------------------------------------------------------------

const FLAGS = {
  plan_uploaded: false,
  scale_known: false,
  geometry_draft: false,
  geometry_confirmed: false,
  room_labels: false,
  photos_added: false,
  photo_mapping: false,
  ready_for_design: false
};

const DIAGNOSTICS = {
  plan_asset_count: 0,
  legacy_apartment_asset_count: 0,
  room_count: 0,
  room_photo_attachment_count: 0,
  dangling_room_photo_attachment_count: 0
};

function status(overrides = {}) {
  const {
    current_step = { id: "upload_plan", number: 1, label: "Загрузить план" },
    what_stroy_knows = [],
    must_confirm = [],
    next_action = {
      id: "upload_plan",
      label: "Загрузить план квартиры",
      page: "plan",
      primary: true,
      disabled: false,
      reason: null
    },
    flags = {},
    diagnostics = {},
    ...rest
  } = overrides;
  return {
    project_id: "project-1",
    flags: { ...FLAGS, ...flags },
    current_step,
    what_stroy_knows,
    must_confirm,
    next_action,
    diagnostics: { ...DIAGNOSTICS, ...diagnostics },
    ...rest
  };
}

const stateOf = (steps, id) => {
  const step = steps.find((item) => item.id === id);
  assert.ok(step, `step "${id}" is present`);
  return step.state;
};

// ---------------------------------------------------------------------------
// Checklist resolver: the BE flag matrix cases
// ---------------------------------------------------------------------------

test("empty project: upload_plan is current, everything else locked", () => {
  const steps = resolveSetupSteps(status());
  assert.deepEqual(steps.map((step) => step.id), SETUP_STEP_ORDER);
  assert.deepEqual(
    steps.map((step) => step.state),
    ["current", "locked", "locked", "locked", "locked", "locked"]
  );
  assert.equal(steps[0].label, SETUP_STEP_LABELS.upload_plan);
  assert.equal(steps[0].number, 1);
});

test("legacy apartment asset: plan_uploaded flag done, scale step current", () => {
  const steps = resolveSetupSteps(
    status({
      flags: { plan_uploaded: true, scale_known: true },
      diagnostics: { legacy_apartment_asset_count: 1 },
      current_step: { id: "check_rooms_scale", number: 2, label: "Проверить комнаты и размеры" }
    })
  );
  assert.equal(stateOf(steps, "upload_plan"), "done");
  assert.equal(stateOf(steps, "check_rooms_scale"), "current");
  assert.equal(stateOf(steps, "create_3d"), "locked");
});

test("plan role asset: same flag matrix as legacy — upload done, check current", () => {
  const steps = resolveSetupSteps(
    status({
      flags: { plan_uploaded: true, scale_known: true },
      diagnostics: { plan_asset_count: 1 },
      current_step: { id: "check_rooms_scale", number: 2, label: "Проверить комнаты и размеры" }
    })
  );
  assert.equal(stateOf(steps, "upload_plan"), "done");
  assert.equal(stateOf(steps, "check_rooms_scale"), "current");
});

test("scale known but geometry draft only: check step stays current, 3D locked", () => {
  const steps = resolveSetupSteps(
    status({
      flags: { plan_uploaded: true, scale_known: true, geometry_draft: true },
      diagnostics: { plan_asset_count: 1 },
      current_step: { id: "check_rooms_scale", number: 2, label: "Проверить комнаты и размеры" }
    })
  );
  assert.equal(stateOf(steps, "upload_plan"), "done");
  assert.equal(stateOf(steps, "check_rooms_scale"), "current");
  assert.equal(stateOf(steps, "create_3d"), "locked");
  assert.equal(stateOf(steps, "add_photos"), "locked");
});

test("geometry_draft without commit is NOT confirmed: step 2 not done by flag alone", () => {
  const steps = resolveSetupSteps(
    status({ flags: { geometry_draft: true }, current_step: { id: "check_rooms_scale", number: 2, label: "x" } })
  );
  assert.equal(stateOf(steps, "check_rooms_scale"), "current");
});

test("committed: geometry confirmed — first three done, photos current", () => {
  const steps = resolveSetupSteps(
    status({
      flags: { plan_uploaded: true, scale_known: true, geometry_draft: true, geometry_confirmed: true },
      diagnostics: { plan_asset_count: 1, room_count: 3 },
      current_step: { id: "add_photos", number: 4, label: "Добавить реальные фото" }
    })
  );
  assert.deepEqual(
    steps.map((step) => step.state),
    ["done", "done", "done", "current", "locked", "locked"]
  );
});

test("demo scene without confirmed geometry: no plan step done, 3D locked", () => {
  // A demo scene exists (room_count > 0) but the plan flow never confirmed
  // geometry — the checklist must NOT treat create_3d as done, and plan
  // upload stays current (room_count alone proves nothing about the flow).
  const steps = resolveSetupSteps(
    status({
      flags: {},
      diagnostics: { room_count: 5 },
      current_step: { id: "upload_plan", number: 1, label: "Загрузить план" }
    })
  );
  assert.equal(stateOf(steps, "upload_plan"), "current");
  assert.equal(stateOf(steps, "check_rooms_scale"), "locked");
  assert.equal(stateOf(steps, "create_3d"), "locked");
});

test("room labels present with draft + scene rooms: photos step current, mapping locked", () => {
  const steps = resolveSetupSteps(
    status({
      flags: {
        plan_uploaded: true,
        scale_known: true,
        geometry_draft: true,
        geometry_confirmed: true,
        room_labels: true
      },
      diagnostics: { plan_asset_count: 1, room_count: 3 },
      current_step: { id: "add_photos", number: 4, label: "Добавить реальные фото" }
    })
  );
  assert.equal(stateOf(steps, "add_photos"), "current");
  assert.equal(stateOf(steps, "map_photos"), "locked");
  assert.equal(stateOf(steps, "ready"), "locked");
});

test("mapping set: mapping done, ready step current", () => {
  const steps = resolveSetupSteps(
    status({
      flags: {
        plan_uploaded: true,
        scale_known: true,
        geometry_draft: true,
        geometry_confirmed: true,
        room_labels: true,
        photos_added: true,
        photo_mapping: true
      },
      diagnostics: { plan_asset_count: 1, room_count: 3, room_photo_attachment_count: 2 },
      current_step: { id: "ready", number: 6, label: "Готово — перейти к дизайну" }
    })
  );
  assert.deepEqual(
    steps.map((step) => step.state),
    ["done", "done", "done", "done", "done", "current"]
  );
});

test("dangling room photo attachments are NOT counted as done anywhere", () => {
  const steps = resolveSetupSteps(
    status({
      flags: { photos_added: true },
      diagnostics: { room_photo_attachment_count: 2, dangling_room_photo_attachment_count: 2 },
      current_step: { id: "add_photos", number: 4, label: "Добавить реальные фото" }
    })
  );
  // photos_added stays honest, but mapping/ready flags are untouched.
  assert.equal(stateOf(steps, "add_photos"), "current");
  assert.equal(stateOf(steps, "map_photos"), "locked");
  assert.equal(stateOf(steps, "ready"), "locked");
});

test("out-of-order work: photos uploaded before 3D still shows that step done", () => {
  const steps = resolveSetupSteps(
    status({
      flags: { photos_added: true },
      current_step: { id: "create_3d", number: 3, label: "Создать 3D" }
    })
  );
  assert.equal(stateOf(steps, "add_photos"), "done");
  assert.equal(stateOf(steps, "create_3d"), "current");
});

test("ready_for_design: everything before done, ready current", () => {
  const steps = resolveSetupSteps(
    status({
      flags: {
        plan_uploaded: true,
        scale_known: true,
        geometry_draft: true,
        geometry_confirmed: true,
        room_labels: true,
        photos_added: true,
        photo_mapping: true,
        ready_for_design: true
      },
      current_step: { id: "ready", number: 6, label: "Готово — перейти к дизайну" }
    })
  );
  assert.deepEqual(
    steps.map((step) => step.state),
    ["done", "done", "done", "done", "done", "current"]
  );
});

test("defensive: missing/unknown current_step falls back to step 1 without throwing", () => {
  const nullStep = status({ current_step: null });
  assert.equal(stateOf(resolveSetupSteps(nullStep), "upload_plan"), "current");
  const weird = status({ current_step: { id: "mystery_step", number: 99, label: "?" } });
  const steps = resolveSetupSteps(weird);
  // Unknown id → only the number matters: earlier steps done by number/flag.
  assert.ok(steps.every((step) => ["done", "current", "locked"].includes(step.state)));
});

// ---------------------------------------------------------------------------
// classifyAssetRole
// ---------------------------------------------------------------------------

test("classifyAssetRole: plan/photo are the new roles, apartment is legacy, rest other", () => {
  assert.equal(classifyAssetRole("plan"), "plan");
  assert.equal(classifyAssetRole("photo"), "photo");
  assert.equal(classifyAssetRole("apartment"), "legacy_apartment");
  assert.equal(classifyAssetRole("reference"), "other");
  assert.equal(classifyAssetRole("derived"), "other");
  assert.equal(classifyAssetRole("attachment"), "other");
  assert.equal(classifyAssetRole("something_new"), "other");
  assert.equal(classifyAssetRole(""), "other");
});

// ---------------------------------------------------------------------------
// CTA priority: setup next_action wins while not ready; ready hands off
// ---------------------------------------------------------------------------

test("not ready: the setup next_action IS the primary CTA, verbatim", () => {
  const cta = ctaFromSetup(
    status({
      next_action: {
        id: "set_scale",
        label: "Задать масштаб плана",
        page: "plan",
        primary: true,
        disabled: false,
        reason: null
      }
    })
  );
  assert.deepEqual(cta, {
    label: "Задать масштаб плана",
    page: "plan",
    disabled: false,
    reason: null
  });
});

test("disabled next_action: CTA stays disabled and carries the reason", () => {
  const cta = ctaFromSetup(
    status({
      next_action: {
        id: "commit_plan",
        label: "Создать 3D",
        page: "plan",
        primary: true,
        disabled: true,
        reason: "Сначала проверьте размеры комнат."
      }
    })
  );
  assert.equal(cta.disabled, true);
  assert.equal(cta.label, "Создать 3D");
  assert.equal(cta.reason, "Сначала проверьте размеры комнат.");
});

test("ready_for_design: CTA becomes the design hand-off", () => {
  const cta = ctaFromSetup(
    status({
      flags: { ready_for_design: true },
      current_step: { id: "ready", number: 6, label: "Готово — перейти к дизайну" }
    })
  );
  assert.deepEqual(cta, {
    label: "Перейти к дизайну",
    page: "design",
    disabled: false,
    reason: null
  });
});

test("defensive: missing next_action while not ready → disabled CTA with reason", () => {
  const broken = status();
  delete broken.next_action;
  const cta = ctaFromSetup(broken);
  assert.equal(cta.disabled, true);
  assert.ok(cta.reason && cta.reason.length > 0);
});

// ---------------------------------------------------------------------------
// buildSetupCopy
// ---------------------------------------------------------------------------

test("buildSetupCopy passes backend strings through", () => {
  const copy = buildSetupCopy(
    status({
      what_stroy_knows: ["План загружен."],
      must_confirm: ["Проверьте масштаб."]
    })
  );
  assert.deepEqual(copy.known, ["План загружен."]);
  assert.deepEqual(copy.confirm, ["Проверьте масштаб."]);
});

test("buildSetupCopy derives fallback bullets from flags when backend lists are empty", () => {
  const copy = buildSetupCopy(
    status({
      flags: { plan_uploaded: true, scale_known: true, geometry_confirmed: true },
      diagnostics: { plan_asset_count: 1 }
    })
  );
  assert.ok(copy.known.some((line) => line.includes("План")));
  assert.ok(copy.known.some((line) => line.includes("Масштаб")));
  assert.deepEqual(copy.confirm, []);
});

test("buildSetupCopy: nothing known → explicit starter line, never an empty bullet list", () => {
  const copy = buildSetupCopy(status());
  assert.equal(copy.known.length, 1);
  assert.ok(copy.known[0].includes("плана"));
});

test("buildSetupCopy: dangling photo attachments surface in must_confirm fallback", () => {
  const copy = buildSetupCopy(
    status({ diagnostics: { dangling_room_photo_attachment_count: 2 } })
  );
  assert.equal(copy.confirm.length, 1);
  assert.ok(copy.confirm[0].includes("комнат"));
});

// ---------------------------------------------------------------------------
// Step meta helpers
// ---------------------------------------------------------------------------

test("setupStepPage: plan work on plan, photos/ready on design; link labels match", () => {
  assert.equal(setupStepPage("upload_plan"), "plan");
  assert.equal(setupStepPage("check_rooms_scale"), "plan");
  assert.equal(setupStepPage("create_3d"), "plan");
  assert.equal(setupStepPage("add_photos"), "design");
  assert.equal(setupStepPage("map_photos"), "design");
  assert.equal(setupStepPage("ready"), "design");
  assert.equal(setupStepLinkLabel("create_3d"), "Изменить");
  assert.equal(setupStepLinkLabel("add_photos"), "Посмотреть");
});

test("step labels are the six contract labels, in order", () => {
  const steps = resolveSetupSteps(status());
  assert.deepEqual(
    steps.map((step) => step.label),
    [
      "Загрузить план",
      "Проверить комнаты и размеры",
      "Создать 3D",
      "Добавить реальные фото",
      "Привязать фото к комнатам",
      "Готово — перейти к дизайну"
    ]
  );
});
