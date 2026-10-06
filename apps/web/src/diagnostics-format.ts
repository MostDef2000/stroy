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

// ---------------------------------------------------------------------------
// Worker telemetry display (#144).
//
// CRITICAL INVARIANT: never synthesize or estimate a value. Anything the
// worker could not report is skipped or surfaced as explicitly unavailable
// («Метрики недоступны» / «Метрики устарели») — absence is never rendered
// as zero, a dash-padded number, or an empty placeholder.
// ---------------------------------------------------------------------------

/** Telemetry older than this is shown as stale, not live. */
export const TELEMETRY_STALE_SECONDS = 90;

/** Structural slice of the API Worker needed for the status tag (#144).
 *  Fields optional so old server payloads (missing keys) never crash:
 *  missing reads as false/null defensively. */
export type WorkerStatusInput = {
  online?: boolean | null;
  busy?: boolean | null;
  current_job?: { job_type?: string | null } | null;
};

/** Status tag + optional secondary line for a worker card. */
export type WorkerStatusView = {
  /** Tag text: "online" | "offline" | "● ЗАНЯТ". */
  label: string;
  /** CSS class suffix on .tag: online/offline are global, warn is #143 diag tone. */
  tone: "online" | "offline" | "warn";
  /** Secondary line: «Сейчас: <job_type>» when busy, «Свободен» when idle. */
  note: string | null;
};

export function workerStatusView(worker: WorkerStatusInput): WorkerStatusView {
  if (!worker.online) return { label: "offline", tone: "offline", note: null };
  if (worker.busy) {
    const job = worker.current_job ?? null;
    const jobType =
      job && typeof job.job_type === "string" && job.job_type ? job.job_type : null;
    return {
      label: "● ЗАНЯТ",
      tone: "warn",
      note: jobType ? `Сейчас: ${jobType}` : null
    };
  }
  return { label: "online", tone: "online", note: "Свободен" };
}

/** Structural slice of the API Worker needed for telemetry lines (#144). */
export type WorkerTelemetryInput = {
  online?: boolean | null;
  telemetry?: {
    cpu?: { utilization_percent?: number | null } | null;
    memory?: { used_bytes?: number | null; total_bytes?: number | null } | null;
    gpus?: Array<{
      name?: string | null;
      utilization_percent?: number | null;
      memory_used_bytes?: number | null;
      memory_total_bytes?: number | null;
    }> | null;
  } | null;
  telemetry_updated_at?: string | null;
};

/**
 * Parse-safe staleness check: unparsable/missing timestamps count as stale
 * (they cannot prove freshness); strictly older than 90s is stale, so an
 * exactly-90s-old sample is still fresh. Future timestamps (clock skew)
 * are treated as fresh, never synthesized.
 */
export function isTelemetryStale(
  updatedAt: string | null | undefined,
  nowMs: number
): boolean {
  if (!updatedAt) return true;
  const date = new Date(updatedAt);
  if (Number.isNaN(date.getTime())) return true;
  return (nowMs - date.getTime()) / 1000 > TELEMETRY_STALE_SECONDS;
}

/** Real finite number or null — guards non-numbers and NaN/Infinity. */
function finiteNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Memory pairs render only complete: <used>/<total> — half a pair is skipped. */
function memoryPairLine(
  used: unknown,
  total: unknown
): string | null {
  const usedBytes = finiteNumberOrNull(used);
  const totalBytes = finiteNumberOrNull(total);
  if (usedBytes === null || totalBytes === null) return null;
  return `${formatBytes(usedBytes)} / ${formatBytes(totalBytes)}`;
}

/**
 * Display lines for a worker card's telemetry block, or the explicit
 * unavailable/stale copies when live values cannot be shown:
 * - offline → «Метрики недоступны» (offline trumps any stored sample);
 * - online without telemetry/timestamp → «Метрики недоступны»;
 * - online stale (>90s) → «Метрики устарели» + relative time;
 * - online fresh → present metrics only (missing metrics are skipped
 *   silently), plus a relative "Последняя телеметрия" line; a sample with
 *   zero present metrics is itself unavailable.
 */
export function telemetryLines(
  worker: WorkerTelemetryInput,
  nowMs: number
): string[] {
  if (!worker.online) return ["Метрики недоступны"];
  const telemetry = worker.telemetry ?? null;
  const updatedAt =
    typeof worker.telemetry_updated_at === "string" && worker.telemetry_updated_at
      ? worker.telemetry_updated_at
      : null;
  if (!telemetry || !updatedAt) return ["Метрики недоступны"];
  if (isTelemetryStale(updatedAt, nowMs)) {
    return [
      "Метрики устарели",
      `Последняя телеметрия: ${formatRelativeTime(updatedAt, new Date(nowMs))}`
    ];
  }

  const lines: string[] = [];
  const cpuPercent = finiteNumberOrNull(telemetry.cpu?.utilization_percent);
  if (cpuPercent !== null) lines.push(`CPU ${Math.round(cpuPercent)}%`);

  const memory = telemetry.memory ?? null;
  const ramLine = memory ? memoryPairLine(memory.used_bytes, memory.total_bytes) : null;
  if (ramLine) lines.push(`RAM ${ramLine}`);

  const gpus = Array.isArray(telemetry.gpus) ? telemetry.gpus : [];
  for (const gpu of gpus) {
    const name = gpu && typeof gpu.name === "string" && gpu.name ? gpu.name : "GPU";
    const parts: string[] = [];
    const utilPercent = finiteNumberOrNull(gpu?.utilization_percent);
    if (utilPercent !== null) parts.push(`${Math.round(utilPercent)}%`);
    const vramLine = gpu ? memoryPairLine(gpu.memory_used_bytes, gpu.memory_total_bytes) : null;
    if (vramLine) parts.push(vramLine);
    // CPU-only workers report gpus: [] — no GPU line, not an error.
    if (parts.length > 0) lines.push(`${name} ${parts.join(" · ")}`);
  }

  if (lines.length === 0) return ["Метрики недоступны"];
  lines.push(`Последняя телеметрия: ${formatRelativeTime(updatedAt, new Date(nowMs))}`);
  return lines;
}
