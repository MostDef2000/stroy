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
  opening: "проём"
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
