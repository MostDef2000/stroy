// Pure timeline model for the Results page (#107).
//
// Note: instruction TEXT is not available — the backend `job_view` does not expose
// `job.payload`, so primary labels are derived from humanized command operations.

export interface TimelineAction {
  kind: "action";
  id: string;
  createdAt: string;
  label: string;
  status: string;
  errorText: string | null;
  finalRevisionId: string | null;
  operations: string[];
}

export interface TimelineResult {
  kind: "result";
  id: string;
  createdAt: string;
  label: string;
  outputAssetId: string | null;
  designRevisionId: string;
}

export type TimelineEntry = TimelineAction | TimelineResult;

export interface JobLike {
  id: string;
  created_at: string;
  status: string;
  job_type: string;
  result: {
    commands?: Array<{ operation?: string; target_id?: string }>;
    final_revision_id?: string;
  } | null;
  error: unknown;
}

export interface GenLike {
  id: string;
  created_at: string;
  design_revision_id: string;
  manifest?: { output_asset_ids?: string[] } | null;
}

// Operation → human-readable Russian phrase. `null` means "display nothing"
// (the operation is an internal bookkeeping step, not a scene change).
const OPERATION_LABELS: Record<string, string | null> = {
  add_object: "добавление объекта",
  move_object: "перемещение",
  remove_object: "удаление объекта",
  set_color: "смена цвета",
  set_material: "смена материала",
  replace_object_from_reference: "замена по референсу",
  set_light_intent: "настройка света",
  create_design_revision: null
};

export function humanizeOperations(operations: string[]): string {
  const parts: string[] = [];
  for (const raw of operations ?? []) {
    if (typeof raw !== "string" || !raw) continue;
    if (Object.prototype.hasOwnProperty.call(OPERATION_LABELS, raw)) {
      const mapped = OPERATION_LABELS[raw];
      if (mapped) parts.push(mapped);
      continue;
    }
    const humanized = raw.replace(/[_.]+/g, " ").trim();
    if (humanized) parts.push(humanized);
  }
  if (parts.length === 0) return "изменение сцены";
  return parts.join(" · ");
}

function extractOperations(result: JobLike["result"]): string[] {
  const commands = result && Array.isArray(result.commands) ? result.commands : [];
  const operations: string[] = [];
  for (const command of commands) {
    const operation = command?.operation;
    if (typeof operation === "string" && operation) operations.push(operation);
  }
  return operations;
}

function jobErrorText(error: unknown): string | null {
  if (!error) return null;
  const record =
    typeof error === "object" && error !== null ? (error as Record<string, unknown>) : null;
  const code = record && typeof record["code"] === "string" ? record["code"] : "job_failed";
  const detail =
    record && typeof record["detail"] === "string" ? record["detail"] : JSON.stringify(error);
  return `${code}: ${detail}`;
}

export function buildTimelineEntries(input: {
  jobs: JobLike[];
  generations: GenLike[];
  currentRevisionId?: string | null;
}): TimelineEntry[] {
  const { jobs, generations } = input;

  const actions: TimelineAction[] = jobs
    .filter(
      (job) =>
        job.job_type === "llm.complete" &&
        (job.result?.["commands"] != null || job.error != null)
    )
    .map((job) => {
      const operations = extractOperations(job.result);
      return {
        kind: "action" as const,
        id: job.id,
        createdAt: job.created_at,
        label: humanizeOperations(operations),
        status: job.status,
        errorText: jobErrorText(job.error),
        finalRevisionId: job.result?.final_revision_id ?? null,
        operations
      };
    });

  const results: TimelineResult[] = generations.map((generation) => ({
    kind: "result" as const,
    id: generation.id,
    createdAt: generation.created_at,
    label: "Рендер версии",
    outputAssetId: generation.manifest?.output_asset_ids?.[0] ?? null,
    designRevisionId: generation.design_revision_id
  }));

  return [...actions, ...results].sort((a, b) =>
    a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : 0
  );
}

function dayKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function groupEntriesByDay(
  entries: TimelineEntry[],
  now: Date = new Date()
): Array<{ key: string; label: string; entries: TimelineEntry[] }> {
  const today = dayKey(now);
  const yesterdayDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const yesterday = dayKey(yesterdayDate);

  const groups = new Map<string, TimelineEntry[]>();
  for (const entry of entries) {
    const key = dayKey(new Date(entry.createdAt));
    const bucket = groups.get(key);
    if (bucket) bucket.push(entry);
    else groups.set(key, [entry]);
  }

  return [...groups.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0))
    .map(([key, dayEntries]) => {
      let label: string;
      if (key === today) {
        label = "Сегодня";
      } else if (key === yesterday) {
        label = "Вчера";
      } else {
        const [year, month, day] = key.split("-").map(Number);
        const date = new Date(year, month - 1, day);
        label = date.toLocaleDateString(
          "ru-RU",
          year === now.getFullYear()
            ? { day: "numeric", month: "long" }
            : { day: "numeric", month: "long", year: "numeric" }
        );
      }
      return { key, label, entries: dayEntries };
    });
}

export function comparePairFor(
  generations: GenLike[],
  afterId: string
): { beforeId: string | null } {
  const sorted = [...generations].sort((a, b) =>
    a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0
  );
  const index = sorted.findIndex((generation) => generation.id === afterId);
  if (index === -1) return { beforeId: null };
  const before = sorted[index + 1];
  return { beforeId: before ? before.id : null };
}
