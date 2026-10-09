// Single source of truth for owner-facing copy: job status vocabulary (#108)
// and the R5 owner-copy glossary (#188). Pure module: no React/api imports so
// it can be unit-tested in isolation.

export function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "в очереди";
    case "pending":
      return "ожидает";
    case "running":
      return "выполняется";
    case "leased":
      return "выполняется";
    case "succeeded":
      return "выполнено";
    case "failed":
      return "ошибка";
    case "cancelled":
      return "отменено";
    default:
      return status;
  }
}

// ---------------------------------------------------------------------------
// R5 glossary (#188): internal identifiers never reach the default UI; the
// maps below are the only sanctioned display names. Enum/wire values stay
// English — only labels are translated.
// ---------------------------------------------------------------------------

/** Scene entity kinds → RU display labels. Unknown kinds pass through. */
const ENTITY_KIND_LABELS: Record<string, string> = {
  wall: "стена",
  floor: "пол",
  room: "комната",
  furniture: "мебель",
  opening: "проём",
  door: "дверь",
  window: "окно",
  ceiling: "потолок"
};

export function entityKindLabel(kind: string): string {
  return ENTITY_KIND_LABELS[kind] ?? kind;
}

/** Plan opening kinds → RU display labels. Unknown kinds pass through. */
export function openingKindLabel(kind: string): string {
  switch (kind) {
    case "door":
      return "дверь";
    case "window":
      return "окно";
    case "arch":
      return "арка";
    default:
      return kind;
  }
}

/**
 * Renderer profile display name. `blender-cycles-v0` is a wire value — the
 * owner-facing name is «Фоторендер»; unknown profiles pass through unchanged.
 */
export function rendererProfileLabel(profile: string | null | undefined): string {
  if (profile === "blender-cycles-v0") return "Фоторендер";
  return profile ?? "";
}

/** Render pass tag: whether the render carries a usable image pass or not. */
export function renderImageTag(hasImage: boolean): string {
  return hasImage ? "изображение" : "нет изображения";
}

/**
 * Asset-picker label: files without a name never surface raw ids in the
 * picker (the id stays available via title/details per the #188 mapping).
 */
export function fileLabel(originalName: string | null | undefined): string {
  return originalName && originalName.trim() !== ""
    ? originalName
    : "Файл без названия";
}

/** 409 conflict hint for scene commands (replaces the raw "(409)" wording). */
export const DESIGN_CONFLICT_HINT =
  "Изменение не применилось: сцена уже изменилась или объект заблокирован. " +
  "Обновите сцену и запустите проверку дизайна.";

/**
 * Worker/queue notices: «GPU-воркер» is internal vocabulary — owner-facing
 * copy says «очередь обработки» (#188).
 */
export const WORKER_OFFLINE_NOTICE =
  "Генерация временно недоступна: очередь обработки недоступна. " +
  "Новые задачи будут ждать её восстановления.";

/** Analyze-progress line in the plan entry phase (queue wording, #188). */
export function planAnalyzeQueueText(status: string): string {
  return `${statusLabel(status)} · план разбирается в очереди обработки, обычно 1–4 минуты (лимит 15)`;
}

/** Style-analysis reference limits (role «референс», glossary #188). */
export function styleAnalysisMinMessage(count: number): string {
  return `Для анализа стиля нужно минимум 3 изображения-референса (роль «референс»). Сейчас загружено: ${count}.`;
}

export function styleAnalysisMaxMessage(count: number): string {
  return `Для анализа стиля можно использовать максимум 5 изображений-референсов. Сейчас выбрано: ${count}.`;
}

// ---------------------------------------------------------------------------
// R7 (#184/#187) copy helpers.
// ---------------------------------------------------------------------------

/**
 * Room inspector heading (Q3): the owner-entered name wins; blank/whitespace
 * names fall back to the stable «Комната {id}» form (id is plan-derived and
 * stable, unlike a silently empty heading).
 */
export function roomHeading(room: { id: string; name?: string | null }): string {
  const name = typeof room.name === "string" ? room.name.trim() : "";
  return name || `Комната ${room.id}`;
}

/** Input for saveIndicator: the plan editor's autosave state flags. */
export type SaveIndicatorState = {
  saving: boolean;
  dirty: boolean;
  /** Latest known server draft status ("committed" = 3D built), null if none. */
  draftStatus: string | null;
};

/**
 * Heading indicator next to «План квартиры» (Q4). Precedence — what the owner
 * must act on now beats historical facts: saving in flight > unsaved edits >
 * 3D created > draft saved > default (edits not yet saved anywhere).
 */
export function saveIndicator(state: SaveIndicatorState): string {
  if (state.saving) return "Сохранение…";
  if (state.dirty) return "Есть несохранённые правки";
  if (state.draftStatus === "committed") return "3D-сцена создана";
  if (state.draftStatus) return "План сохранён";
  return "Есть несохранённые правки";
}

/** Render stage (R7 #187): wire value → owner-facing chip label. Null/absent
 * (legacy renders without a stage, R8 brief wire) reads as final (#198). */
export function renderStageLabel(
  stage: string | null | undefined
): string {
  return stage === "draft" ? "Черновик" : "Финальный";
}

/** Photo→room mapping confidence (R7 #184): wire value → badge label. */
export function mappingConfidenceLabel(
  confidence: string | null | undefined
): string {
  switch (confidence) {
    case "confirmed":
      return "подтверждено владельцем";
    case "calibrated":
      return "откалибровано";
    case "approx":
    default:
      return "приблизительно";
  }
}

// ---------------------------------------------------------------------------
// R8 designer brief (#198): section titles, intent-group labels and the
// brief's fixed disclaimer copy. Pure helpers — same test ritual as the
// glossary above. Wire values stay English; only labels are translated.
// ---------------------------------------------------------------------------

/** Preview/print section titles keyed by section id (briefView.BriefSectionId). */
export const BRIEF_SECTION_TITLES: Record<string, string> = {
  notes: "Потребности и пожелания",
  plan: "План квартиры",
  rooms: "Комнаты",
  furniture: "Мебель и объекты",
  renders: "Рендеры",
  photos: "Фотографии квартиры",
  style: "Стилевое решение",
  budget: "Смета",
  questions: "Вопросы для обсуждения"
};

export function briefSectionTitle(sectionId: string): string {
  return BRIEF_SECTION_TITLES[sectionId] ?? sectionId;
}

/** Furniture-intent group keys → owner-facing headings (fixed print order). */
export function briefIntentGroupLabel(key: string): string {
  switch (key) {
    case "keep":
      return "Сохранить";
    case "remove":
      return "Убрать";
    case "replace":
      return "Заменить";
    case "locked":
      return "Закреплено";
    default:
      return key;
  }
}

/** Fixed order of the intent groups in the brief (print order, #198). */
export const BRIEF_INTENT_GROUP_ORDER = [
  "keep",
  "remove",
  "replace",
  "locked"
] as const;

/**
 * Plan scale line: the BE label is owner copy and wins; the RU fallbacks only
 * cover a missing/empty label so a future BE change can never blank the line.
 */
export function briefScaleLabel(
  scale: { status?: string; label?: string | null } | null | undefined
): string {
  const label = typeof scale?.label === "string" ? scale.label.trim() : "";
  if (label) return label;
  switch (scale?.status) {
    case "confirmed":
      return "Масштаб подтверждён";
    case "approximate":
      return "Масштаб приблизительный";
    case "unknown":
    default:
      return "Масштаб неизвестен";
  }
}

/** Fixed disclaimer carried on every generated image («Концепт, не фотография»). */
export const BRIEF_CONCEPT_NOT_PHOTO = "Концепт, не фотография";

/** Fallback banner line — BE always sends warnings; this only covers an
 * empty array so the document never prints without the disclaimer. */
export const BRIEF_NOT_BUILDING_DOC =
  "Бриф не является строительной или рабочей документацией.";

/** BE warnings verbatim; empty list → the fixed fallback disclaimer. */
export function briefBannerLines(warnings: readonly string[]): string[] {
  return warnings.length > 0 ? [...warnings] : [BRIEF_NOT_BUILDING_DOC];
}

/** Attribution note shown next to the style-reference checklist group
 * (references default to unchecked — authors own the images). */
export const BRIEF_PRIVACY_NOTE =
  "Референсы стиля скрыты по умолчанию: изображения принадлежат их авторам. " +
  "Отметьте те, что можно показать дизайнеру.";

/** Results share button hint when no variant is selected or approved. */
export const BRIEF_SHARE_HINT = "Сначала выберите или утвердите вариант";

/** Composer rail labels. */
export const BRIEF_BACK_LABEL = "Назад к результатам";
export const BRIEF_DOWNLOAD_LABEL = "Скачать PDF";
export const BRIEF_NEEDS_LABEL = "Потребности и пожелания";
export const BRIEF_QUESTIONS_LABEL = "Вопросы для обсуждения";
export const BRIEF_CHECKLIST_TITLE = "Изображения в брифе";

/** Save indicator for the notes composer (autosave pattern, #198). */
export function briefNotesSaveIndicator(state: {
  saving: boolean;
  dirty: boolean;
}): string {
  if (state.saving) return "Сохранение…";
  if (state.dirty) return "Есть несохранённые правки";
  return "Сохранено";
}
