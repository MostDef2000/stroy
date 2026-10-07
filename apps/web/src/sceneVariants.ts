// Pure helpers for the R4 scene variants + variant budget: status labels and
// the exact transition matrix, list sorting/filtering, the variant-scoped
// command envelope, and the ru formatters for diffs and budget reports. No
// React and no network so the module can be unit-tested in isolation (see
// tests/sceneVariants.test.mjs). Structural input types keep this module free
// of the browser-only api.ts so it compiles with a bare `tsc` (same pattern
// as sceneIntent.ts / twinDesign.ts, whose uniqueId() is reused for the
// command id).

// The ".js" suffix is required for the compiled ESM output (node --test
// imports ../build/*.js directly); Bundler resolution maps it back to .ts.
import { uniqueId } from "./twinDesign.js";

/** The four R4 variant statuses, in lifecycle order. */
export type VariantStatus = "draft" | "shortlisted" | "approved" | "archived";

/** Human-readable status labels (Russian, per the R4 chip contract). */
export const STATUS_LABELS: Record<VariantStatus, string> = {
  draft: "Черновик",
  shortlisted: "Шорт-лист",
  approved: "Утверждён",
  archived: "В архиве"
};

/**
 * Client-side mirror of the backend status-transition matrix
 * (services/variants.py STATUS_TRANSITIONS). Exactly five legal moves:
 * draft→shortlisted; shortlisted→approved (the server auto-demotes any other
 * approved variant — there is no replace flag); draft→archived;
 * shortlisted→archived; approved→archived. Nothing else.
 */
export const VARIANT_TRANSITIONS: ReadonlyArray<
  readonly [VariantStatus, VariantStatus]
> = [
  ["draft", "shortlisted"],
  ["shortlisted", "approved"],
  ["draft", "archived"],
  ["shortlisted", "archived"],
  ["approved", "archived"]
];

const TRANSITION_KEYS: ReadonlySet<string> = new Set(
  VARIANT_TRANSITIONS.map(([from, to]) => `${from}->${to}`)
);

/** True only for the exact legal moves above; the server re-checks anyway. */
export function canTransition(
  from: VariantStatus,
  to: VariantStatus
): boolean {
  return TRANSITION_KEYS.has(`${from}->${to}`);
}

/**
 * Chip-row weight: approved first, then shortlisted, then drafts, archived
 * last. Small weight = earlier in the row.
 */
export const STATUS_WEIGHT: Record<VariantStatus, number> = {
  approved: 0,
  shortlisted: 1,
  draft: 2,
  archived: 3
};

/** Structural view of a variant for pure list helpers (api.ts adds the rest). */
export type VariantLike = {
  id: string;
  title: string;
  status: VariantStatus;
  created_at: string;
};

/**
 * Status-weighted sort helper for variant lists: approved first, then
 * shortlisted, drafts, archived last; newest created_at first within one
 * status (ISO strings compare lexically).
 *
 * NOTE (R4 FE): the Results chip row intentionally does NOT use this — it
 * sorts by created_at so the A/B/C chip letters stay stable as statuses
 * change. Use this helper where status-weighted order is wanted (pickers,
 * summary lists).
 */
export function variantSort(a: VariantLike, b: VariantLike): number {
  const weightDiff = STATUS_WEIGHT[a.status] - STATUS_WEIGHT[b.status];
  if (weightDiff !== 0) return weightDiff;
  return b.created_at.localeCompare(a.created_at);
}

/**
 * Archived variants are hidden by default (owner decision, R4). Returns a
 * filtered COPY — never mutates the polled list. Sorting stays the caller's
 * job (variantSort) so each helper does exactly one thing.
 */
export function visibleVariants<T extends VariantLike>(
  variants: readonly T[],
  includeArchived: boolean
): T[] {
  return variants.filter(
    (variant) => includeArchived || variant.status !== "archived"
  );
}

/** Command core accepted by the backend DesignCommand schema (operation part). */
export type VariantCommandCore = {
  operation: string;
  target_id: string;
  parameters: Record<string, unknown>;
};

/** Full DesignCommand envelope — field-for-field the shape DesignPage's
 * canonical dispatcher sends (schema_version 0.1.0, user origin). */
export type VariantCommandEnvelope = {
  schema_version: "0.1.0";
  command_id: string;
  base_revision_id: string;
  operation: string;
  target_id: string;
  parameters: Record<string, unknown>;
  reference_asset_ids: string[];
  origin: "user";
  request_text: null;
};

/**
 * Wrap a command core into the full DesignCommand envelope for
 * POST /variants/{id}/revisions. Mirrors the canonical envelope conventions
 * exactly: unique UUID command id, fresh base revision supplied by the caller
 * (the variant head the user observed), empty reference list unless the
 * command carries assets, user origin, null request_text.
 */
export function buildVariantCommandEnvelope(
  command: VariantCommandCore,
  baseRevisionId: string,
  referenceAssetIds: string[] = []
): VariantCommandEnvelope {
  return {
    schema_version: "0.1.0",
    command_id: uniqueId(),
    base_revision_id: baseRevisionId,
    operation: command.operation,
    target_id: command.target_id,
    parameters: command.parameters,
    reference_asset_ids: referenceAssetIds,
    origin: "user",
    request_text: null
  };
}

/** Default currency for manually added budget items (owner decision, R4). */
export const DEFAULT_BUDGET_CURRENCY = "RUB";

/** Ru labels for the budget item kinds the backend ships (budget.py). */
export const BUDGET_KIND_LABELS: Record<string, string> = {
  manual: "Вручную",
  material: "Материалы",
  candidate: "Товары"
};

export function budgetKindLabel(kind: string): string {
  return BUDGET_KIND_LABELS[kind] ?? kind;
}

/**
 * Format an amount in the ru convention: regular-space thousands grouping,
 * comma decimals (dropped when whole), "₽" for RUB and the raw code otherwise.
 * Manual grouping (not Intl) keeps the output identical across ICU versions.
 */
export function formatMoney(amount: number, currency: string = DEFAULT_BUDGET_CURRENCY): string {
  const sign = amount < 0 ? "-" : "";
  const rounded = Math.round(Math.abs(amount) * 100) / 100;
  const [intPart, decPart] = rounded.toFixed(2).split(".");
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  const decimals = decPart === "00" ? "" : `,${decPart}`;
  const symbol = currency === "RUB" ? " ₽" : ` ${currency}`;
  return `${sign}${grouped}${decimals}${symbol}`;
}

/** Headline money line for the budget card (grand total of the report). The
 * report's single currency is used when known; ₽ is the RUB-default fallback
 * (also for the mixed/unknown-currency case, where unknowns flag it). */
export function budgetFormat(report: {
  totals: { grand_total: number };
  currency?: string | null;
}): string {
  return formatMoney(report.totals.grand_total, report.currency ?? DEFAULT_BUDGET_CURRENCY);
}

/** Backend unknown fact codes → ru phrases (order of the report is kept). */
const UNKNOWN_LABELS: Record<string, string> = {
  missing_price: "нет цены",
  missing_quantity: "нет количества",
  missing_currency: "нет валюты",
  mixed_currency: "несколько валют"
};

/**
 * «нет цены, нет количества» — ru list of what makes a budget report
 * incomplete. Unknown codes fall back to their raw value (defensive).
 */
export function incompleteLabel(unknowns: readonly string[]): string {
  return unknowns
    .map((code) => UNKNOWN_LABELS[code] ?? code)
    .join(", ");
}

/** Structural view of the comparison payload (api.ts carries the full type). */
export type VariantDiffLike = {
  entities: {
    added: readonly string[];
    removed: readonly string[];
    modified: ReadonlyArray<{ id: string }>;
  };
  materials: { added: readonly string[]; removed: readonly string[] };
  validation: { added: readonly unknown[]; resolved: readonly unknown[] };
  renders: { left: readonly unknown[]; right: readonly unknown[] };
};

/**
 * One-line ru summary of a variant diff (counts only; the detail sections
 * render the ids/changes themselves).
 */
export function diffSummary(diff: VariantDiffLike): string {
  const parts = [
    `Объекты: +${diff.entities.added.length} -${diff.entities.removed.length} ~${diff.entities.modified.length}`,
    `Материалы: +${diff.materials.added.length} -${diff.materials.removed.length}`,
    `Проверки: +${diff.validation.added.length} -${diff.validation.resolved.length}`,
    `Рендеры: ${diff.renders.left.length} / ${diff.renders.right.length}`
  ];
  return parts.join(" · ");
}

/** Structural view of a revision row for lineage walking (api.ts adds the rest). */
export type RevisionNodeLike = {
  revision_id: string;
  parent_revision_id?: string | null;
};

/**
 * The variant's lineage as a head-first list: walk parent_revision_id from
 * the variant head down to (and including) the variant base. The project
 * revisions endpoint lists every revision of the project (variant revisions
 * included), so the chain is reconstructed client-side — there is no lineage
 * endpoint. Unknown ids stop the walk defensively; a cycle guard keeps a
 * corrupt parent chain from looping forever.
 */
export function lineageRevisions<
  T extends RevisionNodeLike
>(
  headRevisionId: string,
  baseRevisionId: string,
  revisions: readonly T[]
): T[] {
  const byId = new Map(revisions.map((node) => [node.revision_id, node]));
  const chain: T[] = [];
  const visited = new Set<string>();
  let cursor: string | null = headRevisionId;
  while (cursor && !visited.has(cursor)) {
    visited.add(cursor);
    const node = byId.get(cursor);
    if (!node) break;
    chain.push(node);
    if (cursor === baseRevisionId) break;
    cursor = node.parent_revision_id ?? null;
  }
  return chain;
}

/**
 * Budget line for the comparison view: signed delta of the grand totals, or
 * «неполные данные» when either side is incomplete (delta is null then — the
 * backend never compares partial money). `currency` comes from the compared
 * sides' report currency; ₽ remains the RUB-default fallback when null.
 */
export function budgetDeltaLabel(
  budget: {
    delta: { grand_total: number } | null;
  },
  currency?: string | null
): string {
  if (!budget.delta) return "неполные данные";
  const delta = budget.delta.grand_total;
  const sign = delta > 0 ? "+" : "";
  return `Δ ${sign}${formatMoney(delta, currency ?? DEFAULT_BUDGET_CURRENCY)}`;
}
