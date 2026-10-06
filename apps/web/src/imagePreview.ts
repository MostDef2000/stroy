// Shared image-preview math (#150): pure, DOM-free helpers used by
// ImagePreview.tsx and ImageLightbox.tsx. No React, no DOM — everything here
// is unit-testable via tests/imagePreview.test.mjs.
//
// Transform model (lightbox): the <img> renders at its natural pixel size and
// is moved with `transform: translate(pan) scale(fitScale * zoom)` and
// `transform-origin: center`. Scaling about the center keeps the image center
// fixed, so after translate the image center sits at viewport center + pan.
// `pan` is therefore the offset of the image center from the viewport center.

export type Size = { x: number; y: number };
export type Point = { x: number; y: number };
export type ZoomState = { zoom: number; pan: Point };

export type FitResult = {
  /** Multiply natural pixel size by this to get the fit-at-zoom-1 size. */
  fitScale: number;
  /** Rendered image size at zoom 1 (natural * fitScale). */
  rendered: Size;
};

export type ZoomCommand =
  | "zoomIn"
  | "zoomOut"
  | "reset"
  | "fit"
  | "panLeft"
  | "panRight"
  | "panUp"
  | "panDown";

export const IMAGE_PREVIEW_ZOOM = {
  min: 1,
  max: 4,
  step: 1.25,
  keyboardPanStep: 48
} as const;

const ZERO_POINT: Point = { x: 0, y: 0 };

function isValidSize(size: Size | null | undefined): size is Size {
  return (
    !!size &&
    Number.isFinite(size.x) &&
    Number.isFinite(size.y) &&
    size.x > 0 &&
    size.y > 0
  );
}

function isValidViewport(viewport: Size | null | undefined): viewport is Size {
  return (
    !!viewport &&
    Number.isFinite(viewport.x) &&
    Number.isFinite(viewport.y) &&
    viewport.x >= 0 &&
    viewport.y >= 0
  );
}

/**
 * Fit an image inside a viewport (object-fit: contain math).
 * Zero/invalid dimensions degrade to the safe fallback {fitScale: 1, rendered: {0,0}}.
 */
export function fitContain(
  image: Size | null | undefined,
  viewport: Size
): FitResult {
  const usableViewport =
    isValidViewport(viewport) && viewport.x > 0 && viewport.y > 0 ? viewport : null;
  if (!isValidSize(image) || !usableViewport) {
    return { fitScale: 1, rendered: { x: 0, y: 0 } };
  }
  const scale = Math.min(usableViewport.x / image.x, usableViewport.y / image.y);
  const fitScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  return {
    fitScale,
    rendered: {
      x: image.x * fitScale,
      y: image.y * fitScale
    }
  };
}

/** Clamp a zoom level into [min, max] (defaults: IMAGE_PREVIEW_ZOOM bounds). */
export function clampZoom(
  zoom: number,
  min: number = IMAGE_PREVIEW_ZOOM.min,
  max: number = IMAGE_PREVIEW_ZOOM.max
): number {
  if (!Number.isFinite(zoom)) return min;
  const low = Number.isFinite(min) ? min : IMAGE_PREVIEW_ZOOM.min;
  const high = Number.isFinite(max) ? max : IMAGE_PREVIEW_ZOOM.max;
  return Math.min(max, Math.max(low, zoom));
}

/** Image size on screen for a given fit scale and zoom. Invalid input → {0,0}. */
export function renderedSizeAtZoom(
  image: Size | null | undefined,
  fitScale: number,
  zoom: number
): Size {
  if (!isValidSize(image) || !Number.isFinite(fitScale) || !Number.isFinite(zoom)) {
    return { x: 0, y: 0 };
  }
  return {
    x: image.x * fitScale * zoom,
    y: image.y * fitScale * zoom
  };
}

/**
 * Allowed translate range per axis for an image of `rendered` size in
 * `viewport`. Axis with rendered <= viewport locks to 0 (image fills or
 * letterboxes — no panning room). Axis with rendered > viewport clamps so the
 * image edges never leave the viewport (no blank space): the image center may
 * drift at most (rendered - viewport) / 2 from the viewport center.
 */
export function panBounds(rendered: Size, viewport: Size): {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
} {
  const bound = (renderedAxis: number, viewportAxis: number): number =>
    renderedAxis > viewportAxis ? (renderedAxis - viewportAxis) / 2 : 0;
  const safeRendered = isValidSize(rendered) ? rendered : { x: 0, y: 0 };
  const safeViewport = isValidViewport(viewport) ? viewport : { x: 0, y: 0 };
  const x = bound(safeRendered.x, safeViewport.x);
  const y = bound(safeRendered.y, safeViewport.y);
  // Normalize -0 to 0 so state stays primitive-stable (Object.is/deepStrictEqual).
  return {
    minX: x === 0 ? 0 : -x,
    maxX: x,
    minY: y === 0 ? 0 : -y,
    maxY: y
  };
}

/** Clamp a pan offset so image edges never leave the viewport. */
export function clampPan(
  pan: Point | null | undefined,
  rendered: Size,
  viewport: Size
): Point {
  const bounds = panBounds(rendered, viewport);
  const source = pan && Number.isFinite(pan.x) && Number.isFinite(pan.y) ? pan : ZERO_POINT;
  return {
    x: Math.min(bounds.maxX, Math.max(bounds.minX, source.x)),
    y: Math.min(bounds.maxY, Math.max(bounds.minY, source.y))
  };
}

/**
 * Zoom to `nextZoom` keeping the image point under `focalPoint` (viewport
 * coordinates) fixed. A null/undefined focal point zooms about the center.
 * Does NOT clamp nextZoom — callers clamp via clampZoom first; the resulting
 * pan is always clamped so edges never leave the viewport.
 */
export function applyZoomAtPoint(
  state: ZoomState,
  nextZoom: number,
  focalPoint: Point | null | undefined,
  viewport: Size,
  image: Size | null | undefined
): ZoomState {
  if (!isValidViewport(viewport) || !Number.isFinite(nextZoom) || nextZoom <= 0) {
    return { zoom: clampZoom(nextZoom), pan: { x: 0, y: 0 } };
  }
  const { fitScale } = fitContain(image, viewport);
  const previousZoom = Number.isFinite(state?.zoom) && state.zoom > 0 ? state.zoom : 1;
  const factor = nextZoom / previousZoom;
  const currentPan =
    state?.pan && Number.isFinite(state.pan.x) && Number.isFinite(state.pan.y)
      ? state.pan
      : ZERO_POINT;

  let pan: Point;
  if (focalPoint && Number.isFinite(focalPoint.x) && Number.isFinite(focalPoint.y)) {
    // Keep the image point under the focal point fixed:
    // newPan = (focal - center) * (1 - factor) + pan * factor.
    const centerX = viewport.x / 2;
    const centerY = viewport.y / 2;
    pan = {
      x: (focalPoint.x - centerX) * (1 - factor) + currentPan.x * factor,
      y: (focalPoint.y - centerY) * (1 - factor) + currentPan.y * factor
    };
  } else {
    pan = { x: currentPan.x * factor, y: currentPan.y * factor };
  }

  const rendered = renderedSizeAtZoom(image, fitScale, nextZoom);
  return { zoom: nextZoom, pan: clampPan(pan, rendered, viewport) };
}

/** Apply a zoom/pan command to a state (zoom clamped to default bounds). */
export function reduceZoomState(
  state: ZoomState,
  command: ZoomCommand,
  viewport: Size,
  image: Size | null | undefined
): ZoomState {
  const currentZoom =
    state && Number.isFinite(state.zoom) && state.zoom > 0 ? state.zoom : 1;
  const currentPan =
    state?.pan && Number.isFinite(state.pan.x) && Number.isFinite(state.pan.y)
      ? state.pan
      : ZERO_POINT;

  switch (command) {
    case "reset":
    case "fit":
      return { zoom: 1, pan: { x: 0, y: 0 } };
    case "zoomIn":
      return applyZoomAtPoint(
        state,
        clampZoom(currentZoom * IMAGE_PREVIEW_ZOOM.step),
        null,
        viewport,
        image
      );
    case "zoomOut":
      return applyZoomAtPoint(
        state,
        clampZoom(currentZoom / IMAGE_PREVIEW_ZOOM.step),
        null,
        viewport,
        image
      );
    case "panLeft":
    case "panRight":
    case "panUp":
    case "panDown": {
      const { fitScale } = fitContain(image, viewport);
      const rendered = renderedSizeAtZoom(image, fitScale, currentZoom);
      const step = IMAGE_PREVIEW_ZOOM.keyboardPanStep;
      const delta = {
        panLeft: { x: step, y: 0 },
        panRight: { x: -step, y: 0 },
        panUp: { x: 0, y: step },
        panDown: { x: 0, y: -step }
      }[command];
      return {
        zoom: currentZoom,
        pan: clampPan(
          { x: currentPan.x + delta.x, y: currentPan.y + delta.y },
          rendered,
          viewport
        )
      };
    }
    default:
      return { zoom: currentZoom, pan: { ...currentPan } };
  }
}

/**
 * Map a keyboard event to a zoom/pan command. Modifier combos (Ctrl/Meta/Alt)
 * are ignored (they belong to browser/OS shortcuts) — the lightbox handles
 * Ctrl+wheel separately on a native, non-passive listener.
 */
export function commandFromKeyboardEvent(event: {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
}): ZoomCommand | null {
  if (!event || event.ctrlKey || event.metaKey || event.altKey) return null;
  switch (event.key) {
    case "+":
    case "=":
      return "zoomIn";
    case "-":
    case "_":
      return "zoomOut";
    case "0":
      return "reset";
    case "ArrowLeft":
      return "panLeft";
    case "ArrowRight":
      return "panRight";
    case "ArrowUp":
      return "panUp";
    case "ArrowDown":
      return "panDown";
    default:
      return null;
  }
}
