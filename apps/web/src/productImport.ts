// Pure helpers for the R3 product import flow: dimension normalization and
// the placement gate, the exact `add_object` command core for a placed
// product candidate, the PATCH payload builder for candidate corrections and
// the friendly ru label for URL-guard rejections. No React and no network so
// the module can be unit-tested in isolation (see
// tests/productImport.test.mjs). Structural input types keep this module free
// of the browser-only api.ts so it compiles with a bare `tsc` (same pattern
// as sceneIntent.ts / twinDesign.ts, whose uniqueId()/entityIdFromName() are
// reused for the fallback entity id).

// The ".js" suffix is required for the compiled ESM output (node --test
// imports ../build/*.js directly); Bundler resolution maps it back to .ts.
import { entityIdFromName, uniqueId } from "./twinDesign.js";

/** A dimension/price value as it arrives from the API, an extraction or a form field. */
export type DimensionValue = number | string | null | undefined;

/** Structural view of the three placement dimensions (mm). */
export type ProductDimsLike = {
  width_mm?: DimensionValue;
  depth_mm?: DimensionValue;
  height_mm?: DimensionValue;
};

export type NormalizedDims = {
  width_mm: number | null;
  depth_mm: number | null;
  height_mm: number | null;
};

/**
 * Minimal structural view of a product candidate for placement — everything
 * buildPlaceCommand reads, nothing more.
 */
export type ProductCandidateLike = {
  id: string;
  source_url?: string | null;
  source_asset_id?: string | null;
  title?: string | null;
  brand?: string | null;
  model?: string | null;
  price?: number | string | null;
  currency?: string | null;
  width_mm?: DimensionValue;
  depth_mm?: DimensionValue;
  height_mm?: DimensionValue;
  preview_asset_id?: string | null;
};

/**
 * Trim a free-form numeric value ("1200 мм", " 80,5 ", 1200) down to its
 * first number. Returns null for absent/garbage input — an unparseable field
 * means "missing", never 0.
 */
export function parseNumeric(value: DimensionValue): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const match = /-?\d+(?:[.,]\d+)?/.exec(value.replace(/\s+/g, ""));
  if (!match) return null;
  const parsed = Number.parseFloat(match[0].replace(",", "."));
  return Number.isFinite(parsed) ? parsed : null;
}

/** Parse the three placement dimensions, trimming non-numeric noise. */
export function normalizeDims(
  fact: ProductDimsLike | null | undefined
): NormalizedDims {
  return {
    width_mm: parseNumeric(fact?.width_mm),
    depth_mm: parseNumeric(fact?.depth_mm),
    height_mm: parseNumeric(fact?.height_mm)
  };
}

function allDimsPositive(dims: NormalizedDims): boolean {
  return (
    dims.width_mm !== null && dims.width_mm > 0 &&
    dims.depth_mm !== null && dims.depth_mm > 0 &&
    dims.height_mm !== null && dims.height_mm > 0
  );
}

/**
 * The placement gate: all three dimensions present (parseable) and > 0.
 * Accepts raw candidate/form values — strings are parsed, not trusted.
 */
export function dimsValid(dims: ProductDimsLike | null | undefined): boolean {
  return allDimsPositive(normalizeDims(dims));
}

/** The dimension field keys, in the canonical width → depth → height order. */
export type MissingDimField = "width_mm" | "depth_mm" | "height_mm";

/** Which of the three dimensions are missing (absent, unparseable or <= 0). */
export function missingFields(
  dims: ProductDimsLike | null | undefined
): MissingDimField[] {
  const normalized = normalizeDims(dims);
  const missing: MissingDimField[] = [];
  for (const key of ["width_mm", "depth_mm", "height_mm"] as const) {
    const value = normalized[key];
    if (value === null || value <= 0) missing.push(key);
  }
  return missing;
}

/** Exact `add_object` command core accepted by the backend. */
export type ProductPlaceCommand = {
  operation: "add_object";
  target_id: string;
  parameters: { entity: Record<string, unknown> };
};

/**
 * Build the exact `add_object` command core for a placed product candidate —
 * the same entity shape buildAddFurnitureCommand emits (the backend requires
 * `target_id === parameters.entity.id` and validates the entity against the
 * SceneEntity schema), with provenance source "imported" and the product keys
 * in entity.metadata. Only metadata keys that are actually present are
 * emitted; geometry is emitted only when all three dimensions are valid (the
 * UI gates placement on dimsValid, so this is a defensive fallback).
 * entityId may be omitted — a scene-safe id is then derived from the resolved
 * display name (same convention as TwinDesignPanel).
 */
export function buildPlaceCommand(
  candidate: ProductCandidateLike,
  entityId?: string
): ProductPlaceCommand {
  const title = typeof candidate.title === "string" ? candidate.title.trim() : "";
  const brand = typeof candidate.brand === "string" ? candidate.brand.trim() : "";
  const model = typeof candidate.model === "string" ? candidate.model.trim() : "";
  const displayName = title || [brand, model].filter(Boolean).join(" ") || "Товар";

  const resolvedId =
    typeof entityId === "string" && entityId.length > 0
      ? entityId
      : entityIdFromName(displayName, uniqueId().slice(0, 8));

  const currency = typeof candidate.currency === "string" ? candidate.currency.trim() : "";
  const previewAssetId =
    typeof candidate.preview_asset_id === "string" && candidate.preview_asset_id
      ? candidate.preview_asset_id
      : null;

  const metadata: Record<string, unknown> = { product_candidate_id: candidate.id };
  const sourceUrl =
    typeof candidate.source_url === "string" ? candidate.source_url.trim() : "";
  if (sourceUrl) metadata["source_url"] = sourceUrl;
  const price = parseNumeric(candidate.price);
  if (price !== null) metadata["price"] = price;
  if (currency) metadata["currency"] = currency;
  if (brand) metadata["brand"] = brand;
  if (model) metadata["model"] = model;
  if (previewAssetId) metadata["preview_asset_id"] = previewAssetId;

  const entity: Record<string, unknown> = {
    id: resolvedId,
    kind: "furniture",
    display_name: displayName,
    state: "design",
    transform: {
      translation_mm: [0, 0, 0],
      rotation_deg: [0, 0, 0],
      scale: [1, 1, 1]
    },
    locks: { geometry: false, transform: false, material: false },
    provenance: {
      source: "imported",
      asset_ids: [candidate.source_asset_id, previewAssetId].filter(
        (id): id is string => typeof id === "string" && id.length > 0
      ),
      note: "added from product import"
    },
    metadata
  };
  const dims = normalizeDims(candidate);
  if (allDimsPositive(dims)) {
    entity["geometry"] = {
      dimensions_mm: [dims.width_mm, dims.depth_mm, dims.height_mm]
    };
  }

  return {
    operation: "add_object",
    target_id: resolvedId,
    parameters: { entity }
  };
}

/** Raw review-form state: every field is an editable value ("" = not entered). */
export type ProductImportFormState = {
  title?: string | null;
  brand?: string | null;
  model?: string | null;
  price?: string | number | null;
  currency?: string | null;
  material?: string | null;
  color?: string | null;
  width_mm?: DimensionValue;
  depth_mm?: DimensionValue;
  height_mm?: DimensionValue;
};

/** Field keys the PATCH /products endpoint accepts (the server merges the patch). */
export type ProductPatch = Partial<{
  title: string;
  brand: string;
  model: string;
  price: number;
  currency: string;
  material_descriptors: string[];
  color_descriptors: string[];
  width_mm: number;
  depth_mm: number;
  height_mm: number;
}>;

function text(value: string | null | undefined): string {
  return typeof value === "string" ? value.trim() : "";
}

/** "дерево, лак" → ["дерево", "лак"]; empty/garbage → null (omit the key). */
function descriptors(value: string | null | undefined): string[] | null {
  const list = text(value)
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
  return list.length > 0 ? list : null;
}

/**
 * Build the PATCH payload from the review form: only entered fields are sent
 * (empty/null means "leave unchanged" — an explicit null is never emitted),
 * numbers are parsed from their raw strings, and unparseable/non-positive
 * dimensions are omitted so a stray keystroke cannot wipe a good value
 * server-side.
 */
export function buildPatchPayload(
  formState: ProductImportFormState
): ProductPatch {
  const patch: ProductPatch = {};
  const title = text(formState.title);
  if (title) patch.title = title;
  const brand = text(formState.brand);
  if (brand) patch.brand = brand;
  const model = text(formState.model);
  if (model) patch.model = model;
  const price = parseNumeric(formState.price);
  if (price !== null) patch.price = price;
  const currency = text(formState.currency);
  if (currency) patch.currency = currency;
  const material = descriptors(formState.material);
  if (material) patch.material_descriptors = material;
  const color = descriptors(formState.color);
  if (color) patch.color_descriptors = color;
  const dims = normalizeDims(formState);
  if (dims.width_mm !== null && dims.width_mm > 0) patch.width_mm = dims.width_mm;
  if (dims.depth_mm !== null && dims.depth_mm > 0) patch.depth_mm = dims.depth_mm;
  if (dims.height_mm !== null && dims.height_mm > 0) patch.height_mm = dims.height_mm;
  return patch;
}

/** Backend URL-guard rejection reason codes (closed taxonomy, guard.py) with
 * friendly ru labels; anything unrecognized falls back to the generic label. */
const REJECT_REASON_LABELS: Record<string, string> = {
  private_address: "Внутренние адреса запрещены",
  redirect_private_address: "Перенаправление на внутренний адрес запрещено",
  too_many_redirects: "Слишком много перенаправлений",
  dns_failed: "Не удалось определить адрес сайта",
  timeout: "Сайт не ответил вовремя",
  response_too_large: "Страница слишком большая",
  unsupported_content_type: "Неподдерживаемый тип содержимого"
};

const DEFAULT_REJECT_LABEL = "Ссылка отклонена";

/** Depth-first search for a string `reason` key (handles detail envelopes). */
function findRejectReason(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findRejectReason(item);
      if (found) return found;
    }
    return null;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const reason = record["reason"];
    if (typeof reason === "string") return reason;
    for (const child of Object.values(record)) {
      const found = findRejectReason(child);
      if (found) return found;
    }
  }
  return null;
}

/**
 * Friendly ru label for a failed importProductUrl() call. The backend answers
 * guard rejections with 422 {code:"url_rejected", reason} (wrapped by api.ts
 * into "<status>: <body>" like every other error); anything unrecognized —
 * including non-rejection failures — falls back to the generic label.
 */
export function rejectReasonLabel(reason: unknown): string {
  const message = reason instanceof Error ? reason.message : String(reason);
  const match = /^\d{3}:\s*([\s\S]*)$/.exec(message);
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(match ? match[1] : message);
  } catch {
    return DEFAULT_REJECT_LABEL;
  }
  const code = findRejectReason(parsed);
  return (code && REJECT_REASON_LABELS[code]) || DEFAULT_REJECT_LABEL;
}
