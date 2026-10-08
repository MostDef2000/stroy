// Compact camera-readiness summary for the Design page (#105). Pure and total:
// nothing throws, no React, no network. Reports whether any camera exists and
// whether the first photo-calibrated camera carries calibration quality.
//
// Structural input type (no api.ts import): any camera shape with an optional
// calibration { method, quality } qualifies — keeps this helper dependency-free
// like the other pure modules.

export type CameraReadinessState = "none" | "uncalibrated" | "calibrated";

export interface CameraReadiness {
  state: CameraReadinessState;
  label: string;
  detail: string | null;
}

export interface CameraLike {
  calibration?:
    | {
        method?: "manual" | "correspondences" | "imported";
        quality?: number | null;
      }
    | null;
}

export function computeCameraReadiness(cameras: readonly CameraLike[]): CameraReadiness {
  if (cameras.length === 0) {
    return { state: "none", label: "Камера не настроена", detail: null };
  }
  const calibrated = cameras.find(
    (camera) => camera.calibration?.method === "correspondences"
  );
  if (calibrated) {
    const quality = calibrated.calibration?.quality;
    return {
      state: "calibrated",
      label: "Камера откалибрована по фото",
      detail: quality != null ? `качество ${Math.round(quality * 100)}%` : null
    };
  }
  return { state: "uncalibrated", label: "Камера не откалибрована по фото", detail: null };
}

// ---------------------------------------------------------------------------
// R7 (#187): viewpoint picker grouping + render readiness. Pure and total,
// same structural-input style as computeCameraReadiness.
// ---------------------------------------------------------------------------

/** Viewpoint origin on the wire (absent = legacy camera = saved view). */
export type ViewpointKind = "saved" | "photo" | "overview" | "auto";

/** Structural camera slice for the viewpoint picker. */
export interface ViewpointCameraLike {
  id: string;
  label?: string | null;
  viewpoint_kind?: ViewpointKind | null;
}

export type ViewpointOption = { id: string; label: string; title: string };

export type ViewpointGroups = {
  saved: ViewpointOption[];
  photo: ViewpointOption[];
};

function viewpointOption(
  camera: { id: string; label?: string | null },
  index: number
): ViewpointOption {
  const label = typeof camera.label === "string" ? camera.label.trim() : "";
  // #188: raw camera ids never reach the default UI — fallback is the stable
  // «Камера N» form; the id stays available via the option title.
  return {
    id: camera.id,
    label: label !== "" ? label : `Камера ${index + 1}`,
    title: camera.id
  };
}

/**
 * Group persisted cameras for the viewpoint picker: everything that is not a
 * photo viewpoint counts as a saved viewpoint (including legacy cameras
 * without viewpoint_kind and explicit "overview" kind). Order is preserved
 * within each group; labels are per-group numbered when unnamed.
 */
export function groupViewpointOptions(
  cameras: readonly ViewpointCameraLike[]
): ViewpointGroups {
  const saved: ViewpointOption[] = [];
  const photo: ViewpointOption[] = [];
  for (const camera of cameras) {
    if (!camera.id) continue;
    const option = viewpointOption(
      camera,
      camera.viewpoint_kind === "photo" ? photo.length : saved.length
    );
    (camera.viewpoint_kind === "photo" ? photo : saved).push(option);
  }
  return { saved, photo };
}

// ---------------------------------------------------------------------------
// R7 (#187): render readiness for the Design page rail. Pure and total.
// ---------------------------------------------------------------------------

export type RenderReadinessState = "none" | "estimated" | "calibrated";

export interface RenderCameraLike extends CameraLike {
  id: string;
}

export interface RenderReadiness {
  state: RenderReadinessState;
  /** Camera the render would use (first calibrated, else the first camera). */
  cameraId: string | null;
  /** Final renders need a photo-calibrated (correspondences) camera. */
  allowFinal: boolean;
  label: string;
  /** Warning for the disabled state («Недостаточно точности…»). */
  warning: string | null;
}

/**
 * Which camera a render may use (contract §2.3: a saved overview viewpoint
 * must be renderable — «Сделать рендер» works from any existing camera).
 * - A correspondences-calibrated camera enables the full draft→final flow.
 * - ANY other existing camera (saved/photo/estimated/user provenance) renders
 *   drafts only, with the «приблизительный ракурс» caveat — never pretending
 *   an approximate pose is calibrated.
 * - No camera at all disables rendering.
 */
export function computeRenderReadiness(
  cameras: readonly RenderCameraLike[]
): RenderReadiness {
  const calibrated = cameras.find(
    (camera) => camera.calibration?.method === "correspondences"
  );
  if (calibrated) {
    return {
      state: "calibrated",
      cameraId: calibrated.id,
      allowFinal: true,
      label: "Камера откалибрована по фото",
      warning: null
    };
  }
  const anyCamera = cameras.find((camera) => Boolean(camera.id));
  if (anyCamera) {
    return {
      state: "estimated",
      cameraId: anyCamera.id,
      allowFinal: false,
      label: "приблизительный ракурс",
      warning: "Финальный рендер требует откалиброванной камеры."
    };
  }
  return {
    state: "none",
    cameraId: null,
    allowFinal: false,
    label: "Камера не настроена",
    warning: "Недостаточно точности для финального рендера"
  };
}
