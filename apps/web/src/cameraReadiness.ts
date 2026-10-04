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
