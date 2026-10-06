// Pure formatting helpers for the compact diagnostics dashboard (#143).
//
// No React/api imports: structural types only, so the module compiles
// standalone for node --test (same pattern as copy.ts / resultsTimeline.ts).

/** Everything display-sorted in the dashboard is ordered newest-first. */
type HasCreatedAt = { created_at: string };

/** First 8 chars of an id for compact rows; "—" when missing. */
export function shortId(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : "—";
}

/** Stable newest-first copy (created_at desc; Array#sort is stable, so equal timestamps keep input order). */
export function newestFirst<T extends HasCreatedAt>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
}

/** HH:MM local clock for compact row timestamps; "—" when unparsable. */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

/** "DD.MM HH:MM" local stamp for rows that can be older than today; "—" when unparsable. */
export function formatDayClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `${day}.${month} ${formatClock(iso)}`;
}

/** "5 мин назад"-style relative time. Reserved for the worker telemetry slot (#144). */
export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.max(0, Math.round((now.getTime() - date.getTime()) / 1000));
  if (seconds < 60) return "только что";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.round(hours / 24);
  return `${days} дн назад`;
}

/** Compact byte size: 856 B / 12.4 KB / 3.2 MB / 1.1 GB. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = "B";
  for (const next of units) {
    value /= 1024;
    unit = next;
    if (value < 1024) break;
  }
  const rounded = value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${unit}`;
}

/**
 * Compact live-progress text from job.progress ("render · 42%").
 * Returns null when the job carries no phase/fraction yet.
 */
export function jobProgressText(progress: Record<string, unknown>): string | null {
  const fraction =
    typeof progress["fraction"] === "number" ? Math.round((progress["fraction"] as number) * 100) : null;
  const phase = typeof progress["phase"] === "string" ? (progress["phase"] as string) : null;
  if (phase && fraction !== null) return `${phase} · ${fraction}%`;
  if (phase) return phase;
  if (fraction !== null) return `${fraction}%`;
  return null;
}

/** Row subtitle per the #143 example: "#20a37dcd · попытка 1". */
export function formatJobSubtitle(job: { id: string; attempt: number }): string {
  return `#${shortId(job.id)} · попытка ${job.attempt}`;
}

/**
 * Terminal statuses for which cancellation is meaningless. Everything else
 * (queued/pending/running/leased/…) keeps its "Отменить" button.
 */
export function isJobCancellable(status: string): boolean {
  return !["succeeded", "failed", "cancelled"].includes(status);
}

/** Semantic tone for the status tag: ok/err/warn, null → default processing. */
export function jobStatusTone(status: string): "ok" | "err" | "warn" | null {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "err";
  if (status === "cancelled") return "warn";
  return null;
}

/** "code: detail" one-liner for failed jobs; null when the job has no error. */
export function jobErrorText(error: unknown): string | null {
  if (!error) return null;
  const record =
    typeof error === "object" && error !== null ? (error as Record<string, unknown>) : null;
  const code = record && typeof record["code"] === "string" ? record["code"] : "job_failed";
  const detail =
    record && typeof record["detail"] === "string" ? record["detail"] : JSON.stringify(error);
  return `${code}: ${detail}`;
}

/** Single-line JSON for <details> blocks; "—" for null/undefined. */
export function compactJson(value: unknown): string {
  if (value === null || value === undefined) return "—";
  try {
    return JSON.stringify(value);
  } catch {
    return "—";
  }
}
