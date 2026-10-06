// Zero-dependency unit tests for the shared image preview math (#150).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/imagePreview.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/imagePreview.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  applyZoomAtPoint,
  clampPan,
  clampZoom,
  commandFromKeyboardEvent,
  fitContain,
  IMAGE_PREVIEW_ZOOM,
  panBounds,
  reduceZoomState,
  renderedSizeAtZoom
} from "../build/imagePreview.js";

// --- fitContain -------------------------------------------------------------

test("fitContain scales a landscape image down inside a portrait viewport", () => {
  const result = fitContain({ x: 2000, y: 1000 }, { x: 500, y: 1000 });
  assert.equal(result.fitScale, 0.25);
  assert.deepEqual(result.rendered, { x: 500, y: 250 });
});

test("fitContain scales a portrait image down inside a landscape viewport", () => {
  const result = fitContain({ x: 1000, y: 2000 }, { x: 1000, y: 500 });
  assert.equal(result.fitScale, 0.25);
  assert.deepEqual(result.rendered, { x: 250, y: 500 });
});

test("fitContain keeps the scale for a same-aspect viewport", () => {
  const result = fitContain({ x: 1000, y: 500 }, { x: 500, y: 250 });
  assert.equal(result.fitScale, 0.5);
  assert.deepEqual(result.rendered, { x: 500, y: 250 });
});

test("fitContain falls back safely on zero/invalid dimensions", () => {
  assert.deepEqual(fitContain({ x: 0, y: 100 }, { x: 500, y: 500 }), {
    fitScale: 1,
    rendered: { x: 0, y: 0 }
  });
  assert.deepEqual(fitContain(null, { x: 500, y: 500 }), {
    fitScale: 1,
    rendered: { x: 0, y: 0 }
  });
  assert.deepEqual(fitContain({ x: 10, y: 10 }, { x: 0, y: 500 }), {
    fitScale: 1,
    rendered: { x: 0, y: 0 }
  });
});

// --- clampZoom --------------------------------------------------------------

test("clampZoom raises values below the minimum", () => {
  assert.equal(clampZoom(0.5), IMAGE_PREVIEW_ZOOM.min);
});

test("clampZoom lowers values above the maximum", () => {
  assert.equal(clampZoom(10), IMAGE_PREVIEW_ZOOM.max);
});

test("clampZoom leaves in-range values unchanged", () => {
  assert.equal(clampZoom(2.5), 2.5);
  assert.equal(clampZoom(IMAGE_PREVIEW_ZOOM.min), IMAGE_PREVIEW_ZOOM.min);
  assert.equal(clampZoom(IMAGE_PREVIEW_ZOOM.max), IMAGE_PREVIEW_ZOOM.max);
});

test("clampZoom honors custom bounds and non-finite input", () => {
  assert.equal(clampZoom(0.2, 0.5, 2), 0.5);
  assert.equal(clampZoom(3, 0.5, 2), 2);
  assert.equal(clampZoom(Number.NaN), IMAGE_PREVIEW_ZOOM.min);
});

// --- renderedSizeAtZoom -----------------------------------------------------

test("renderedSizeAtZoom multiplies natural size by fitScale and zoom", () => {
  assert.deepEqual(renderedSizeAtZoom({ x: 2000, y: 1000 }, 0.25, 2), {
    x: 1000,
    y: 500
  });
  assert.deepEqual(renderedSizeAtZoom({ x: 800, y: 600 }, 1, 1.25), {
    x: 1000,
    y: 750
  });
});

test("renderedSizeAtZoom degrades to zero on invalid input", () => {
  assert.deepEqual(renderedSizeAtZoom(null, 1, 2), { x: 0, y: 0 });
  assert.deepEqual(renderedSizeAtZoom({ x: 10, y: 10 }, Number.NaN, 2), {
    x: 0,
    y: 0
  });
});

// --- panBounds / clampPan ---------------------------------------------------

test("panBounds locks axes where the image is smaller than the viewport", () => {
  assert.deepEqual(panBounds({ x: 100, y: 100 }, { x: 500, y: 500 }), {
    minX: 0,
    maxX: 0,
    minY: 0,
    maxY: 0
  });
  // Mixed: x larger, y smaller (letterboxed vertically).
  assert.deepEqual(panBounds({ x: 1000, y: 300 }, { x: 500, y: 500 }), {
    minX: -250,
    maxX: 250,
    minY: 0,
    maxY: 0
  });
});

test("panBounds clamps larger axes so edges never leave the viewport", () => {
  assert.deepEqual(panBounds({ x: 1000, y: 600 }, { x: 500, y: 500 }), {
    minX: -250,
    maxX: 250,
    minY: -50,
    maxY: 50
  });
});

test("clampPan zeroes panning for a smaller-than-viewport image", () => {
  assert.deepEqual(clampPan({ x: 50, y: -30 }, { x: 100, y: 100 }, { x: 500, y: 500 }), {
    x: 0,
    y: 0
  });
});

test("clampPan pulls oversized images back inside the edges", () => {
  assert.deepEqual(clampPan({ x: 400, y: -400 }, { x: 1000, y: 600 }, { x: 500, y: 500 }), {
    x: 250,
    y: -50
  });
  assert.deepEqual(clampPan(null, { x: 1000, y: 600 }, { x: 500, y: 500 }), {
    x: 0,
    y: 0
  });
});

// --- applyZoomAtPoint ---------------------------------------------------------

test("applyZoomAtPoint keeps the image center fixed when zooming at the center", () => {
  const next = applyZoomAtPoint(
    { zoom: 1, pan: { x: 0, y: 0 } },
    2,
    { x: 250, y: 250 },
    { x: 500, y: 500 },
    { x: 2000, y: 2000 }
  );
  assert.equal(next.zoom, 2);
  assert.deepEqual(next.pan, { x: 0, y: 0 });
});

test("applyZoomAtPoint pans toward the focal point and clamps to the edges", () => {
  // Focal at the top-left corner: the image point under it must stay there;
  // the required pan exactly equals the max drift for this geometry.
  const next = applyZoomAtPoint(
    { zoom: 1, pan: { x: 0, y: 0 } },
    2,
    { x: 0, y: 0 },
    { x: 500, y: 500 },
    { x: 2000, y: 2000 }
  );
  assert.equal(next.zoom, 2);
  assert.deepEqual(next.pan, { x: 250, y: 250 });
});

test("applyZoomAtPoint scales the existing pan when no focal point is given", () => {
  const next = applyZoomAtPoint(
    { zoom: 1, pan: { x: 40, y: -20 } },
    2,
    null,
    { x: 500, y: 500 },
    { x: 2000, y: 2000 }
  );
  assert.deepEqual(next.pan, { x: 80, y: -40 });
});

// --- reduceZoomState ----------------------------------------------------------

const VIEWPORT = { x: 500, y: 500 };

test("reduceZoomState steps zoom in by IMAGE_PREVIEW_ZOOM.step", () => {
  const next = reduceZoomState(
    { zoom: 1, pan: { x: 0, y: 0 } },
    "zoomIn",
    VIEWPORT,
    { x: 2000, y: 2000 }
  );
  assert.equal(next.zoom, 1.25);
  assert.deepEqual(next.pan, { x: 0, y: 0 });
});

test("reduceZoomState steps zoom out and clamps at the minimum", () => {
  const stepped = reduceZoomState(
    { zoom: 1.25, pan: { x: 0, y: 0 } },
    "zoomOut",
    VIEWPORT,
    { x: 2000, y: 2000 }
  );
  assert.equal(stepped.zoom, 1);
  const floored = reduceZoomState(stepped, "zoomOut", VIEWPORT, { x: 2000, y: 2000 });
  assert.equal(floored.zoom, IMAGE_PREVIEW_ZOOM.min);
});

test("reduceZoomState reset returns to zoom 1 and zero pan", () => {
  assert.deepEqual(
    reduceZoomState({ zoom: 3.7, pan: { x: 120, y: -40 } }, "reset", VIEWPORT, {
      x: 2000,
      y: 2000
    }),
    { zoom: 1, pan: { x: 0, y: 0 } }
  );
  assert.deepEqual(
    reduceZoomState({ zoom: 3.7, pan: { x: 120, y: -40 } }, "fit", VIEWPORT, {
      x: 2000,
      y: 2000
    }),
    { zoom: 1, pan: { x: 0, y: 0 } }
  );
});

test("reduceZoomState arrow pan moves by keyboardPanStep and clamps", () => {
  // zoom 2 of a 2000px image at fitScale 0.25 renders 1000px in a 500px
  // viewport → allowed pan drift is ±250px on both axes.
  const zoomed = { zoom: 2, pan: { x: 0, y: 0 } };
  assert.deepEqual(
    reduceZoomState(zoomed, "panLeft", VIEWPORT, { x: 2000, y: 2000 }).pan,
    { x: IMAGE_PREVIEW_ZOOM.keyboardPanStep, y: 0 }
  );
  assert.deepEqual(
    reduceZoomState(zoomed, "panRight", VIEWPORT, { x: 2000, y: 2000 }).pan,
    { x: -IMAGE_PREVIEW_ZOOM.keyboardPanStep, y: 0 }
  );
  // Repeated panLeft eventually clamps at the +250 edge.
  let state = zoomed;
  for (let i = 0; i < 10; i += 1) {
    state = reduceZoomState(state, "panLeft", VIEWPORT, { x: 2000, y: 2000 });
  }
  assert.deepEqual(state.pan, { x: 250, y: 0 });
});

test("reduceZoomState arrow pan stays locked at zoom 1", () => {
  assert.deepEqual(
    reduceZoomState({ zoom: 1, pan: { x: 0, y: 0 } }, "panUp", VIEWPORT, {
      x: 2000,
      y: 2000
    }).pan,
    { x: 0, y: 0 }
  );
});

// --- commandFromKeyboardEvent -------------------------------------------------

test("commandFromKeyboardEvent maps zoom keys", () => {
  assert.equal(commandFromKeyboardEvent({ key: "+" }), "zoomIn");
  assert.equal(commandFromKeyboardEvent({ key: "=" }), "zoomIn");
  assert.equal(commandFromKeyboardEvent({ key: "-" }), "zoomOut");
  assert.equal(commandFromKeyboardEvent({ key: "_" }), "zoomOut");
  assert.equal(commandFromKeyboardEvent({ key: "0" }), "reset");
});

test("commandFromKeyboardEvent maps arrow keys to pan commands", () => {
  assert.equal(commandFromKeyboardEvent({ key: "ArrowLeft" }), "panLeft");
  assert.equal(commandFromKeyboardEvent({ key: "ArrowRight" }), "panRight");
  assert.equal(commandFromKeyboardEvent({ key: "ArrowUp" }), "panUp");
  assert.equal(commandFromKeyboardEvent({ key: "ArrowDown" }), "panDown");
});

test("commandFromKeyboardEvent ignores modified and unrelated keys", () => {
  assert.equal(commandFromKeyboardEvent({ key: "p", ctrlKey: true }), null);
  assert.equal(commandFromKeyboardEvent({ key: "r", metaKey: true }), null);
  assert.equal(commandFromKeyboardEvent({ key: "ArrowLeft", altKey: true }), null);
  assert.equal(commandFromKeyboardEvent({ key: "+" , ctrlKey: true }), null);
  assert.equal(commandFromKeyboardEvent({ key: "a" }), null);
  assert.equal(commandFromKeyboardEvent({ key: "Escape" }), null);
  assert.equal(commandFromKeyboardEvent(null), null);
});

test("commandFromKeyboardEvent allows Shift modifiers", () => {
  assert.equal(commandFromKeyboardEvent({ key: "ArrowLeft", shiftKey: true }), "panLeft");
});

// --- Peer-review additions (#150 review round) ------------------------------

test("fitContain upscales a smaller-than-viewport image to fit (no cap at 1)", () => {
  // Implemented rule (src/imagePreview.ts): pure contain — scale is
  // min(viewport/image) with NO "never upscale" cap, so a small image is
  // enlarged to fill the viewport exactly on its limiting axis. This matches
  // the lightbox contract where zoom 1 = fit-to-screen even for small assets.
  assert.deepEqual(fitContain({ x: 100, y: 100 }, { x: 200, y: 200 }), {
    fitScale: 2,
    rendered: { x: 200, y: 200 }
  });
  // Non-square small image in a larger viewport: limiting axis wins.
  assert.deepEqual(fitContain({ x: 100, y: 50 }, { x: 400, y: 400 }), {
    fitScale: 4,
    rendered: { x: 400, y: 200 }
  });
});

test("applyZoomAtPoint keeps the focal point fixed while zooming out", () => {
  // Geometry: 4000px image, 1000px viewport → fitScale 0.25. At zoom 2 the
  // rendered size is 2000 (bounds ±500); at zoom 1.25 it is 1250 (bounds ±125).
  const state = { zoom: 2, pan: { x: -200, y: 100 } };
  const next = applyZoomAtPoint(
    state,
    1.25,
    { x: 600, y: 550 },
    { x: 1000, y: 1000 },
    { x: 4000, y: 4000 }
  );
  assert.equal(next.zoom, 1.25);
  assert.deepEqual(next.pan, { x: -87.5, y: 81.25 });

  // Focal-point invariance on the way down: the image point that sat under
  // the focal point before the zoom must sit there after it. scale before =
  // 0.25 * 2 = 0.5, after = 0.25 * 1.25 = 0.3125.
  const scaleBefore = 0.25 * 2;
  const scaleAfter = 0.25 * 1.25;
  const imagePointX = (600 - 500 - state.pan.x) / scaleBefore;
  const imagePointY = (550 - 500 - state.pan.y) / scaleBefore;
  const projectedX = 500 + next.pan.x + imagePointX * scaleAfter;
  const projectedY = 500 + next.pan.y + imagePointY * scaleAfter;
  assert.ok(Math.abs(projectedX - 600) < 1e-9);
  assert.ok(Math.abs(projectedY - 550) < 1e-9);

  // At a corner focal the required pan would leave the viewport, so the
  // edges-first clamp takes precedence over exact invariance (documented rule).
  const corner = applyZoomAtPoint(
    state,
    1.25,
    { x: 0, y: 0 },
    { x: 1000, y: 1000 },
    { x: 4000, y: 4000 }
  );
  assert.deepEqual(corner.pan, { x: -125, y: -125 });
});

test("reduceZoomState zoomIn clamps at the maximum zoom", () => {
  // 3.5 * 1.25 = 4.375 → clamped to IMAGE_PREVIEW_ZOOM.max (4); pan stays 0.
  const next = reduceZoomState(
    { zoom: 3.5, pan: { x: 0, y: 0 } },
    "zoomIn",
    { x: 500, y: 500 },
    { x: 2000, y: 2000 }
  );
  assert.equal(next.zoom, IMAGE_PREVIEW_ZOOM.max);
  assert.equal(next.zoom, 4);
  assert.deepEqual(next.pan, { x: 0, y: 0 });
  // Zooming in again from the max must stay pinned at 4.
  const pinned = reduceZoomState(next, "zoomIn", { x: 500, y: 500 }, { x: 2000, y: 2000 });
  assert.equal(pinned.zoom, 4);
});
