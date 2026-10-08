// Pure helpers for the R1 project attachments (notes, tasks, photos, files):
// grouping, overdue checks, human labels, dangling-target detection and the
// exact POST /attachments payload with typed validation. No React and no
// network so the module can be unit-tested in isolation (see
// tests/attachments.test.mjs). Structural types keep this module free of the
// browser-only api.ts so it compiles with a bare `tsc` (twinDesign.ts pattern).

/** Attachment target kinds (R1 contract: project-level, room or entity). */
export type AttachmentTargetType = "project" | "room" | "entity";

/** Attachment kinds (R1 contract: photo | note | file | task). */
export type AttachmentKind = "photo" | "note" | "file" | "task";

/**
 * The attachment record as returned by the backend. Structural mirror of the
 * attachments table row (api.ts re-exports this type for the UI).
 */
export type Attachment = {
  id: string;
  project_id: string;
  target_type: AttachmentTargetType;
  target_id: string | null;
  kind: AttachmentKind;
  asset_id: string | null;
  body: string | null;
  due_date: string | null;
  done: boolean;
  metadata: object;
  created_at: string;
  updated_at: string;
};

/** Filter query accepted by listAttachments (all keys optional). */
export type AttachmentFilters = {
  target_type?: AttachmentTargetType;
  target_id?: string;
  kind?: AttachmentKind;
};

/** Minimal structural view of a scene for dangling-target checks. */
export type AttachmentSceneLike = {
  entities: Array<{ id: string }>;
} | null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Group a flat attachment list by target ("target_type:target_id"). Pure:
 * returns a new Map, never mutates the input; list order is preserved inside
 * each group. Project-level rows group under the "project:" key (their
 * target_id is null).
 */
export function groupAttachmentsByTarget(
  list: readonly Attachment[]
): Map<string, Attachment[]> {
  const groups = new Map<string, Attachment[]>();
  for (const attachment of list) {
    const key = `${attachment.target_type}:${attachment.target_id ?? ""}`;
    const bucket = groups.get(key);
    if (bucket) bucket.push(attachment);
    else groups.set(key, [attachment]);
  }
  return groups;
}

/**
 * Local-calendar date key "YYYY-MM-DD" (not UTC — an ISO slice of Date would
 * shift the day for evening hours west of GMT).
 */
function localDateKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/**
 * True when a due_date carries time/zone info beyond a plain YYYY-MM-DD
 * (an ISO datetime separator, a trailing Z, or a numeric UTC offset). The
 * backend sends plain dates — this branch is purely defensive.
 */
function hasTimeOrZone(dueDate: string): boolean {
  return (
    /T/i.test(dueDate) || /Z$/i.test(dueDate) || /[+-]\d{2}:?\d{2}$/.test(dueDate)
  );
}

/**
 * Only kind=task counts as overdue: not done AND due_date strictly before
 * today (local calendar). Due today is NOT overdue; tasks without a due date
 * are never overdue.
 *
 * Timezone handling: the backend's plain "YYYY-MM-DD" dates carry no zone and
 * are compared as written against the local today key. Defensive datetime /
 * zone values ("2026-10-06T23:00:00Z", "+05:30" offsets) are normalized to
 * their LOCAL calendar date first — comparing a raw UTC string against a
 * local date key flips the day for users east of GMT (2026-10-06T23:00:00Z
 * is already 2026-10-07 in GMT+5, so it is due today there, not overdue).
 * Unparseable defensive values never mark a task overdue.
 */
export function isTaskOverdue(
  attachment: Attachment,
  today: Date = new Date()
): boolean {
  if (attachment.kind !== "task" || attachment.done) return false;
  const dueDate = attachment.due_date;
  if (typeof dueDate !== "string" || dueDate.length === 0) return false;
  let dueKey: string | null;
  if (hasTimeOrZone(dueDate)) {
    const parsed = new Date(dueDate);
    dueKey = Number.isNaN(parsed.getTime()) ? null : localDateKey(parsed);
  } else {
    dueKey = dueDate;
  }
  if (dueKey === null) return false;
  return dueKey < localDateKey(today);
}

const KIND_LABELS: Record<AttachmentKind, string> = {
  photo: "Фото",
  note: "Заметка",
  file: "Файл",
  task: "Задача"
};

const TARGET_TYPE_LABELS: Record<AttachmentTargetType, string> = {
  project: "проект",
  room: "комната",
  entity: "объект"
};

/**
 * Compact display form of an id: strip the scene namespace prefix
 * ("object.sofa.main" → "sofa.main", "room.1" → "1") and clip long tails.
 */
function shortId(id: string | null): string {
  if (!id) return "";
  const stripped = id.replace(/^(object|room|camera|surface)\./, "");
  return stripped.length > 12 ? `${stripped.slice(0, 12)}…` : stripped;
}

/**
 * Compact human label: kind + target, e.g. "Задача · комната Гостиная",
 * "Заметка · проект". Uses the attachment body as a hint for tasks/notes with
 * text. Falls back to the raw id tail when no better name is known (this pure
 * helper cannot resolve scene display names).
 */
export function attachmentLabel(attachment: Attachment): string {
  const parts: string[] = [KIND_LABELS[attachment.kind] ?? attachment.kind];
  if (attachment.target_type === "project") {
    parts.push("проект");
  } else {
    parts.push(`${TARGET_TYPE_LABELS[attachment.target_type] ?? attachment.target_type} ${shortId(attachment.target_id)}`.trim());
  }
  const label = parts.join(" · ");
  const body = typeof attachment.body === "string" ? attachment.body.trim() : "";
  return body ? `${label} — ${body}` : label;
}

/** Partial update accepted by PATCH /attachments/{id}. */
export type AttachmentPatch = {
  body?: string | null;
  done?: boolean;
  due_date?: string | null;
};

export type AttachmentPayloadInput = {
  targetType: AttachmentTargetType;
  targetId: string | null;
  kind: AttachmentKind;
  body?: string | null;
  assetId?: string | null;
  dueDate?: string | null;
  /** R6 (#183): room-mapping attachments carry {mapping, confidence}; absent → {}. */
  metadata?: object;
};

export type AttachmentPostBody = {
  target_type: AttachmentTargetType;
  target_id: string | null;
  kind: AttachmentKind;
  body: string | null;
  asset_id: string | null;
  due_date: string | null;
  metadata: object;
};

/** Typed validation result (no throwing — the caller renders `error`). */
export type AttachmentPayloadResult =
  | { ok: true; payload: AttachmentPostBody }
  | { ok: false; error: string };

/**
 * Build the POST /attachments body. Typed error result instead of an
 * exception: photo/file attachments must reference an uploaded asset
 * (asset_id), everything else must not invent one. Optional fields are
 * normalized to null so the payload shape stays total.
 */
export function buildAttachmentPayload(
  input: AttachmentPayloadInput
): AttachmentPayloadResult {
  const needsAsset = input.kind === "photo" || input.kind === "file";
  const assetId = typeof input.assetId === "string" && input.assetId.length > 0 ? input.assetId : null;
  if (needsAsset && !assetId) {
    return {
      ok: false,
      error:
        input.kind === "photo"
          ? "Для фото сначала загрузите файл во вложение."
          : "Для файла сначала загрузите его во вложение."
    };
  }
  if (!needsAsset && assetId) {
    return {
      ok: false,
      error: "Файл можно прикрепить только к фото или файлу."
    };
  }
  const targetId =
    input.targetType === "project" ? null : input.targetId;
  if (input.targetType !== "project" && (!targetId || targetId.length === 0)) {
    return { ok: false, error: "Не указана цель вложения." };
  }
  const body =
    typeof input.body === "string" && input.body.trim().length > 0
      ? input.body.trim()
      : null;
  const dueDate =
    typeof input.dueDate === "string" && input.dueDate.trim().length > 0
      ? input.dueDate.trim()
      : null;
  if (input.kind === "task" && !dueDate && !body) {
    return { ok: false, error: "У задачи укажите описание или срок." };
  }
  return {
    ok: true,
    payload: {
      target_type: input.targetType,
      target_id: targetId,
      kind: input.kind,
      body,
      asset_id: assetId,
      due_date: dueDate,
      metadata: input.metadata ?? {}
    }
  };
}

/**
 * R6 (#183): true when a photo attachment's metadata marks an approximate
 * photo→room mapping set by the room-targeted attach flow
 * ({mapping: "owner_room", confidence: "approx"}). Total: any non-record or
 * foreign metadata is simply not an approx mapping.
 */
export function isApproxRoomMapping(metadata: unknown): boolean {
  if (!isRecord(metadata)) return false;
  return metadata["mapping"] === "owner_room" && metadata["confidence"] === "approx";
}

/**
 * True when the attachment's target no longer exists in the given scene: the
 * room/entity id is not among the scene entities. Project-level attachments
 * are never dangling; a missing scene means "cannot verify" → not dangling
 * (the list may render before the scene loads). A room/entity row without a
 * target_id counts as dangling.
 */
export function isDanglingTarget(
  attachment: Attachment,
  scene: AttachmentSceneLike
): boolean {
  if (!isRecord(attachment)) return false;
  if (attachment.target_type === "project") return false;
  if (typeof attachment.target_id !== "string" || attachment.target_id.length === 0) {
    return true;
  }
  if (!scene || !Array.isArray(scene.entities)) return false;
  return !scene.entities.some(
    (entity) => isRecord(entity) && entity.id === attachment.target_id
  );
}
