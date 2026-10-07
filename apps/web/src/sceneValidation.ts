// Pure helpers for the R2 design-check report: grouping by severity, the
// compact top-list for the rail, the human summary line and short rule
// labels. No React and no network so the module can be unit-tested in
// isolation (see tests/sceneValidation.test.mjs). Structural input types
// keep this module free of the browser-only api.ts (same pattern as
// sceneLayers.ts — the api.ts types stay structurally compatible).

export type CheckSeverity = "info" | "warning" | "error";

export type CheckResult = {
  severity: CheckSeverity;
  entity_ids: string[];
  rule_id: string;
  measured_mm?: number | null;
  expected_min_mm?: number | null;
  explanation: string;
  suggestion?: string | null;
};

export type ValidationReport = {
  schema_version: string;
  scene_revision_id: string;
  scene_content_hash: string;
  config: { min_walkway_mm?: number };
  summary: { info: number; warning: number; error: number };
  results: CheckResult[];
};

/** Severity buckets in display order (most severe first). */
export const CHECK_SEVERITIES: readonly CheckSeverity[] = [
  "error",
  "warning",
  "info"
];

function toSeverity(value: unknown): CheckSeverity | null {
  return value === "info" || value === "warning" || value === "error"
    ? value
    : null;
}

/**
 * Group results into severity buckets, preserving the server order inside
 * each bucket. Entries with an unknown severity cannot be displayed and are
 * dropped defensively.
 */
export function groupResults(
  results: readonly CheckResult[]
): Record<CheckSeverity, CheckResult[]> {
  const grouped: Record<CheckSeverity, CheckResult[]> = {
    error: [],
    warning: [],
    info: []
  };
  for (const result of results) {
    const severity = toSeverity(result?.severity);
    if (severity) grouped[severity].push(result);
  }
  return grouped;
}

/**
 * First n results in severity order (error → warning → info), server order
 * preserved inside each severity. Feeds the compact rail card.
 */
export function topResults(report: ValidationReport, n = 3): CheckResult[] {
  const grouped = groupResults(report.results);
  return [...grouped.error, ...grouped.warning, ...grouped.info].slice(
    0,
    Math.max(0, n)
  );
}

/** Russian plural forms for [1, 2–4, 5+], selected by the standard rules. */
function pluralRu(count: number, forms: [string, string, string]): string {
  const mod100 = Math.abs(count) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return forms[2];
  if (mod10 === 1) return forms[0];
  if (mod10 >= 2 && mod10 <= 4) return forms[1];
  return forms[2];
}

const SEVERITY_NOUNS: Record<CheckSeverity, [string, string, string]> = {
  error: ["ошибка", "ошибки", "ошибок"],
  warning: ["предупреждение", "предупреждения", "предупреждений"],
  info: ["замечание", "замечания", "замечаний"]
};

/**
 * Human summary line, e.g. "2 ошибки · 3 предупреждения" (severity order).
 * Zero groups are omitted; a fully clean report reads "Проблем не найдено".
 */
export function summaryLine(report: ValidationReport): string {
  const parts: string[] = [];
  for (const severity of CHECK_SEVERITIES) {
    const count = report?.summary?.[severity] ?? 0;
    if (count > 0) {
      parts.push(`${count} ${pluralRu(count, SEVERITY_NOUNS[severity])}`);
    }
  }
  return parts.length > 0 ? parts.join(" · ") : "Проблем не найдено";
}

/** Short human labels for the v1 rule ids; unknown ids pass through as-is. */
const RULE_LABELS: Record<string, string> = {
  "object.object_collision": "Столкновение объектов",
  "object.wall_collision": "Пересечение со стеной",
  "object.outside_room_bounds": "Вне комнаты",
  "opening.blocked": "Перекрыт проём",
  "clearance.walkway_min": "Узкий проход",
  "envelope.door_swing": "Мешает открытию",
  "envelope.door_swing_unchecked": "Мешает открытию",
  "envelope.cabinet_opening": "Мешает открытию",
  "envelope.chair_pullout": "Нет доступа",
  "envelope.bed_access": "Нет доступа",
  "envelope.sofa_access": "Нет доступа"
};

export function ruleLabel(ruleId: string): string {
  return RULE_LABELS[ruleId] ?? ruleId;
}
