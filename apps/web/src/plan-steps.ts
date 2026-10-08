// Guided 4-stage flow model for the plan editor (#104). Pure and total: it
// derives stage states from a few booleans/counts, nothing throws, no React.
//
// Stages always come back in this fixed order:
//   upload -> markup -> scale -> build
// A stage is "done" from its own rule. It is "reachable" when every earlier
// stage is done (upload is always reachable). The first not-done reachable
// stage is "current"; every other not-done stage is "todo". When all stages
// are done there is no current stage.

export type PlanStageId = "upload" | "markup" | "scale" | "build";
export type PlanStageState = "done" | "current" | "todo";

export interface PlanStage {
  id: PlanStageId;
  label: string;
  state: PlanStageState;
  hint: string;
}

export interface PlanStageInput {
  apartmentImageCount: number;
  hasDraft: boolean;
  scaleKnown: boolean;
  committed: boolean;
}

const STAGE_ORDER: PlanStageId[] = ["upload", "markup", "scale", "build"];

const STAGE_LABELS: Record<PlanStageId, string> = {
  upload: "Загрузить план",
  markup: "Проверить разметку",
  scale: "Проверить масштаб",
  build: "Создать / обновить 3D"
};

const STAGE_HINTS: Record<PlanStageId, string> = {
  upload: "Загрузите изображение плана квартиры.",
  markup: "Проанализируйте план и проверьте разметку комнат и стен.",
  scale: "Задайте масштаб по двум точкам плана — без него нельзя создать 3D.",
  build: "Создайте 3D-сцену из проверенного плана."
};

export function computePlanStages(input: PlanStageInput): PlanStage[] {
  const { apartmentImageCount, hasDraft, scaleKnown, committed } = input;

  const done: Record<PlanStageId, boolean> = {
    upload: apartmentImageCount > 0,
    markup: hasDraft,
    scale: hasDraft && scaleKnown,
    build: committed
  };

  // Reachability: upload always; a stage is reachable when the previous one is
  // done. Computed on the done flags above.
  const reachable: Record<PlanStageId, boolean> = {
    upload: true,
    markup: done.upload,
    scale: done.markup,
    build: done.scale
  };

  // The single "current" is the first not-done reachable stage.
  let currentIndex = -1;
  for (let index = 0; index < STAGE_ORDER.length; index += 1) {
    const id = STAGE_ORDER[index];
    if (!done[id] && reachable[id]) {
      currentIndex = index;
      break;
    }
  }

  return STAGE_ORDER.map((id, index) => {
    const state: PlanStageState = done[id] ? "done" : index === currentIndex ? "current" : "todo";
    return { id, label: STAGE_LABELS[id], state, hint: STAGE_HINTS[id] };
  });
}

// ---------------------------------------------------------------------------
// R6 plan autosave (#183): the pure debounce decision. The PlanEditor effect
// runs this on a timer with wall-clock timestamps; being pure, the policy is
// unit-testable without a DOM (tests/plan-steps.test.mjs).
//
// Policy:
//   - not dirty                → wait   (nothing to save)
//   - a save is in flight      → queue  (re-save after it lands if still dirty)
//   - idle for >= debounceMs   → save   (quiet period after the last edit)
//   - dirty for >= maxDelayMs  → save   (hard cap: continuous editing must not
//                                        starve the server copy for long)
//   - otherwise                → wait   (inside the debounce window)
// ---------------------------------------------------------------------------

export type AutosaveDecision = "save" | "queue" | "wait";

export interface AutosaveDecisionInput {
  dirty: boolean;
  inflight: boolean;
  msSinceLastEdit: number;
  /**
   * How long the draft has carried unsaved changes — measured from the FIRST
   * edit of the CURRENT unsaved batch. The caller (PlanEditor) resets the
   * batch start after every successful save, so the maxDelayMs cap bounds
   * each dirty cycle, not the whole session.
   */
  msSinceDirtyStart: number;
  debounceMs: number;
  maxDelayMs: number;
}

export function planAutosaveDecision(input: AutosaveDecisionInput): AutosaveDecision {
  const { dirty, inflight, msSinceLastEdit, msSinceDirtyStart, debounceMs, maxDelayMs } = input;
  if (!dirty) return "wait";
  if (inflight) return "queue";
  if (msSinceLastEdit >= debounceMs || msSinceDirtyStart >= maxDelayMs) return "save";
  return "wait";
}
