// Pure helpers for the R11 photo-backed context (#185): the photo quad frame
// at a given depth in front of a calibrated camera, resolution of the room
// photo attachment backing a photo viewpoint, toolbar-toggle availability and
// the owner-facing caption copy. No React and no network (and no three.js) so
// the module stays browser-free and unit-testable in isolation (see
// tests/photoContext.test.mjs) — the same style as sceneLayers.ts /
// cameraReadiness.ts. Structural input types keep this module free of the
// browser-only api.ts; the two imports below are pure modules as well.

// Value imports keep the .js extension (R8 briefView.ts convention): the
// unit-test ritual compiles this module for Node, and Node ESM will not
// resolve an extensionless relative import.
import { photoMappingMetadataView } from "./attachments.js";
import { mappingConfidenceLabel } from "./copy.js";

/**
 * R11 contract: the photo quad floats this many metres in front of the active
 * photo camera. Close enough to sit inside the scene, far enough that the
 * frustum cross-section still covers the whole viewport (the quad is sized
 * from the same depth, so the photo fills the view exactly).
 */
export const PHOTO_CONTEXT_DEPTH_M = 0.5;

// ---------------------------------------------------------------------------
// Frustum math: the photo quad must exactly cover the active camera's view
// frustum cross-section at PHOTO_CONTEXT_DEPTH_M, principal-point offsets
// included (CameraController uses an off-axis projection built from fx/fy/
// cx/cy — see SceneViewer.tsx).
// ---------------------------------------------------------------------------

/** Structural camera slice for the frustum math (api.ts SceneCamera-compatible). */
export interface PhotoContextFrameCameraLike {
  width_px: number;
  height_px: number;
  intrinsics: { fx: number; fy: number; cx: number; cy: number };
}

/** Where the photo quad sits in the camera's local space, in metres. */
export type PhotoContextFrame = {
  widthM: number;
  heightM: number;
  centerX: number;
  centerY: number;
  centerZ: number;
};

/**
 * Frame of the photo quad at `depthM` in front of the camera (camera-local
 * coordinates, three.js convention: the camera looks down its local −Z, so
 * centerZ = −depthM puts the quad in front of the eye):
 * - widthM/heightM — the frustum cross-section at that depth (d·px/f).
 * - centerX/centerY — the frustum axis offset induced by the principal point
 *   (cx/cy); zero for a centred principal point. centerZ keeps the
 *   off-axis-projection convention of CameraController.
 */
export function photoContextFrameAtDepth(
  camera: PhotoContextFrameCameraLike,
  depthM: number
): PhotoContextFrame {
  const { fx, fy, cx, cy } = camera.intrinsics;
  return {
    widthM: (depthM * camera.width_px) / fx,
    heightM: (depthM * camera.height_px) / fy,
    centerX: (depthM * (camera.width_px / 2 - cx)) / fx,
    centerY: (depthM * (cy - camera.height_px / 2)) / fy,
    centerZ: -depthM
  };
}

// ---------------------------------------------------------------------------
// Photo-viewpoint → room-photo attachment resolution (#185): a photo context
// exists only when the viewpoint was created from a photo, is calibrated, and
// a room photo attachment carrying the owner-room mapping metadata backs it.
// ---------------------------------------------------------------------------

/** Structural camera slice for photo-context resolution (api.ts-compatible). */
export interface PhotoContextCameraLike {
  id: string;
  label?: string | null;
  viewpoint_kind?: string | null;
  source_asset_id?: string | null;
  /** Presence (non-null) marks the viewpoint as calibrated. */
  calibration?: unknown;
}

/** Structural attachment slice (attachments.Attachment-compatible). */
export interface PhotoContextAttachmentLike {
  kind: string;
  target_type: string;
  asset_id: string | null;
  metadata: unknown;
}

/** The resolved photo backing a photo viewpoint. */
export type PhotoContextSource = {
  /** Asset to load (api.assetUrl) — the attachment's photo. */
  assetId: string;
  /** Wire mapping confidence as stamped on the attachment (may be null). */
  confidence: string | null;
  /** The backing attachment (the quad never mutates it). */
  attachment: PhotoContextAttachmentLike;
};

/**
 * Resolve the room photo that backs a photo viewpoint, or null when any link
 * in the chain is missing: the camera must be a photo viewpoint, calibrated,
 * carry a non-empty source_asset_id, and an attachment must match it with
 * kind="photo", target_type="room", the same asset_id and owner-room mapping
 * metadata. A metadata camera_id, when present, must equal the camera id.
 * Total: never throws — unresolved inputs simply resolve to null.
 */
export function resolvePhotoContextSource(
  camera: PhotoContextCameraLike | null | undefined,
  attachments: readonly PhotoContextAttachmentLike[] | null | undefined
): PhotoContextSource | null {
  if (!camera || camera.viewpoint_kind !== "photo") return null;
  if (!camera.calibration) return null;
  const assetId =
    typeof camera.source_asset_id === "string" ? camera.source_asset_id : "";
  if (assetId.length === 0) return null;
  if (!Array.isArray(attachments)) return null;
  for (const attachment of attachments) {
    if (!attachment) continue;
    if (attachment.kind !== "photo") continue;
    if (attachment.target_type !== "room") continue;
    if (attachment.asset_id !== assetId) continue;
    const view = photoMappingMetadataView(attachment.metadata);
    if (!view) continue;
    const metadataCameraId = (attachment.metadata as Record<string, unknown>)["camera_id"];
    if (metadataCameraId != null && metadataCameraId !== camera.id) continue;
    return { assetId, confidence: view.confidence, attachment };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Toolbar-toggle availability (#185): the toggle is hidden entirely when the
// scene has no photo viewpoints, disabled with a short RU reason otherwise.
// ---------------------------------------------------------------------------

export type PhotoContextAvailability = {
  toggleVisible: boolean;
  toggleEnabled: boolean;
  /** RU reason for the disabled state (title attr); absent when enabled. */
  reason?: string;
};

/**
 * Availability of the «Фото-контекст» toolbar toggle. Pure and total:
 * - no photo viewpoints in the scene → the toggle is not rendered at all;
 * - the active view is not a photo viewpoint → visible but disabled;
 * - a photo viewpoint without a resolved room photo → disabled;
 * - otherwise enabled.
 */
export function photoContextAvailability(
  sceneCameras: readonly PhotoContextCameraLike[] | null | undefined,
  activeCamera: PhotoContextCameraLike | null | undefined,
  source: PhotoContextSource | null | undefined
): PhotoContextAvailability {
  const hasPhotoCameras =
    Array.isArray(sceneCameras) &&
    sceneCameras.some((camera) => camera?.viewpoint_kind === "photo");
  if (!hasPhotoCameras) {
    return { toggleVisible: false, toggleEnabled: false };
  }
  if (!activeCamera || activeCamera.viewpoint_kind !== "photo") {
    return {
      toggleVisible: true,
      toggleEnabled: false,
      reason: "Доступно только для ракурса по фото"
    };
  }
  if (activeCamera.calibration == null) {
    return {
      toggleVisible: true,
      toggleEnabled: false,
      reason: "Сначала откалибруйте камеру по фото"
    };
  }
  if (!source) {
    return {
      toggleVisible: true,
      toggleEnabled: false,
      reason: "Фото не связано с ракурсом"
    };
  }
  return { toggleVisible: true, toggleEnabled: true };
}

// ---------------------------------------------------------------------------
// Caption copy (#185): one RU line composing the viewpoint label, the photo
// provenance, the mapping confidence and the calibration quality. Never leaks
// raw ids (see #188) and degrades to explicit «недоступен» states.
// ---------------------------------------------------------------------------

/** Degrade states of the photo context (why the quad is not showing). */
export type PhotoContextCaptionState = {
  /** "load-failed" — the photo asset failed to load; "missing-link" — the
   * viewpoint has no backing room photo; "uncalibrated" — the photo
   * viewpoint has no calibration yet (review minor: distinguish the real
   * cause instead of blaming the missing link). */
  error: "load-failed" | "missing-link" | "uncalibrated";
};

/** Structural camera slice for the caption (api.ts SceneCamera-compatible). */
export interface PhotoContextCaptionCameraLike {
  label?: string | null;
  calibration?: { quality?: number | null } | null;
}

/**
 * Caption for the active photo viewpoint. Success line:
 * «Фото: {label} · ракурс по фото · {confidence label} · качество N%» —
 * the label falls back to «Фото комнаты» when the camera is unnamed, the
 * quality segment is dropped when no finite calibration quality is known
 * (never «качество NaN%»), and no raw ids ever appear. The state argument
 * overrides with the matching error line: load failure or a missing
 * photo↔viewpoint link.
 */
export function photoContextCaption(
  camera: PhotoContextCaptionCameraLike | null | undefined,
  source: PhotoContextSource | null | undefined,
  state?: PhotoContextCaptionState
): string {
  if (state?.error === "load-failed") {
    return "Фото-контекст недоступен: не удалось загрузить фото";
  }
  if (state?.error === "uncalibrated") {
    return "Фото-контекст недоступен: камера ещё не откалибрована по фото";
  }
  if (state?.error === "missing-link" || !source) {
    return "Фото-контекст недоступен: фото не связано с ракурсом";
  }
  const label = typeof camera?.label === "string" ? camera.label.trim() : "";
  const quality = camera?.calibration?.quality;
  const qualityPart =
    typeof quality === "number" && Number.isFinite(quality)
      ? ` · качество ${Math.round(quality * 100)}%`
      : "";
  return `Фото: ${label !== "" ? label : "Фото комнаты"} · ракурс по фото · ${mappingConfidenceLabel(source.confidence)}${qualityPart}`;
}

/**
 * True when the success caption for this source already displays the
 * «приблизительно» confidence label — the case where the standalone
 * «Камера сопоставлена приблизительно» badge would duplicate it (R11
 * compose-once rule: the info is shown once, never twice).
 */
export function photoContextCaptionShowsApprox(
  source: PhotoContextSource | null | undefined
): boolean {
  return (
    source != null &&
    mappingConfidenceLabel(source.confidence) === "приблизительно"
  );
}
