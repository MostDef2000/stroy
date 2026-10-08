// R6 guided setup (#183): the owner-facing model for the project setup
// status served by GET /api/v1/projects/{project_id}/setup. Pure and total:
// every function returns a value, nothing throws, no React and no network —
// the module compiles with a bare `tsc` and is unit-tested in isolation
// (tests/setup.test.mjs), same ritual as copy.ts / overview.ts.
//
// The wire shape is the approved R6 contract (mirrored verbatim in api.ts
// re-exports; the backend owns the flag matrix — this module only reads it).

/** Stable setup step ids (contract: current_step.id). */
export type SetupStepId =
  | "upload_plan"
  | "check_rooms_scale"
  | "create_3d"
  | "add_photos"
  | "map_photos"
  | "ready";

/** Setup flags as reported by the backend (R6 wire contract). */
export type SetupFlags = {
  plan_uploaded: boolean;
  scale_known: boolean;
  geometry_draft: boolean;
  geometry_confirmed: boolean;
  room_labels: boolean;
  photos_added: boolean;
  photo_mapping: boolean;
  ready_for_design: boolean;
};

/** One step of the guided setup as carried on the wire (current_step). */
export type SetupStep = {
  id: string;
  number: number;
  label: string;
};

/** The single next action suggested by the backend (next_action). */
export type SetupAction = {
  id: string;
  label: string;
  page: "plan" | "design";
  primary: boolean;
  disabled: boolean;
  reason: string | null;
};

/** Server-side counters backing the setup decision (diagnostics). */
export type SetupDiagnostics = {
  plan_asset_count: number;
  legacy_apartment_asset_count: number;
  room_count: number;
  room_photo_attachment_count: number;
  dangling_room_photo_attachment_count: number;
};

/** GET /api/v1/projects/{project_id}/setup response (R6 wire contract). */
export type SetupStatus = {
  project_id: string;
  flags: SetupFlags;
  current_step: SetupStep;
  what_stroy_knows: string[];
  must_confirm: string[];
  next_action: SetupAction;
  diagnostics: SetupDiagnostics;
};

// ---------------------------------------------------------------------------
// Six-step checklist. Labels are the owner-facing RU copy from the R6
// contract; ids stay English wire values (#188: identifiers never reach the
// default UI, only these labels do).
// ---------------------------------------------------------------------------

export const SETUP_STEP_ORDER: SetupStepId[] = [
  "upload_plan",
  "check_rooms_scale",
  "create_3d",
  "add_photos",
  "map_photos",
  "ready"
];

export const SETUP_STEP_LABELS: Record<SetupStepId, string> = {
  upload_plan: "Загрузить план",
  check_rooms_scale: "Проверить комнаты и размеры",
  create_3d: "Создать 3D",
  add_photos: "Добавить реальные фото",
  map_photos: "Привязать фото к комнатам",
  ready: "Готово — перейти к дизайну"
};

/** Per-step state for the overview checklist. */
export type SetupStepState = "done" | "current" | "locked";

export type SetupStepView = {
  id: SetupStepId;
  number: number;
  label: string;
  state: SetupStepState;
};

/**
 * Which setup flag proves a step's fact on its own. The backend sequences the
 * steps via current_step; the flag is the honest cross-check for work done
 * out of order (e.g. photos uploaded before the 3D was created).
 */
const STEP_FLAG: Record<SetupStepId, (flags: SetupFlags) => boolean> = {
  upload_plan: (flags) => flags.plan_uploaded,
  check_rooms_scale: (flags) => flags.geometry_confirmed,
  create_3d: (flags) => flags.geometry_confirmed,
  add_photos: (flags) => flags.photos_added,
  map_photos: (flags) => flags.photo_mapping,
  ready: (flags) => flags.ready_for_design
};

/**
 * The six checklist steps with their state derived from the status:
 * a step before current_step is done, current_step itself is current, a later
 * step is locked — unless its own flag already proves the fact (done).
 * Defensive: a missing or unknown current_step falls back to step 1, so the
 * checklist always renders a total, sane state.
 */
export function resolveSetupSteps(status: SetupStatus): SetupStepView[] {
  const current = status.current_step;
  const currentId =
    current && typeof current.id === "string" && current.id.length > 0
      ? current.id
      : "upload_plan";
  const currentNumber =
    current && typeof current.number === "number" && Number.isFinite(current.number)
      ? current.number
      : 1;
  return SETUP_STEP_ORDER.map((id, index) => {
    const number = index + 1;
    let state: SetupStepState;
    if (currentId === id) {
      state = "current";
    } else if (number < currentNumber) {
      state = "done";
    } else if (STEP_FLAG[id](status.flags)) {
      state = "done";
    } else {
      state = "locked";
    }
    return { id, number, label: SETUP_STEP_LABELS[id], state };
  });
}

/**
 * Navigation target of a checklist step: plan work (steps 1–3) lives on the
 * plan page, photos (steps 4–5) and the ready hand-off live on the design
 * page. Used for the quiet «Изменить»/«Посмотреть» links on done steps.
 */
export function setupStepPage(stepId: SetupStepId): "plan" | "design" {
  return stepId === "upload_plan" || stepId === "check_rooms_scale" || stepId === "create_3d"
    ? "plan"
    : "design";
}

/** Quiet link label for a done checklist step (#188 RU copy). */
export function setupStepLinkLabel(stepId: SetupStepId): string {
  return setupStepPage(stepId) === "plan" ? "Изменить" : "Посмотреть";
}

/**
 * The two bullet lists under the checklist. Backend strings (RU per contract)
 * pass through; when a list arrives empty the copy is derived from the flags
 * so the section never renders a bare heading.
 */
export function buildSetupCopy(status: SetupStatus): { known: string[]; confirm: string[] } {
  const { flags, diagnostics } = status;

  const derivedKnown: string[] = [];
  if (flags.plan_uploaded) derivedKnown.push("План квартиры загружен.");
  if (flags.scale_known) derivedKnown.push("Масштаб плана известен.");
  if (flags.geometry_draft) derivedKnown.push("Черновик геометрии комнат построен.");
  if (flags.geometry_confirmed) derivedKnown.push("Геометрия комнат подтверждена.");
  if (flags.room_labels) derivedKnown.push("Комнаты названы.");
  if (flags.photos_added) derivedKnown.push("Реальные фото добавлены.");
  if (flags.photo_mapping) derivedKnown.push("Фото привязаны к комнатам.");
  const known =
    status.what_stroy_knows.length > 0
      ? [...status.what_stroy_knows]
      : derivedKnown.length > 0
        ? derivedKnown
        : ["Пока ничего не известно — начните с загрузки плана."];

  const derivedConfirm: string[] = [];
  if (diagnostics.dangling_room_photo_attachment_count > 0) {
    derivedConfirm.push("Проверьте фото, оставшиеся без комнаты после правок плана.");
  }
  if (flags.geometry_draft && !flags.geometry_confirmed) {
    derivedConfirm.push("Проверьте геометрию комнат и размеры на плане.");
  }
  const confirm =
    status.must_confirm.length > 0 ? [...status.must_confirm] : derivedConfirm;

  return { known, confirm };
}

/** Asset roles the R6 upload paths use (plus the legacy role). */
export type ClassifiedAssetRole = "plan" | "photo" | "legacy_apartment" | "other";

/**
 * Classify an asset role for the R6 flow: "plan" and "photo" are the new
 * upload roles, "apartment" stays legacy-accepted (old uploads keep working),
 * everything else (reference, derived, attachment, …) is "other".
 */
export function classifyAssetRole(role: string): ClassifiedAssetRole {
  switch (role) {
    case "plan":
      return "plan";
    case "photo":
      return "photo";
    case "apartment":
      return "legacy_apartment";
    default:
      return "other";
  }
}

/** The single primary CTA derived from the setup status. */
export type SetupCta = {
  label: string;
  page: "plan" | "design";
  disabled: boolean;
  reason: string | null;
};

/**
 * Single primary CTA (#188 §2): while the project is not ready for design the
 * setup next_action IS the primary CTA — its label, target page, disabled
 * state and reason are respected verbatim. Once ready_for_design flips, the
 * CTA becomes the hand-off to the design page.
 */
export function ctaFromSetup(status: SetupStatus): SetupCta {
  if (status.flags.ready_for_design) {
    return { label: "Перейти к дизайну", page: "design", disabled: false, reason: null };
  }
  const action = status.next_action;
  if (!action || typeof action.label !== "string" || action.label.length === 0) {
    return {
      label: "Продолжить настройку",
      page: "plan",
      disabled: true,
      reason: "Статус настройки пока недоступен."
    };
  }
  return {
    label: action.label,
    page: action.page === "design" ? "design" : "plan",
    disabled: action.disabled === true,
    reason: typeof action.reason === "string" && action.reason.length > 0 ? action.reason : null
  };
}
