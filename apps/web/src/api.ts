import {
  type Attachment,
  type AttachmentFilters,
  type AttachmentKind,
  type AttachmentPatch,
  type AttachmentPostBody,
  type AttachmentTargetType
} from "./attachments";
import { deleteOutcomeFromStatus, type ProjectDeleteOutcome } from "./projectDelete";
import type { ProductPatch } from "./productImport";
import {
  unwrapValidationReport,
  type ValidationReportEnvelope
} from "./sceneValidation";
import type { SetupStatus } from "./setup";

// Re-exported so UI code can unwrap validation envelopes without importing
// the pure module directly (api.ts stays the single browser-side surface).
export { unwrapValidationReport } from "./sceneValidation";
export type { ValidationReportEnvelope } from "./sceneValidation";

// R6 setup: the wire type lives in the pure setup module (unit-tested without
// a browser); api.ts re-exports it so UI code keeps a single import surface.
export type { SetupStatus } from "./setup";

// Re-exported so UI code can keep importing attachment types from api.ts.
export type {
  Attachment,
  AttachmentFilters,
  AttachmentKind,
  AttachmentPatch,
  AttachmentPostBody,
  AttachmentTargetType
};

// R7 (#184): photo mapping metadata v1 — wire types live in the pure
// attachments module (unit-tested there); re-exported for the UI import
// surface, same pattern as the other attachment types.
export type {
  PhotoMappingMetadata,
  PhotoMappingOrientationHint,
  PhotoMappingTargetKind,
  PhotoMappingVisibleTarget
} from "./attachments";

export type Project = {
  id: string;
  name: string;
  created_at: string;
};

/** Job currently leased by a busy worker (server-derived, #144). */
export type WorkerCurrentJob = {
  id: string;
  job_type: string;
  status: string;
  project_id: string | null;
};

export type Worker = {
  id: string;
  display_name: string | null;
  online: boolean;
  capabilities: string[];
  models: string[];
  last_heartbeat: string;
  /** Structured hardware description; opaque to the UI. */
  hardware: Record<string, unknown>;
  busy: boolean;
  current_job: WorkerCurrentJob | null;
};

/** R1: attachment uploads carry the files referenced by photo/file attachments.
 * R6 (#183): asset roles now include "plan" (plan images) and "photo" (real
 * room photos); role "apartment" stays legacy-accepted for old uploads. */
export type AssetRole = "apartment" | "plan" | "photo" | "reference" | "derived" | "attachment";

export type Asset = {
  id: string;
  original_name: string | null;
  media_type: string;
  size_bytes: number;
  sha256: string;
  provenance: string;
  role: AssetRole;
  metadata: Record<string, unknown>;
  source_asset_id: string | null;
  source_asset_ids: string[];
  duplicate_of_asset_id: string | null;
  created_at: string;
};

export type Job = {
  id: string;
  project_id: string | null;
  job_type: string;
  status: string;
  attempt: number;
  idempotency_key: string | null;
  progress: Record<string, unknown>;
  correlation_id: string | null;
  runtime_provenance: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  leased_to: string | null;
  lease_expires_at: string | null;
  created_at: string;
};

export type Generation = {
  id: string;
  job_id: string;
  scene_revision_id: string;
  design_revision_id: string;
  camera_id: string;
  created_at: string;
  manifest: {
    schema_version: "0.1.0";
    generation_id: string;
    scene_revision_id: string;
    design_revision_id: string;
    camera_id: string;
    workflow: { id: string; version: string };
    model_profile: string;
    seed?: number | null;
    input_asset_ids: string[];
    output_asset_ids: string[];
    structured_conditioning?: Record<string, unknown>;
  };
};

export type RenderManifest = {
  schema_version: "0.1.0";
  render_id: string;
  scene_revision_id: string;
  design_revision_id?: string | null;
  camera_id: string;
  renderer_profile: string | null;
  /** Pass name (rgb, depth, …) → derived asset id. */
  passes: Record<string, string>;
  /**
   * Render duration in seconds as reported by the worker (#188/R5 field).
   */
  render_seconds?: number | null;
  /**
   * R7 (#187): render stage as persisted in manifest_json; absent = legacy
   * render (final semantics). The wire RenderRecord carries no top-level
   * stage — read it from here.
   */
  stage?: "draft" | "final" | null;
};

export type RenderRecord = {
  id: string;
  job_id: string;
  scene_revision_id: string;
  design_revision_id: string | null;
  camera_id: string;
  created_at: string;
  manifest: RenderManifest;
  /**
   * R4: variant the render belongs to (server-derived wire key `variant_id`).
   * Absent/null = legacy render shown in the canonical Results section as
   * before.
   */
  variant_id?: string | null;
};

export type CreateRenderInput = {
  camera_id: string;
  scene_revision_id?: string;
  design_revision_id?: string;
  renderer_profile?: string;
  idempotency_key?: string;
  /** R7 (#187): "draft" maps to the eevee profile BE-side when no explicit
   * renderer_profile is sent; "final" (default) keeps the photoreal profile. */
  stage?: "draft" | "final";
};

export type SceneCommandResponse = {
  revision_id: string;
  parent_revision_id: string | null;
  content_hash: string;
  scene: SceneDocument;
};

export type StylePaletteEntry = { hex: string; role: string };

export type StyleProfile = {
  id: string;
  project_id: string;
  model_profile: string | null;
  correlation_id: string | null;
  created_at: string;
  profile: {
    labels: string[];
    palette: StylePaletteEntry[];
    materials: { name: string; finish: string; application: string }[];
    lighting: { temperature_k: number; intent: string[] } | null;
    forms: { keywords: string[] } | null;
    negative_constraints: string[];
  };
};

export type RevisionSummary = {  revision_id: string;
  parent_revision_id: string | null;
  command_id: string | null;
  content_hash: string;
  created_at: string;
};

export type SceneEntity = {
  id: string;
  kind: string;
  /** R1 design layer: "asis" | "structure" | "design"; absent = legacy as-is. */
  state?: string | null;
  /** R2 design intent: "keep" | "remove" | "replace"; absent = not expressed. */
  intent?: "keep" | "remove" | "replace" | null;
  display_name?: string | null;
  transform?: {
    translation_mm?: [number, number, number];
    rotation_deg?: [number, number, number];
    scale?: [number, number, number];
  };
  geometry?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  locks?: {
    existence?: boolean;
    geometry?: boolean;
    transform?: boolean;
    material?: boolean;
  };
};

export type SceneCamera = {
  id: string;
  width_px: number;
  height_px: number;
  intrinsics: {
    fx: number;
    fy: number;
    cx: number;
    cy: number;
  };
  transform: {
    translation_mm: [number, number, number];
    rotation_deg: [number, number, number];
  };
  source_asset_id?: string | null;
  provenance?: {
    source: "user" | "imported" | "measured" | "estimated" | "model_inferred";
    asset_ids?: string[];
    note?: string | null;
  } | null;
  calibration?: {
    quality?: number | null;
    residual?: number | null;
    method?: "manual" | "correspondences" | "imported";
    observations?: Array<{
      world_mm: [number, number, number];
      image_px: [number, number];
      label?: string | null;
    }>;
  } | null;
  /** R7 (#187): owner-facing viewpoint name; null/absent → «Камера N». */
  label?: string | null;
  /** R7 (#187): where the viewpoint came from; absent = legacy saved view. */
  viewpoint_kind?: "saved" | "photo" | "overview" | "auto" | null;
  /** R7 (#187): opaque UI hints; the UI never relies on its contents. */
  ui_metadata?: Record<string, unknown>;
};

export type SceneDocument = {
  scene_id: string;
  project_id: string;
  entities: SceneEntity[];
  cameras: SceneCamera[];
};

export type SceneRevision = {
  revision_id: string;
  parent_revision_id?: string | null;
  content_hash: string;
  scene: SceneDocument;
};

// R2 design check: one server-side rule violation. rule_id is a stable
// identifier (e.g. "clearance.walkway_min"); measured/expected are
// rule-dependent and may be absent or null.
export type CheckSeverity = "info" | "warning" | "error";

export type CheckResult = {
  severity: CheckSeverity;
  /** Entities the violation applies to (e.g. the blockage pair). */
  entity_ids: string[];
  rule_id: string;
  measured_mm?: number | null;
  expected_min_mm?: number | null;
  explanation: string;
  suggestion?: string | null;
};

export type ValidationConfig = {
  min_walkway_mm?: number;
};

export type ValidationReport = {
  schema_version: string;
  scene_revision_id: string;
  scene_content_hash: string;
  config: ValidationConfig;
  summary: { info: number; warning: number; error: number };
  results: CheckResult[];
};

export type AffectedRegion = {
  type: string;
  target_entity_id: string | null;
  camera_id: string | null;
  bbox_px: [number, number, number, number];
  feather_px: number;
  source: string;
};

export type RegionReplacementInput = {
  base_revision_id: string;
  base_asset_id: string;
  mask_region: [number, number, number, number];
  prompt: string;
  reference_asset_id?: string;
  ipa_weight?: number;
  shape?: "rectangle" | "silhouette";
};

export type RegionReplacementResponse = {
  revision_id: string;
  content_hash: string;
  scene: SceneDocument;
  command: Record<string, unknown> | null;
  affected_region: AffectedRegion;
  job: Job;
  base_asset_id: string;
  mask_asset_id: string;
};

export type RedesignInput = {
  base_revision_id: string;
  base_asset_id: string;
  reference_asset_id?: string;
  prompt: string;
  strength: number;
  seed?: number;
  negative_prompt?: string;
};

export type RedesignResponse = {
  revision_id: string;
  content_hash: string;
  job: Job;
  base_asset_id: string;
  reference_asset_id: string;
};

export type PlanScaleSource = "plan_label" | "manual" | "unknown";

export type PlanScale = {
  source: PlanScaleSource;
  mm_per_px: number | null;
};

export type PlanOpeningKind = "door" | "window" | "arch";

export type PlanOpening = {
  id: string;
  kind: PlanOpeningKind;
  t: number;
  width_mm: number;
  height_mm: number;
  sill_mm: number | null;
};

export type PlanWall = {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  thickness_mm: number;
  openings: PlanOpening[];
};

export type PlanRoom = {
  id: string;
  name: string;
  wall_ids: string[];
  floor_finish: string | null;
};

export type PlanFloor = {
  name: string;
  level_mm: number;
  walls: PlanWall[];
  rooms: PlanRoom[];
};

export type PlanDraft = {
  version: string;
  units: "mm";
  scale: PlanScale;
  floors: PlanFloor[];
};

export type PlanAnalyzeHints = {
  known_wall_length_mm?: number;
  wall_asset_index?: number;
  length_mm?: number;
};

export type PlanAnalyzeResponse = Job & { job_id: string; draft_id: string | null };

export type PlanDraftResponse = {
  draft_id: string;
  project_id: string;
  version: number;
  status: string;
  job_id: string | null;
  created_at: string;
  draft: PlanDraft;
};

export type PlanDraftSaveResponse = {
  draft_id: string;
  project_id: string;
  version: number;
  status: string;
};

export type PlanCommitResponse = {
  revision_id: string;
  content_hash: string;
};

// R3 product import: a product candidate extracted from a product URL (or
// created manually from a reference image) that the design page reviews and
// places into the scene. provenance is server-derived: "extracted" for URL
// import, "manual" for image-created candidates, "mixed" once a manually
// patched candidate has extracted fields too.
export type ProductProvenance = "extracted" | "manual" | "mixed";

export type ProductCandidate = {
  id: string;
  project_id: string;
  source_url?: string | null;
  source_asset_id?: string | null;
  title?: string | null;
  brand?: string | null;
  model?: string | null;
  price?: number | null;
  currency?: string | null;
  width_mm?: number | null;
  depth_mm?: number | null;
  height_mm?: number | null;
  material_descriptors?: string[] | null;
  color_descriptors?: string[] | null;
  provenance: ProductProvenance;
  extraction_confidence?: number | null;
  preview_asset_id?: string | null;
  three_d_ref?: string | null;
  // Wire name is "metadata" (candidate_view emits row.metadata_json under
  // that key, always present).
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ImportUrlResponse = {
  candidate: ProductCandidate;
  missing_fields: string[];
  extraction: { status: string; confidence?: number | null };
};

// R4 scene variants: one approved variant per project (the server auto-demotes
// a previously approved variant when another one is approved — there is no
// replace flag). The canonical /scene stays separate: a variant detail NEVER
// redirects the canonical scene head.
export type SceneVariantStatus = "draft" | "shortlisted" | "approved" | "archived";

export type SceneVariant = {
  id: string;
  project_id: string;
  title: string;
  /** Canonical scene revision the variant forked from. */
  base_scene_revision_id: string;
  /** Variant-local head; commands and validation target this revision. */
  head_scene_revision_id: string;
  status: SceneVariantStatus;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type SceneVariantPatch = {
  title?: string;
  status?: SceneVariantStatus;
};

export type VariantListFilters = {
  status?: SceneVariantStatus;
  /** Archived variants are hidden by default (owner decision, R4). */
  include_archived?: boolean;
};

export type VariantForkInput = {
  title?: string;
  /** Fork from an earlier revision of the same variant; default = head. */
  from_revision_id?: string;
};

export type VariantRestoreInput = {
  target_revision_id: string;
  expected_head_revision_id: string;
};

/** Variant reference inside a comparison payload (variants/compare). */
export type VariantRef = {
  variant_id: string;
  title: string;
  status: SceneVariantStatus;
  base_scene_revision_id: string;
  head_scene_revision_id: string;
};

/** One modified entity in a diff: entity id + changed leaf paths (sorted). */
export type VariantEntityChange = { id: string; changes: string[] };

/** One warning-level check diff entry (rule + affected entities). */
export type VariantWarningRef = { rule_id: string; entity_ids: string[] };

/** Totals block of a budget report (backend rounds to 2 decimals). */
export type BudgetTotals = {
  known: number;
  contingency: number;
  grand_total: number;
};

/** Compact budget summary attached to each side of a comparison. */
export type BudgetSummary = {
  totals: BudgetTotals;
  incomplete: boolean;
  unknowns: string[];
  currency: string | null;
};

/** One variant-linked render inside a comparison (image via manifest passes). */
export type VariantRenderRef = {
  id: string;
  job_id: string;
  scene_revision_id: string;
  camera_id: string;
  created_at: string;
};

/** Structured diff between two variant heads (left → right). budget.delta is
 * null whenever either side's report is incomplete (incomparable). */
export type VariantDiff = {
  left: VariantRef;
  right: VariantRef;
  entities: {
    /** Entity ids. */
    added: string[];
    removed: string[];
    modified: VariantEntityChange[];
  };
  materials: {
    /** Material refs. */
    added: string[];
    removed: string[];
  };
  validation: {
    added: VariantWarningRef[];
    resolved: VariantWarningRef[];
  };
  budget: {
    left: BudgetSummary;
    right: BudgetSummary;
    delta: BudgetTotals | null;
  };
  renders: { left: VariantRenderRef[]; right: VariantRenderRef[] };
};

// R4 budget: items are variant-scoped on the wire (variant_id; the R4 routes
// only serve variant-scoped budget collections). New items default to RUB
// (owner decision) — the default lives in sceneVariants.DEFAULT_BUDGET_CURRENCY.
// amount/currency/quantity are nullable: an incomplete item has no price facts
// yet and surfaces through the report's unknowns instead of zero-coercing.
export type BudgetItem = {
  id: string;
  project_id: string;
  variant_id: string | null;
  scene_revision_id: string | null;
  kind: string;
  product_candidate_id: string | null;
  label: string;
  amount: number | null;
  currency: string | null;
  quantity: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type BudgetItemPostBody = {
  kind: string;
  label: string;
  amount?: number;
  currency?: string;
  quantity?: number;
  product_candidate_id?: string;
  metadata?: Record<string, unknown>;
};

export type BudgetItemPatch = Partial<
  Pick<BudgetItemPostBody, "label" | "amount" | "currency" | "quantity" | "metadata">
>;

/** One priced item inside a budget report (derived view, not the raw item). */
export type PricedBudgetItem = {
  id: string;
  kind: string;
  label: string;
  product_candidate_id: string | null;
  scene_revision_id: string | null;
  amount: number | null;
  currency: string | null;
  quantity: number | null;
  effective_amount: number | null;
  effective_currency: string | null;
  effective_quantity: number | null;
  contribution: number | null;
  takeoff: Record<string, unknown> | null;
  unknowns: string[];
  incomplete: boolean;
};

export type BudgetReport = {
  variant_id: string;
  scene_revision_id: string;
  totals: BudgetTotals;
  currency: string | null;
  currencies: string[];
  incomplete: boolean;
  /** Backend fact codes for missing data (missing_price, mixed_currency, …). */
  unknowns: string[];
  items: PricedBudgetItem[];
};

/** Response of POST /variants/{id}/revisions:restore — the new variant head
 * revision. The refreshed variant itself is refetched by the caller. */
export type VariantRestoreResponse = {
  revision_id: string;
  parent_revision_id: string | null;
  content_hash: string;
  scene: SceneDocument;
};

/** Response of a variant-scoped command: the new variant revision. The wire
 * carries `variant_head_scene_revision_id` (no variant object) — callers
 * refetch the variant to observe the moved head. */
export type VariantCommandResponse = {
  revision_id: string;
  parent_revision_id: string | null;
  content_hash: string;
  scene: SceneDocument;
  variant_head_scene_revision_id: string;
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
let csrfToken = "";

// Q1 (#quick-win): projects whose scene is known to not exist yet (GET /scene
// answered 404). The 5s poll refetches the scene every tick; caching the null
// stops the repeated 404 wire calls per project. Only nulls are cached — a
// successful fetch always clears the flag, and scene-creation success paths
// call api.invalidateSceneCache() (App demo create, PlanEditor commit) so the
// poll observes the scene immediately.
const nullSceneCache = new Map<string, true>();

function apiPath(path: string) {
  return `${API_BASE}${path}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (init.method && !["GET", "HEAD"].includes(init.method.toUpperCase()) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(apiPath(path), {
    ...init,
    headers,
    credentials: "include"
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  async me() {
    const result = await request<{ authenticated: boolean; username: string; csrf_token: string }>(
      "/api/v1/auth/me"
    );
    csrfToken = result.csrf_token;
    return result;
  },

  async login(username: string, password: string) {
    const result = await request<{ authenticated: boolean; username: string; csrf_token: string }>(
      "/api/v1/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) }
    );
    csrfToken = result.csrf_token;
    return result;
  },

  async logout() {
    await request("/api/v1/auth/logout", { method: "POST" });
    csrfToken = "";
  },

  projects() {
    return request<Project[]>("/api/v1/projects");
  },

  createProject(name: string) {
    return request<Project>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify({ name })
    });
  },

  // Status-sensitive like scene()/getPlanDraft(): the delete flow must tell
  // 204/404/409 apart, so it reads the raw status instead of request()'s throw.
  // CSRF header is attached the same way request()/upload() do it.
  async deleteProject(projectId: string): Promise<ProjectDeleteOutcome> {
    const headers = new Headers();
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    const response = await fetch(apiPath(`/api/v1/projects/${projectId}`), {
      method: "DELETE",
      headers,
      credentials: "include"
    });
    return deleteOutcomeFromStatus(response.status);
  },

  // Q1: null-cache — a cached "no scene" resolves without a fetch; any
  // successful fetch (or invalidation) clears the cached null.
  async scene(projectId: string): Promise<SceneRevision | null> {
    if (nullSceneCache.has(projectId)) return null;
    const response = await fetch(apiPath(`/api/v1/projects/${projectId}/scene`), {
      credentials: "include"
    });
    if (response.status === 404) {
      nullSceneCache.set(projectId, true);
      return null;
    }
    if (!response.ok) throw new Error(await response.text());
    nullSceneCache.delete(projectId);
    return response.json();
  },

  // Q1: scene creation success paths (App demo create, PlanEditor commit)
  // call this so the next poll actually fetches the freshly created scene.
  invalidateSceneCache(projectId: string) {
    nullSceneCache.delete(projectId);
  },

  // R6 guided setup (#183): the owner-facing setup status for the overview
  // checklist. Poll-safe by contract: 404 (route not deployed yet, or project
  // gone) AND any other failure (network hiccup, 500, …) resolve to null so
  // the 5s poll and the refreshProject Promise.all never stall; the Overview
  // page skips its setup section then.
  async getSetup(projectId: string): Promise<SetupStatus | null> {
    try {
      const response = await fetch(apiPath(`/api/v1/projects/${projectId}/setup`), {
        credentials: "include"
      });
      if (!response.ok) return null;
      return (await response.json()) as SetupStatus;
    } catch {
      return null;
    }
  },

  createScene(projectId: string, scene: SceneDocument) {
    return request<SceneRevision>(`/api/v1/projects/${projectId}/scene`, {
      method: "POST",
      body: JSON.stringify(scene)
    });
  },

  revisions(projectId: string) {
    return request<RevisionSummary[]>(`/api/v1/projects/${projectId}/scene/revisions`);
  },

  revert(projectId: string, expectedBaseRevisionId: string, targetRevisionId: string) {
    return request<SceneRevision>(`/api/v1/projects/${projectId}/scene/revert`, {
      method: "POST",
      body: JSON.stringify({
        expected_base_revision_id: expectedBaseRevisionId,
        target_revision_id: targetRevisionId
      })
    });
  },

  upsertCamera(
    projectId: string,
    cameraId: string,
    baseRevisionId: string,
    camera: SceneCamera,
    solve = false
  ) {
    return request<{
      revision_id: string;
      content_hash: string;
      camera: SceneCamera;
      scene: SceneDocument;
    }>(`/api/v1/projects/${projectId}/cameras/${cameraId}`, {
      method: "PUT",
      body: JSON.stringify({
        base_revision_id: baseRevisionId,
        camera,
        solve
      })
    });
  },

  deleteCamera(projectId: string, cameraId: string, baseRevisionId: string) {
    return request<{
      revision_id: string;
      content_hash: string;
      scene: SceneDocument;
    }>(`/api/v1/projects/${projectId}/cameras/${cameraId}`, {
      method: "DELETE",
      body: JSON.stringify({ base_revision_id: baseRevisionId })
    });
  },

  workers() {
    return request<Worker[]>("/api/v1/workers");
  },

  assets(projectId: string) {
    return request<Asset[]>(`/api/v1/projects/${projectId}/assets`);
  },

  jobs(projectId: string) {
    return request<Job[]>(`/api/v1/projects/${projectId}/jobs`);
  },

  generations(projectId: string) {
    return request<Generation[]>(`/api/v1/projects/${projectId}/generations`);
  },

  styleProfiles(projectId: string) {
    return request<StyleProfile[]>(
      `/api/v1/projects/${projectId}/style-profiles`
    );
  },

  analyzeStyle(projectId: string, referenceAssetIds: string[], sourceText = "") {
    return request<Job>(
      `/api/v1/projects/${projectId}/style-profiles/analyze`,
      {
        method: "POST",
        body: JSON.stringify({
          reference_asset_ids: referenceAssetIds,
          source_text: sourceText,
          overrides: {}
        })
      }
    );
  },

  createReplacement(
    projectId: string,
    baseRevisionId: string,
    targetEntityId: string,
    referenceAssetId: string,
    cameraId: string,
    prompt: string
  ) {
    return request<{
      revision_id: string;
      content_hash: string;
      scene: SceneDocument;
      command: Record<string, unknown>;
      affected_region: {
        type: string;
        target_entity_id: string;
        camera_id: string;
        bbox_px: [number, number, number, number];
        feather_px: number;
        source: string;
      };
      job: Job;
    }>(`/api/v1/projects/${projectId}/replacements`, {
      method: "POST",
      body: JSON.stringify({
        base_revision_id: baseRevisionId,
        target_entity_id: targetEntityId,
        reference_asset_id: referenceAssetId,
        camera_id: cameraId,
        prompt
      })
    });
  },

  createRegionReplacement(projectId: string, body: RegionReplacementInput) {
    return request<RegionReplacementResponse>(
      `/api/v1/projects/${projectId}/replacements`,
      {
        method: "POST",
        body: JSON.stringify(body)
      }
    );
  },

  createRedesign(projectId: string, body: RedesignInput) {
    return request<RedesignResponse>(
      `/api/v1/projects/${projectId}/redesigns`,
      {
        method: "POST",
        body: JSON.stringify(body)
      }
    );
  },

  createGeneration(
    projectId: string,
    designRevisionId: string,
    cameraId: string,
    prompt: string,
    referenceAssetIds: string[] = []
  ) {
    return request<Job>(`/api/v1/projects/${projectId}/generations`, {
      method: "POST",
      body: JSON.stringify({
        design_revision_id: designRevisionId,
        camera_id: cameraId,
        prompt,
        reference_asset_ids: referenceAssetIds,
        idempotency_key: `manual:${designRevisionId}:${cameraId}:${prompt}`
      })
    });
  },

  assetUrl(assetId: string) {
    return apiPath(`/api/v1/assets/${assetId}`);
  },

  createRender(projectId: string, body: CreateRenderInput) {
    return request<Job>(`/api/v1/projects/${projectId}/renders`, {
      method: "POST",
      body: JSON.stringify(body)
    });
  },

  listRenders(projectId: string) {
    return request<RenderRecord[]>(`/api/v1/projects/${projectId}/renders`);
  },

  // R1 attachments: project-scoped collection, same conventions as the other
  // project child resources. listAttachments narrows server-side via query
  // filters; createAttachment takes the payload built by
  // attachments.buildAttachmentPayload (validated, total shape).
  listAttachments(projectId: string, filters: AttachmentFilters = {}) {
    const params = new URLSearchParams();
    if (filters.target_type) params.set("target_type", filters.target_type);
    if (filters.target_id) params.set("target_id", filters.target_id);
    if (filters.kind) params.set("kind", filters.kind);
    const query = params.toString();
    return request<Attachment[]>(
      `/api/v1/projects/${projectId}/attachments${query ? `?${query}` : ""}`
    );
  },

  createAttachment(projectId: string, body: AttachmentPostBody) {
    return request<Attachment>(`/api/v1/projects/${projectId}/attachments`, {
      method: "POST",
      body: JSON.stringify(body)
    });
  },

  patchAttachment(projectId: string, attachmentId: string, patch: AttachmentPatch) {
    return request<Attachment>(
      `/api/v1/projects/${projectId}/attachments/${attachmentId}`,
      {
        method: "PATCH",
        body: JSON.stringify(patch)
      }
    );
  },

  deleteAttachment(projectId: string, attachmentId: string) {
    // 204 responses resolve to undefined via request() (same as DELETE flows).
    return request<void>(`/api/v1/projects/${projectId}/attachments/${attachmentId}`, {
      method: "DELETE"
    });
  },

  // R3 product import: candidate CRUD + URL extraction, same project-scoped
  // conventions as the other project child resources. URL-guard rejections
  // (private addresses, timeouts, …) surface as 422 {code:"url_rejected",
  // reason} — DesignPage maps them to friendly text via
  // productImport.rejectReasonLabel.
  importProductUrl(projectId: string, url: string) {
    return request<ImportUrlResponse>(
      `/api/v1/projects/${projectId}/products/import-url`,
      { method: "POST", body: JSON.stringify({ url }) }
    );
  },

  createProduct(
    projectId: string,
    payload: ProductPatch & { source_url?: string; source_asset_id?: string }
  ) {
    return request<ProductCandidate>(`/api/v1/projects/${projectId}/products`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },

  patchProduct(projectId: string, candidateId: string, patch: ProductPatch) {
    return request<ProductCandidate>(
      `/api/v1/projects/${projectId}/products/${candidateId}`,
      { method: "PATCH", body: JSON.stringify(patch) }
    );
  },

  deleteProduct(projectId: string, candidateId: string) {
    return request<void>(`/api/v1/projects/${projectId}/products/${candidateId}`, {
      method: "DELETE"
    });
  },

  // Opaque pass-through query filters (server-defined); empty values are
  // dropped like listAttachments does for its typed filters.
  listProducts(projectId: string, filters: Record<string, string> = {}) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value);
    }
    const query = params.toString();
    return request<ProductCandidate[]>(
      `/api/v1/projects/${projectId}/products${query ? `?${query}` : ""}`
    );
  },

  // R4 scene variants + variant budget. Wire cross-checked against the
  // backend routes (variants are project children; budget collections are
  // variant-scoped). Writes go through request() so the CSRF header is
  // attached exactly like every other mutating client.
  createVariantFromCurrent(projectId: string, title: string) {
    return request<SceneVariant>(
      `/api/v1/projects/${projectId}/variants:create-from-current`,
      { method: "POST", body: JSON.stringify({ title }) }
    );
  },

  // include_archived lifts the server-side archive exclusion (owner decision:
  // archived variants are hidden by default; the UI toggle passes it).
  listVariants(projectId: string, filters: VariantListFilters = {}) {
    const params = new URLSearchParams();
    if (filters.status) params.set("status", filters.status);
    if (filters.include_archived) params.set("include_archived", "true");
    const query = params.toString();
    return request<SceneVariant[]>(
      `/api/v1/projects/${projectId}/variants${query ? `?${query}` : ""}`
    );
  },

  // Status-sensitive like scene(): a variant deleted in another tab must not
  // crash a poll-driven refetch — 404 resolves to null.
  async getVariant(projectId: string, variantId: string): Promise<SceneVariant | null> {
    const response = await fetch(
      apiPath(`/api/v1/projects/${projectId}/variants/${variantId}`),
      { credentials: "include" }
    );
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return response.json();
  },

  patchVariant(projectId: string, variantId: string, patch: SceneVariantPatch) {
    return request<SceneVariant>(
      `/api/v1/projects/${projectId}/variants/${variantId}`,
      { method: "PATCH", body: JSON.stringify(patch) }
    );
  },

  deleteVariant(projectId: string, variantId: string) {
    // 204 resolves to undefined via request() (same as the other DELETEs).
    return request<void>(`/api/v1/projects/${projectId}/variants/${variantId}`, {
      method: "DELETE"
    });
  },

  forkVariant(projectId: string, variantId: string, input: VariantForkInput = {}) {
    return request<SceneVariant>(
      `/api/v1/projects/${projectId}/variants/${variantId}:fork`,
      { method: "POST", body: JSON.stringify(input) }
    );
  },

  compareVariants(projectId: string, leftId: string, rightId: string) {
    const params = new URLSearchParams({ left: leftId, right: rightId });
    return request<VariantDiff>(
      `/api/v1/projects/${projectId}/variants/compare?${params.toString()}`
    );
  },

  restoreVariantRevision(
    projectId: string,
    variantId: string,
    input: VariantRestoreInput
  ) {
    return request<VariantRestoreResponse>(
      `/api/v1/projects/${projectId}/variants/${variantId}/revisions:restore`,
      { method: "POST", body: JSON.stringify(input) }
    );
  },

  // Variant-scoped command application: the command is the SAME DesignCommand
  // envelope as the canonical scene (see
  // sceneVariants.buildVariantCommandEnvelope), plus the optimistic-lock head
  // the caller observed. The canonical scene is never touched.
  applyVariantCommand(
    projectId: string,
    variantId: string,
    command: Record<string, unknown>,
    expectedHeadRevisionId: string
  ) {
    return request<VariantCommandResponse>(
      `/api/v1/projects/${projectId}/variants/${variantId}/revisions`,
      {
        method: "POST",
        body: JSON.stringify({
          command,
          expected_head_revision_id: expectedHeadRevisionId
        })
      }
    );
  },

  listBudgetItems(projectId: string, variantId: string) {
    return request<BudgetItem[]>(
      `/api/v1/projects/${projectId}/variants/${variantId}/budget/items`
    );
  },

  addBudgetItem(projectId: string, variantId: string, body: BudgetItemPostBody) {
    return request<BudgetItem>(
      `/api/v1/projects/${projectId}/variants/${variantId}/budget/items`,
      { method: "POST", body: JSON.stringify(body) }
    );
  },

  patchBudgetItem(
    projectId: string,
    variantId: string,
    itemId: string,
    patch: BudgetItemPatch
  ) {
    return request<BudgetItem>(
      `/api/v1/projects/${projectId}/variants/${variantId}/budget/items/${itemId}`,
      { method: "PATCH", body: JSON.stringify(patch) }
    );
  },

  deleteBudgetItem(projectId: string, variantId: string, itemId: string) {
    return request<void>(
      `/api/v1/projects/${projectId}/variants/${variantId}/budget/items/${itemId}`,
      { method: "DELETE" }
    );
  },

  getBudgetReport(projectId: string, variantId: string) {
    return request<BudgetReport>(
      `/api/v1/projects/${projectId}/variants/${variantId}/budget/report`
    );
  },

  applySceneCommand(projectId: string, command: Record<string, unknown>) {
    return request<SceneCommandResponse>(
      `/api/v1/projects/${projectId}/scene/commands`,
      {
        method: "POST",
        body: JSON.stringify(command)
      }
    );
  },

  // R2 design check: run the server-side rule checks for a scene revision
  // (defaults to the latest when scene_revision_id is omitted). CSRF write
  // like every other POST via request(). The response is the stored-report
  // envelope; unwrapValidationReport() extracts body.report (#188).
  async validateScene(
    projectId: string,
    payload: { scene_revision_id?: string; min_walkway_mm?: number } = {}
  ): Promise<ValidationReport> {
    const envelope = await request<ValidationReportEnvelope>(
      `/api/v1/projects/${projectId}/validation`,
      {
        method: "POST",
        body: JSON.stringify(payload)
      }
    );
    const report = unwrapValidationReport(envelope);
    if (!report) throw new Error("validation report missing in response");
    return report;
  },

  // Latest stored validation report for the project (optionally narrowed to
  // one scene revision via query filter, same convention as
  // listAttachments). Status-sensitive like scene()/getPlanDraft(): 404 means
  // "no report yet" and resolves to null instead of throwing. The unwrapped
  // report body is returned (null when the envelope carries no report).
  async latestValidation(
    projectId: string,
    sceneRevisionId?: string
  ): Promise<ValidationReport | null> {
    const query = sceneRevisionId
      ? `?scene_revision_id=${encodeURIComponent(sceneRevisionId)}`
      : "";
    const response = await fetch(
      apiPath(`/api/v1/projects/${projectId}/validation/latest${query}`),
      { credentials: "include" }
    );
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return unwrapValidationReport((await response.json()) as ValidationReportEnvelope);
  },

  analyzePlan(projectId: string, assetIds: string[], hints: PlanAnalyzeHints = {}) {
    return request<PlanAnalyzeResponse>(`/api/v1/projects/${projectId}/plan/analyze`, {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds, hints })
    });
  },

  async getPlanDraft(projectId: string): Promise<PlanDraftResponse | null> {
    const response = await fetch(apiPath(`/api/v1/projects/${projectId}/plan/draft`), {
      credentials: "include"
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return response.json();
  },

  savePlanDraft(projectId: string, draft: PlanDraft) {
    return request<PlanDraftSaveResponse>(`/api/v1/projects/${projectId}/plan/draft`, {
      method: "PUT",
      body: JSON.stringify({ draft })
    });
  },

  commitPlanDraft(projectId: string) {
    return request<PlanCommitResponse>(`/api/v1/projects/${projectId}/plan/draft/commit`, {
      method: "POST"
    });
  },

  cancelJob(jobId: string) {
    return request<Job>(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
  },

  designInstruction(projectId: string, text: string) {
    return request<Job>(`/api/v1/projects/${projectId}/design/instructions`, {
      method: "POST",
      body: JSON.stringify({ text })
    });
  },

  createJob(
    projectId: string,
    jobType: string,
    requiredCapabilities: string[],
    payload: Record<string, unknown> = {},
    idempotencyKey?: string
  ) {
    return request<Job>(`/api/v1/projects/${projectId}/jobs`, {
      method: "POST",
      body: JSON.stringify({
        job_type: jobType,
        required_capabilities: requiredCapabilities,
        payload,
        idempotency_key: idempotencyKey
      })
    });
  },

  upload(
    projectId: string,
    file: File,
    role: AssetRole,
    onProgress: (percent: number) => void
  ) {
    return new Promise<{ id: string; sha256: string; role: AssetRole }>((resolve, reject) => {
      const form = new FormData();
      form.append("role", role);
      form.append("file", file);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", apiPath(`/api/v1/projects/${projectId}/assets`));
      xhr.withCredentials = true;
      if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress(100);
          resolve(JSON.parse(xhr.responseText));
          return;
        }
        reject(new Error(`${xhr.status}: ${xhr.responseText}`));
      };
      xhr.onerror = () => reject(new Error("upload network error"));
      xhr.send(form);
    });
  }
};
