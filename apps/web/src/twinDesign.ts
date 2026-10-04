// Pure helpers for the Twin design panel: render-manifest formatting, camera
// dropdown labels and the exact add_object scene-command payload. No React and
// no network so the module can be unit-tested in isolation (see
// tests/twinDesign.test.mjs). Structural input types keep this module free of
// the browser-only api.ts so it compiles with a bare `tsc`.

export type CameraLike = {
  id: string;
  calibration?: {
    method?: string | null;
    residual?: number | null;
  } | null;
};

export type ManifestLike = {
  renderer_profile?: string | null;
  /** Not part of the v0 schema; tolerated if an experimental worker reports it. */
  wall_seconds?: number | null;
  passes?: Record<string, string> | null;
};

export type RenderLike = {
  id: string;
  created_at: string;
};

/** Backend-accepted redesign strength window (RedesignRequest, routes.py). */
export const REDESIGN_STRENGTH_MIN = 0.2;
export const REDESIGN_STRENGTH_MAX = 0.95;
export const REDESIGN_STRENGTH_DEFAULT = 0.6;

/** Minimal structural view of a project asset for reference filtering. */
export type AssetLike = {
  id: string;
  role: string;
  media_type: string;
  original_name?: string | null;
};

/** Minimal structural view of a job for result-asset resolution. */
export type JobLike = {
  status?: string | null;
  result?: Record<string, unknown> | null;
};

export type DesignVariantDraft = {
  baseRevisionId: string;
  baseAssetId: string;
  prompt: string;
  strength: number;
  referenceAssetId?: string | null;
};

export type RedesignPayload = {
  base_revision_id: string;
  base_asset_id: string;
  prompt: string;
  strength: number;
  reference_asset_id?: string;
};

export type AddFurnitureInput = {
  commandId: string;
  baseRevisionId: string;
  entityId: string;
  label: string;
  roomId?: string | null;
  /** [width, depth, height] in millimetres. */
  dimensionsMm: [number, number, number];
  /** Canonical +Z-up world position in millimetres. */
  positionMm: [number, number, number];
  rotationZdeg: number;
  color?: string | null;
  materialRef?: string | null;
};

export type DesignCommandPayload = {
  schema_version: "0.1.0";
  command_id: string;
  base_revision_id: string;
  operation: "add_object";
  target_id: string;
  parameters: { entity: Record<string, unknown> };
  reference_asset_ids: string[];
  origin: "user";
  request_text: null;
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * The rgb pass asset id from a render manifest (`passes.rgb`), or null when the
 * manifest is absent or has no usable rgb pass.
 */
export function renderRgbAssetId(
  manifest: ManifestLike | null | undefined
): string | null {
  const id = manifest?.passes?.["rgb"];
  return typeof id === "string" && id.length > 0 ? id : null;
}

/**
 * Clamp a redesign strength into the backend window [0.2, 0.95]; non-finite
 * input falls back to the panel default so a stray empty field cannot 422.
 */
export function clampStrength(value: number): number {
  if (!Number.isFinite(value)) return REDESIGN_STRENGTH_DEFAULT;
  return Math.min(REDESIGN_STRENGTH_MAX, Math.max(REDESIGN_STRENGTH_MIN, value));
}

/**
 * Build the POST /redesigns body from the panel draft. The prompt is trimmed
 * and the strength clamped. The optional style reference is omitted entirely
 * (not nulled) when absent so the backend takes its prompt-only placeholder
 * path (`reference_asset_id=None` -> synthetic black reference).
 */
export function buildRedesignInput(draft: DesignVariantDraft): RedesignPayload {
  const payload: RedesignPayload = {
    base_revision_id: draft.baseRevisionId,
    base_asset_id: draft.baseAssetId,
    prompt: draft.prompt.trim(),
    strength: clampStrength(draft.strength)
  };
  if (typeof draft.referenceAssetId === "string" && draft.referenceAssetId.length > 0) {
    payload.reference_asset_id = draft.referenceAssetId;
  }
  return payload;
}

function firstUsableAssetId(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  for (const item of value) {
    if (typeof item === "string" && item.length > 0) return item;
  }
  return null;
}

/**
 * Resolve the result asset of a finished redesign job. The worker uploads
 * outputs into top-level `result.output_asset_ids` (worker/runtime.py) and
 * mirrors them in `result.generation_manifest.output_asset_ids`; read the first
 * usable id. Returns null while the job is queued, running or has no output.
 */
export function redesignResultAssetId(
  job: JobLike | null | undefined
): string | null {
  const direct = firstUsableAssetId(job?.result?.["output_asset_ids"]);
  if (direct) return direct;
  const manifest = job?.result?.["generation_manifest"];
  if (manifest && typeof manifest === "object") {
    return firstUsableAssetId((manifest as Record<string, unknown>)["output_asset_ids"]);
  }
  return null;
}

/**
 * Project assets eligible as a redesign style reference — an image with
 * role=reference, mirroring PhotoEditPanel's reference picker.
 */
export function referenceImageAssets<T extends AssetLike>(
  assets: readonly T[]
): T[] {
  return assets.filter(
    (asset) => asset.role === "reference" && asset.media_type.startsWith("image/")
  );
}

/**
 * One-line render summary: "<renderer_profile> · <wall_seconds> s". The wall
 * clock is only shown when the worker reported a finite `wall_seconds`.
 */
export function formatRenderSummary(
  manifest: ManifestLike | null | undefined
): string {
  if (!manifest) return "no manifest";
  const profile =
    typeof manifest.renderer_profile === "string" && manifest.renderer_profile
      ? manifest.renderer_profile
      : "unknown profile";
  if (isFiniteNumber(manifest.wall_seconds)) {
    return `${profile} · ${manifest.wall_seconds.toFixed(1)} s`;
  }
  return profile;
}

/**
 * Camera dropdown label. Calibrated cameras surface their calibration method
 * and residual so the user can tell measured poses apart from defaults.
 */
export function cameraOptionLabel(camera: CameraLike): string {
  const calibration = camera.calibration;
  if (!calibration) return `${camera.id} (uncalibrated)`;
  const parts: string[] = [];
  if (typeof calibration.method === "string" && calibration.method) {
    parts.push(calibration.method);
  }
  if (isFiniteNumber(calibration.residual)) {
    parts.push(`residual ${calibration.residual.toFixed(2)} px`);
  }
  if (parts.length === 0) return `${camera.id} (uncalibrated)`;
  return `${camera.id} · ${parts.join(" · ")}`;
}

/** Newest-first copy of a render list (ISO `created_at` strings compare lexically). */
export function sortedRendersNewestFirst<T extends RenderLike>(
  renders: readonly T[]
): T[] {
  return [...renders].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/**
 * Slugify a human label into a scene entity id. Non-ASCII labels (e.g. Russian)
 * fall back to "furniture" so the id stays a valid scene key.
 */
export function entityIdFromName(name: string, suffix: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const safeSuffix = suffix.replace(/[^a-z0-9]+/gi, "") || "0";
  return `object.${slug || "furniture"}.${safeSuffix}`;
}

function uuidFromBytes(bytes: Uint8Array): string {
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex: string[] = [];
  for (const byte of bytes) hex.push(byte.toString(16).padStart(2, "0"));
  return (
    `${hex.slice(0, 4).join("")}-` +
    `${hex.slice(4, 6).join("")}-` +
    `${hex.slice(6, 8).join("")}-` +
    `${hex.slice(8, 10).join("")}-` +
    `${hex.slice(10, 16).join("")}`
  );
}

/**
 * Project-convention command id: always a valid UUID v4 string with no prefix
 * or post-processing. Prefers WebCrypto randomUUID; otherwise builds a v4 from
 * getRandomValues (version/variant bits set), with a Math.random last resort.
 */
export function uniqueId(): string {
  const webCrypto = typeof crypto !== "undefined" ? crypto : null;
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (webCrypto && typeof webCrypto.getRandomValues === "function") {
    webCrypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  return uuidFromBytes(bytes);
}

/**
 * Build the authoritative `add_object` DesignCommand payload. The backend
 * requires `target_id === parameters.entity.id` and validates the entity
 * against the SceneEntity schema, so only schema-known fields are emitted.
 */
export function buildAddFurnitureCommand(
  input: AddFurnitureInput
): DesignCommandPayload {
  const entity: Record<string, unknown> = {
    id: input.entityId,
    kind: "furniture",
    display_name: input.label,
    transform: {
      translation_mm: input.positionMm,
      rotation_deg: [0, 0, input.rotationZdeg],
      scale: [1, 1, 1]
    },
    geometry: { dimensions_mm: input.dimensionsMm },
    locks: { geometry: false, transform: false, material: false },
    provenance: {
      source: "user",
      asset_ids: [],
      note: "added from twin design panel"
    }
  };
  if (input.roomId) entity["room_id"] = input.roomId;
  if (input.materialRef) entity["material_ref"] = input.materialRef;
  if (input.color) entity["metadata"] = { color: input.color };

  return {
    schema_version: "0.1.0",
    command_id: input.commandId,
    base_revision_id: input.baseRevisionId,
    operation: "add_object",
    target_id: input.entityId,
    parameters: { entity },
    reference_asset_ids: [],
    origin: "user",
    request_text: null
  };
}

/**
 * Mirrors the api.ts request helper: API errors are "<status>: <body>". Unwrap
 * the backend's {"detail": {"code", "detail"}} envelope for inline display.
 */
export function apiErrorText(reason: unknown): string {
  const text = reason instanceof Error ? reason.message : String(reason);
  const match = /^(\d{3}):\s*([\s\S]*)$/.exec(text);
  if (!match) return text;
  try {
    const body = JSON.parse(match[2]) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const record = detail as Record<string, unknown>;
      const code = typeof record.code === "string" ? record.code : null;
      const message =
        typeof record.detail === "string" ? record.detail : JSON.stringify(detail);
      return code ? `${code}: ${message}` : message;
    }
  } catch {
    /* fall through to the raw body */
  }
  return `${match[1]}: ${match[2]}`;
}
