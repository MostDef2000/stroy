// Pure helpers for R12 renovation progress (#162): snapshots + planned-vs-
// observed deviations. RU label maps for every wire enum, the unresolved
// (open+confirmed) filter, the per-status summary builder, compare-target
// descriptions, the deviation display line and the exact accept-command
// envelope builder. No React and no network so the module can be unit-tested
// in isolation (see tests/progress.test.mjs). Structural wire types keep this
// module free of the browser-only api.ts (attachments.ts pattern) — the single
// allowed import is the pure twinDesign helper whose uniqueId() is the
// project-convention command id (sceneVariants.ts precedent).
//
// The ".js" suffix is required for the compiled ESM output (node --test
// imports ../build/*.js directly); Bundler resolution maps it back to .ts.
import { uniqueId } from "./twinDesign.js";

// ---------------------------------------------------------------------------
// Wire enums (#162 contract) — api.ts re-exports these types for the UI.
// ---------------------------------------------------------------------------

/** Renovation stage a snapshot captures. */
export type ProgressStage =
  | "original"
  | "demolition"
  | "rough"
  | "fitout"
  | "current"
  | "completed";

/** Where a snapshot/deviation fact came from. */
export type ProgressProvenance =
  | "observed"
  | "manually_measured"
  | "model_inferred"
  | "confirmed";

/** Owner confidence in an observed fact. */
export type ProgressConfidence = "low" | "medium" | "high";

/** Deviation lifecycle; "accepted" is reachable only via the accept endpoint. */
export type ProgressDeviationStatus =
  | "open"
  | "confirmed"
  | "accepted"
  | "discarded";

/** What the observation asserts about the planned state. */
export type ProgressObservedKind =
  | "missing"
  | "unexpected"
  | "property_diff"
  | "state_diff"
  | "note";

// ---------------------------------------------------------------------------
// Wire types (#162 contract, cross-checked against services/progress.py).
// api.ts re-exports these; the panel consumes them via api.ts.
// ---------------------------------------------------------------------------

/** What a snapshot is pinned against. Omitted at create → the canonical
 * scene's latest revision is pinned ONCE (never re-resolved). */
export type ProgressCompareTarget = {
  kind: "canonical" | "variant";
  variant_id?: string | null;
  revision_id?: string | null;
};

/** The snapshot row as returned by the backend (snapshot_view). */
export type ProgressSnapshot = {
  id: string;
  project_id: string;
  name: string;
  stage: ProgressStage;
  stage_label: string | null;
  captured_at: string;
  provenance: ProgressProvenance;
  note: string | null;
  attachment_ids: string[];
  compare_target: ProgressCompareTarget;
  metadata: Record<string, unknown>;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  /** LIST view only: per-status deviation counts (all four keys always present). */
  deviation_counts?: {
    open: number;
    confirmed: number;
    accepted: number;
    discarded: number;
  };
  /** DETAIL view only (getSnapshot): the snapshot's deviations. */
  deviations?: ProgressDeviation[];
};

/** The deviation row as returned by the backend (deviation_view). */
export type ProgressDeviation = {
  id: string;
  project_id: string;
  snapshot_id: string;
  status: ProgressDeviationStatus;
  planned_revision_id: string | null;
  planned: {
    target_type: "scene" | "room" | "entity";
    entity_id?: string | null;
    room_id?: string | null;
    property_path?: string | null;
    value?: unknown;
  };
  observed: { kind: ProgressObservedKind; value: unknown };
  provenance: ProgressProvenance;
  confidence: ProgressConfidence;
  evidence_attachment_ids: string[];
  note: string | null;
  /** Set once accepted (accept endpoint only). */
  accepted_target_kind?: string | null;
  accepted_variant_id?: string | null;
  accepted_command_id?: string | null;
  accepted_revision_id?: string | null;
  created_at: string;
  updated_at: string;
};

/** One deviation inside a compare payload: the review subset + the BE-computed
 * dangling flag (planned target no longer exists in the pinned scene). */
export type ProgressCompareDeviation = {
  id: string;
  status: ProgressDeviationStatus;
  planned: {
    target_type: "scene" | "room" | "entity";
    entity_id?: string | null;
    room_id?: string | null;
    property_path?: string | null;
    value?: unknown;
    dangling: boolean;
  };
  observed: { kind: ProgressObservedKind; value: unknown };
  provenance: ProgressProvenance;
  confidence: ProgressConfidence;
  evidence_attachment_ids: string[];
  note: string | null;
};

/** GET /progress/snapshots/{id}/compare response. */
export type ProgressCompare = {
  snapshot: ProgressSnapshot;
  target: ProgressCompareTarget;
  summary: {
    open: number;
    confirmed: number;
    accepted: number;
    discarded: number;
    total: number;
  };
  deviations: ProgressCompareDeviation[];
};

/** POST /progress/snapshots body (captured_at: ISO datetime, required). */
export type ProgressSnapshotPostBody = {
  name: string;
  stage: ProgressStage;
  stage_label?: string;
  captured_at: string;
  provenance?: ProgressProvenance;
  note?: string;
  attachment_ids?: string[];
  compare_target?: ProgressCompareTarget;
};

/** Planned side of a deviation: what the pinned scene says should be. */
export type ProgressPlannedRef = {
  target_type: "scene" | "room" | "entity";
  entity_id?: string | null;
  room_id?: string | null;
  property_path?: string | null;
  value?: unknown;
};

/** POST /progress/snapshots/{id}/deviations body. accepted is NOT creatable;
 * provenance/confidence are required (no server defaults). */
export type ProgressDeviationPostBody = {
  planned: ProgressPlannedRef;
  observed: { kind: ProgressObservedKind; value: unknown };
  status?: Exclude<ProgressDeviationStatus, "accepted">;
  provenance: ProgressProvenance;
  confidence: ProgressConfidence;
  evidence_attachment_ids?: string[];
  note?: string;
};

/** PATCH /progress/deviations/{id} body — accepted is not patchable. */
export type ProgressDeviationPatchBody = {
  status?: Exclude<ProgressDeviationStatus, "accepted">;
  provenance?: ProgressProvenance;
  confidence?: ProgressConfidence;
  observed?: { kind: ProgressObservedKind; value: unknown };
  note?: string;
};

/** POST /progress/deviations/{id}/accept body: the CAS target plus the full
 * accept envelope (progress.buildAcceptCommand builds it). */
export type ProgressAcceptPostBody = {
  target:
    | { kind: "canonical" }
    | { kind: "variant"; variant_id: string; expected_head_revision_id: string };
  command: AcceptCommandEnvelope;
};

/** POST …/accept response: the accepted deviation + the new head revision. */
export type ProgressAcceptResponse = {
  deviation: ProgressDeviation;
  revision: {
    revision_id: string;
    parent_revision_id: string | null;
    content_hash: string;
    scene: SceneDocumentLike;
  };
};

/** Structural scene (accept response carries the full new scene). */
export type SceneDocumentLike = {
  scene_id: string;
  project_id: string;
  entities: Array<Record<string, unknown>>;
  cameras: Array<Record<string, unknown>>;
};

// ---------------------------------------------------------------------------
// RU labels (the #162 label contract).
// ---------------------------------------------------------------------------

export const STAGE_LABELS: Record<ProgressStage, string> = {
  original: "Оригинал",
  demolition: "Демонтаж",
  rough: "Черновые работы",
  fitout: "Отделка",
  current: "Текущее",
  completed: "Завершено"
};

export const PROVENANCE_LABELS: Record<ProgressProvenance, string> = {
  observed: "Наблюдение",
  manually_measured: "Ручной замер",
  model_inferred: "Модель",
  confirmed: "Подтверждено владельцем"
};

export const CONFIDENCE_LABELS: Record<ProgressConfidence, string> = {
  low: "Низкая",
  medium: "Средняя",
  high: "Высокая"
};

export const DEVIATION_STATUS_LABELS: Record<ProgressDeviationStatus, string> = {
  open: "Открыто",
  confirmed: "Подтверждено",
  accepted: "Принято",
  discarded: "Отклонено"
};

export const OBSERVED_KIND_LABELS: Record<ProgressObservedKind, string> = {
  missing: "Отсутствует",
  unexpected: "Лишнее",
  property_diff: "Отличие параметра",
  state_diff: "Отличие состояния",
  note: "Заметка"
};

export function stageLabel(stage: ProgressStage): string {
  return STAGE_LABELS[stage] ?? stage;
}

export function provenanceLabel(provenance: ProgressProvenance): string {
  return PROVENANCE_LABELS[provenance] ?? provenance;
}

export function confidenceLabel(confidence: ProgressConfidence): string {
  return CONFIDENCE_LABELS[confidence] ?? confidence;
}

export function deviationStatusLabel(status: ProgressDeviationStatus): string {
  return DEVIATION_STATUS_LABELS[status] ?? status;
}

export function observedKindLabel(kind: ProgressObservedKind): string {
  return OBSERVED_KIND_LABELS[kind] ?? kind;
}

/** Deviation statuses a reviewer still has to act on (open + confirmed). */
export const UNRESOLVED_DEVIATION_STATUSES: readonly ProgressDeviationStatus[] =
  ["open", "confirmed"];

export function isUnresolvedDeviation(
  status: ProgressDeviationStatus
): boolean {
  return UNRESOLVED_DEVIATION_STATUSES.includes(status);
}

/**
 * Only open+confirmed rows need review; returns a filtered COPY (never
 * mutates the polled list — visibleVariants rule).
 */
export function filterUnresolvedDeviations<T extends { status: ProgressDeviationStatus }>(
  deviations: readonly T[]
): T[] {
  return deviations.filter((deviation) => isUnresolvedDeviation(deviation.status));
}

// ---------------------------------------------------------------------------
// Summary counts.
// ---------------------------------------------------------------------------

/** Per-status counts over a deviation list, plus the total. Statuses absent
 * from the list stay zero; the four wire statuses are always present. */
export function summarizeDeviations(
  deviations: ReadonlyArray<{ status: ProgressDeviationStatus }>
): {
  open: number;
  confirmed: number;
  accepted: number;
  discarded: number;
  total: number;
} {
  const summary = { open: 0, confirmed: 0, accepted: 0, discarded: 0 };
  for (const deviation of deviations ?? []) {
    if (deviation && deviation.status in summary) {
      summary[deviation.status as keyof typeof summary] += 1;
    }
  }
  return { ...summary, total: summary.open + summary.confirmed + summary.accepted + summary.discarded };
}

// ---------------------------------------------------------------------------
// Compare-target description.
// ---------------------------------------------------------------------------

/** Structural view of a snapshot's compare target (api.ts adds the rest). */
export type ProgressCompareTargetLike = {
  kind: "canonical" | "variant";
  variant_id?: string | null;
  revision_id?: string | null;
};

/**
 * «Каноничная сцена» for the canonical target; «Вариант „{title}“» for a
 * variant target — the variant title is used when the caller can resolve it
 * (titles map), otherwise the variant id is the honest fallback (no raw ids
 * where human labels exist; a variant id IS its label when unnamed).
 */
export function compareTargetDescription(
  target: ProgressCompareTargetLike,
  variantTitles?: ReadonlyMap<string, string>
): string {
  if (target.kind === "variant") {
    const id = typeof target.variant_id === "string" ? target.variant_id : "";
    const title = id ? variantTitles?.get(id) : undefined;
    return `Вариант „${title || id || "без привязки"}“`;
  }
  return "Каноничная сцена";
}

// ---------------------------------------------------------------------------
// Deviation display line (planned → observed, RU).
// ---------------------------------------------------------------------------

/** Structural view of a deviation row (compare view adds `dangling`). */
export type ProgressDeviationLike = {
  planned: {
    target_type: "scene" | "room" | "entity";
    entity_id?: string | null;
    room_id?: string | null;
    property_path?: string | null;
    value?: unknown;
  };
  observed: { kind: ProgressObservedKind; value?: unknown };
  dangling?: boolean;
};

/** Dangling-warning clause appended to the display line (BE compare flag). */
export const DANGLING_WARNING = "Запланированный объект больше не существует";

/** Compact display form of a scene id ("object.sofa.main" → "sofa.main"). */
function shortId(id: string | null | undefined): string {
  if (!id) return "";
  const stripped = id.replace(/^(object|room|camera|surface)\./, "");
  return stripped.length > 12 ? `${stripped.slice(0, 12)}…` : stripped;
}

/**
 * Observed/planned value → display string. Strings pass through (trimmed,
 * empty → ""), scalars stringify, JSON values compact to one line, null → "".
 */
function formatDeviationValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

/**
 * One-line RU description of a deviation: the planned target (human label for
 * scene/room/entity — entity/room ids are their own honest fallback) with the
 * property path when present, then the observed fact. Both values known →
 * "план → факт"; otherwise the observed kind label carries the line. The
 * dangling clause is appended when BE flagged the planned target as gone.
 * Only planned/observed data is consumed — snapshot/variant/revision ids can
 * never leak into the line.
 */
export function deviationDisplayLine(
  deviation: ProgressDeviationLike
): string {
  const planned = deviation.planned ?? { target_type: "scene" as const };
  let subject: string;
  if (planned.target_type === "entity") {
    const id = shortId(planned.entity_id);
    subject = id ? `Объект ${id}` : "Объект без привязки";
  } else if (planned.target_type === "room") {
    const id = shortId(planned.room_id);
    subject = id ? `Комната ${id}` : "Комната без привязки";
  } else {
    subject = "Сцена";
  }
  const path =
    typeof planned.property_path === "string" && planned.property_path.trim()
      ? ` · ${planned.property_path.trim()}`
      : "";
  const observedLabel = observedKindLabel(deviation.observed.kind);
  const observedValue = formatDeviationValue(deviation.observed.value);
  const plannedValue = formatDeviationValue(planned.value);
  let line: string;
  if (plannedValue && observedValue) {
    line = `${subject}${path}: ${plannedValue} → ${observedValue}`;
  } else if (observedValue) {
    line = `${subject}${path}: ${observedLabel} — ${observedValue}`;
  } else {
    line = `${subject}${path}: ${observedLabel}`;
  }
  if (deviation.dangling) {
    line += ` · ${DANGLING_WARNING}`;
  }
  return line;
}

// ---------------------------------------------------------------------------
// Accept flow (#162): the confirmed deviation's fix rides the accept endpoint
// as a DesignCommand envelope wrapping an HONEST scene change. Presets exist
// only where the client can build a real, executable command — never a fake
// no-op:
//   observed.kind === "missing" && planned.entity_id → remove_object (the
//     planned object is absent on site → remove it from the scene).
// Everything else (unexpected needs a full add_object entity payload the
// deviation does not carry; property_diff/state_diff/note have no honest
// mapping) stays disabled with ACCEPT_UNAVAILABLE_HINT.
// ---------------------------------------------------------------------------

/** The one honest preset: remove_object on the planned entity. This is the
 * PRESET description; buildAcceptCommand expands it into a full embedded
 * DesignCommand (BE pydantic requires command_id/base_revision_id/origin on
 * the nested object too). */
export type AcceptChangeCore = {
  operation: "remove_object";
  target_id: string;
  parameters: Record<string, never>;
};

/** RU hint shown when no honest scene change exists for a deviation. */
export const ACCEPT_UNAVAILABLE_HINT =
  "Автоматическое изменение сцены недоступно для этого типа отклонения";

/**
 * The embedded change for «Принять в сцену», or null when no honest preset
 * exists (the UI then disables the accept action with the hint).
 */
export function acceptChangeFor(deviation: {
  planned: { entity_id?: string | null };
  observed: { kind: ProgressObservedKind };
}): AcceptChangeCore | null {
  if (deviation.observed?.kind !== "missing") return null;
  const entityId = deviation.planned?.entity_id;
  if (typeof entityId !== "string" || entityId.length === 0) return null;
  return { operation: "remove_object", target_id: entityId, parameters: {} };
}

/** The embedded change as the BE runtime re-validates it: a FULL
 * DesignCommand (domain/commands.py apply_command →
 * DesignCommand.model_validate(raw_change) — command_id, base_revision_id
 * and origin are required on the nested object as well; request_text stays
 * omitted, reference_asset_ids rides empty like the wrapper). */
export type AcceptChangeCommand = {
  schema_version: "0.1.0";
  command_id: string;
  base_revision_id: string;
  operation: "remove_object";
  target_id: string;
  parameters: Record<string, never>;
  reference_asset_ids: string[];
  origin: "user";
};

/** Full DesignCommand envelope for POST /progress/deviations/{id}/accept —
 * field-for-field the shape sceneVariants.buildVariantCommandEnvelope emits
 * (schema_version 0.1.0, fresh UUID command ids, user origin, null
 * request_text), with the FULL change command embedded in parameters per the
 * #162 schema (additionalProperties: false — nothing else may ride along). */
export type AcceptCommandEnvelope = {
  schema_version: "0.1.0";
  command_id: string;
  base_revision_id: string;
  operation: "accept_confirmed_deviation";
  target_id: string;
  parameters: {
    deviation_id: string;
    change: AcceptChangeCommand;
  };
  reference_asset_ids: string[];
  origin: "user";
  request_text: null;
};

/**
 * Build the accept envelope. base_revision_id is the head the caller
 * observed (canonical latest, or the variant head — both CAS-checked
 * server-side) and is stamped on BOTH the wrapper and the embedded change
 * (the BE delegates to the embedded command against the same head). The
 * wrapper target_id echoes the change target (or "scene" when the change has
 * none) so the command reads coherently in the audit. Wrapper and embedded
 * command ids are FRESH and distinct UUIDs — both land in the command audit.
 */
export function buildAcceptCommand(input: {
  deviationId: string;
  change: AcceptChangeCore;
  baseRevisionId: string;
}): AcceptCommandEnvelope {
  return {
    schema_version: "0.1.0",
    command_id: uniqueId(),
    base_revision_id: input.baseRevisionId,
    operation: "accept_confirmed_deviation",
    target_id: input.change.target_id || "scene",
    parameters: {
      deviation_id: input.deviationId,
      change: {
        schema_version: "0.1.0",
        command_id: uniqueId(),
        base_revision_id: input.baseRevisionId,
        operation: input.change.operation,
        target_id: input.change.target_id,
        parameters: input.change.parameters,
        reference_asset_ids: [],
        origin: "user"
      }
    },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  };
}
