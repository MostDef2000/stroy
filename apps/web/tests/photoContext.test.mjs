// Zero-dependency unit tests for the R11 photo-backed context helpers (#185).
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/*.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/photoContext.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  PHOTO_CONTEXT_DEPTH_M,
  photoContextAvailability,
  photoContextCaption,
  photoContextCaptionShowsApprox,
  photoContextFrameAtDepth,
  resolvePhotoContextSource
} from "../build/photoContext.js";

// ---------------------------------------------------------------------------
// Fixtures. Valid photo mapping metadata shape (attachments.ts
// photoMappingMetadataView): mapping="owner_room" + confidence + targets.
// ---------------------------------------------------------------------------

const CAMERA_ID = "camera-uuid-1";
const ASSET_ID = "asset-uuid-9";

function photoCamera(overrides = {}) {
  return {
    id: CAMERA_ID,
    label: "Гостиная",
    viewpoint_kind: "photo",
    source_asset_id: ASSET_ID,
    calibration: { quality: 0.876 },
    ...overrides
  };
}

function mappedAttachment(overrides = {}) {
  return {
    kind: "photo",
    target_type: "room",
    asset_id: ASSET_ID,
    metadata: {
      mapping: "owner_room",
      confidence: "confirmed",
      visible_targets: []
    },
    ...overrides
  };
}

// ---------------------------------------------------------------------------
// Frustum math (#185): the quad must exactly cover the frustum cross-section
// at the quad depth, principal-point offsets included, camera looking down
// its local −Z.
// ---------------------------------------------------------------------------

test("#185: quad depth constant is a small positive metre value", () => {
  assert.ok(Number.isFinite(PHOTO_CONTEXT_DEPTH_M));
  assert.ok(PHOTO_CONTEXT_DEPTH_M > 0);
  assert.ok(PHOTO_CONTEXT_DEPTH_M < 5, "quad sits inside the room scale");
});

test("#185: frame size = depth·px/focal (w=d·width/fx, h=d·height/fy)", () => {
  const frame = photoContextFrameAtDepth(
    {
      width_px: 1920,
      height_px: 1080,
      intrinsics: { fx: 1000, fy: 900, cx: 960, cy: 540 }
    },
    0.5
  );
  assert.equal(frame.widthM, (0.5 * 1920) / 1000);
  assert.equal(frame.heightM, (0.5 * 1080) / 900);
});

test("#185: centerZ = −depth (three.js camera looks down its local −Z)", () => {
  const frame = photoContextFrameAtDepth(
    {
      width_px: 1920,
      height_px: 1080,
      intrinsics: { fx: 1000, fy: 900, cx: 960, cy: 540 }
    },
    0.5
  );
  assert.equal(frame.centerZ, -0.5);
});

test("#185: centred principal point → zero centre offsets", () => {
  const frame = photoContextFrameAtDepth(
    {
      width_px: 1920,
      height_px: 1080,
      intrinsics: { fx: 1000, fy: 900, cx: 960, cy: 540 }
    },
    0.5
  );
  assert.equal(frame.centerX, 0);
  assert.equal(frame.centerY, 0);
});

test("#185: off-centre principal point shifts the quad centre", () => {
  const frame = photoContextFrameAtDepth(
    {
      width_px: 1920,
      height_px: 1080,
      intrinsics: { fx: 1000, fy: 900, cx: 980, cy: 500 }
    },
    0.5
  );
  // centerX = d·(width/2 − cx)/fx = 0.5·(960−980)/1000 = −0.01
  assert.equal(frame.centerX, -0.01);
  // centerY = d·(cy − height/2)/fy = 0.5·(500−540)/900
  assert.ok(
    Math.abs(frame.centerY - (0.5 * (500 - 540)) / 900) < 1e-12,
    "centerY follows the d·(cy − height/2)/fy offset"
  );
});

// ---------------------------------------------------------------------------
// resolvePhotoContextSource (#185): room+photo+asset match OK; every other
// attachment kind/target, a metadata camera_id mismatch, a missing
// calibration and an absent source_asset_id are all rejected.
// ---------------------------------------------------------------------------

test("#185: resolve — matching room photo attachment accepted", () => {
  const source = resolvePhotoContextSource(photoCamera(), [mappedAttachment()]);
  assert.ok(source, "a calibrated photo viewpoint with a matching room photo resolves");
  assert.equal(source.assetId, ASSET_ID);
  assert.equal(source.confidence, "confirmed");
  assert.equal(source.attachment.asset_id, ASSET_ID);
});

test("#185: resolve — confidence passes through from the attachment metadata", () => {
  const approx = resolvePhotoContextSource(
    photoCamera(),
    [mappedAttachment({ metadata: { mapping: "owner_room", confidence: "approx", visible_targets: [] } })]
  );
  assert.equal(approx.confidence, "approx");
  const unmapped = resolvePhotoContextSource(
    photoCamera(),
    [mappedAttachment({ metadata: { mapping: "owner_room", visible_targets: [] } })]
  );
  assert.equal(unmapped.confidence, null);
});

test("#185: resolve — non-photo attachment kinds rejected", () => {
  for (const kind of ["file", "concept", "note", "render"]) {
    const source = resolvePhotoContextSource(photoCamera(), [
      mappedAttachment({ kind })
    ]);
    assert.equal(source, null, `kind=${kind} must not back a photo viewpoint`);
  }
});

test("#185: resolve — non-room attachment targets rejected", () => {
  for (const target_type of ["project", "entity", "scene"]) {
    const source = resolvePhotoContextSource(photoCamera(), [
      mappedAttachment({ target_type })
    ]);
    assert.equal(source, null, `target_type=${target_type} must not resolve`);
  }
});

test("#185: resolve — asset_id mismatch rejected", () => {
  const source = resolvePhotoContextSource(photoCamera(), [
    mappedAttachment({ asset_id: "asset-uuid-other" })
  ]);
  assert.equal(source, null);
});

test("#185: resolve — metadata.camera_id mismatch rejected, match accepted", () => {
  const mismatch = resolvePhotoContextSource(photoCamera(), [
    mappedAttachment({ metadata: { mapping: "owner_room", confidence: "approx", camera_id: "camera-uuid-2", visible_targets: [] } })
  ]);
  assert.equal(mismatch, null, "a camera_id stamp for another viewpoint must reject");
  const match = resolvePhotoContextSource(photoCamera(), [
    mappedAttachment({ metadata: { mapping: "owner_room", confidence: "approx", camera_id: CAMERA_ID, visible_targets: [] } })
  ]);
  assert.ok(match, "camera_id equal to the viewpoint id resolves");
});

test("#185: resolve — non-mapping metadata rejected", () => {
  for (const metadata of [null, {}, { mapping: "something_else" }, "junk"]) {
    const source = resolvePhotoContextSource(photoCamera(), [
      mappedAttachment({ metadata })
    ]);
    assert.equal(source, null, `metadata=${JSON.stringify(metadata)} must not resolve`);
  }
});

test("#185: resolve — uncalibrated viewpoint rejected", () => {
  const source = resolvePhotoContextSource(
    photoCamera({ calibration: undefined }),
    [mappedAttachment()]
  );
  assert.equal(source, null, "no calibration → no photo context");
  const nullCalibration = resolvePhotoContextSource(
    photoCamera({ calibration: null }),
    [mappedAttachment()]
  );
  assert.equal(nullCalibration, null);
});

test("#185: resolve — absent/empty source_asset_id rejected", () => {
  assert.equal(
    resolvePhotoContextSource(photoCamera({ source_asset_id: undefined }), [mappedAttachment()]),
    null
  );
  assert.equal(
    resolvePhotoContextSource(photoCamera({ source_asset_id: null }), [mappedAttachment()]),
    null
  );
  assert.equal(
    resolvePhotoContextSource(photoCamera({ source_asset_id: "" }), [mappedAttachment()]),
    null
  );
});

test("#185: resolve — non-photo viewpoints and degenerate inputs rejected", () => {
  assert.equal(resolvePhotoContextSource(photoCamera({ viewpoint_kind: "saved" }), [mappedAttachment()]), null);
  assert.equal(resolvePhotoContextSource(photoCamera({ viewpoint_kind: "overview" }), [mappedAttachment()]), null);
  assert.equal(resolvePhotoContextSource(null, [mappedAttachment()]), null);
  assert.equal(resolvePhotoContextSource(photoCamera(), null), null);
  assert.equal(resolvePhotoContextSource(photoCamera(), []), null);
});

// ---------------------------------------------------------------------------
// photoContextAvailability (#185): hidden without photo viewpoints; disabled
// with a reason for non-photo views and unresolved sources; enabled otherwise.
// ---------------------------------------------------------------------------

test("#185: availability — no photo viewpoints anywhere → toggle hidden", () => {
  for (const cameras of [null, undefined, [], [photoCamera({ viewpoint_kind: "saved" })]]) {
    const availability = photoContextAvailability(cameras, null, null);
    assert.equal(availability.toggleVisible, false, `cameras=${JSON.stringify(cameras)}`);
    assert.equal(availability.toggleEnabled, false);
  }
});

test("#185: availability — overview/saved/auto active view → disabled with reason", () => {
  const cameras = [photoCamera(), photoCamera({ id: "saved-1", viewpoint_kind: "saved", source_asset_id: null })];
  for (const active of [null, cameras[1]]) {
    const availability = photoContextAvailability(cameras, active, null);
    assert.equal(availability.toggleVisible, true);
    assert.equal(availability.toggleEnabled, false);
    assert.equal(availability.reason, "Доступно только для ракурса по фото");
  }
});

test("#185: availability — photo viewpoint without a resolved source → disabled with reason", () => {
  const availability = photoContextAvailability([photoCamera()], photoCamera(), null);
  assert.equal(availability.toggleVisible, true);
  assert.equal(availability.toggleEnabled, false);
  assert.equal(availability.reason, "Фото не связано с ракурсом");
});

test("#185: availability — photo viewpoint without calibration → disabled with calibration reason", () => {
  // Review minor: an uncalibrated photo camera used to be blamed on the
  // missing link; the real cause (no calibration yet) gets its own reason.
  const uncalibrated = photoCamera({ calibration: null });
  const reason = photoContextAvailability([uncalibrated], uncalibrated, null);
  assert.equal(uncalibrated.calibration, null);
  assert.equal(reason.toggleVisible, true);
  assert.equal(reason.toggleEnabled, false);
  assert.equal(reason.reason, "Сначала откалибруйте камеру по фото");
});

test("#185: availability — photo viewpoint with a resolved source → enabled, no reason", () => {
  const source = resolvePhotoContextSource(photoCamera(), [mappedAttachment()]);
  const availability = photoContextAvailability([photoCamera()], photoCamera(), source);
  assert.equal(availability.toggleVisible, true);
  assert.equal(availability.toggleEnabled, true);
  assert.equal(availability.reason, undefined);
});

// ---------------------------------------------------------------------------
// photoContextCaption (#185): provenance line, fallbacks, quality rounding,
// error states — and never a raw id.
// ---------------------------------------------------------------------------

test("#185: caption — success line composes label, provenance and confidence", () => {
  const source = { assetId: ASSET_ID, confidence: "confirmed", attachment: mappedAttachment() };
  const caption = photoContextCaption(photoCamera(), source);
  assert.ok(caption.includes("Фото: Гостиная"), `label in caption: ${caption}`);
  assert.ok(caption.includes("ракурс по фото"), `provenance in caption: ${caption}`);
  assert.ok(caption.includes("подтверждено владельцем"), `confidence in caption: ${caption}`);
  assert.equal(caption, "Фото: Гостиная · ракурс по фото · подтверждено владельцем · качество 88%");
});

test("#185: caption — unnamed viewpoint falls back to «Фото комнаты»", () => {
  const source = { assetId: ASSET_ID, confidence: "confirmed", attachment: mappedAttachment() };
  for (const label of [null, undefined, "", "   "]) {
    const caption = photoContextCaption(photoCamera({ label }), source);
    assert.ok(caption.startsWith("Фото: Фото комнаты"), `label=${JSON.stringify(label)} → fallback, got ${caption}`);
  }
});

test("#185: caption — quality is rounded to whole percents", () => {
  const source = { assetId: ASSET_ID, confidence: "confirmed", attachment: mappedAttachment() };
  assert.ok(
    photoContextCaption(photoCamera({ calibration: { quality: 0.876 } }), source).includes("качество 88%")
  );
  assert.ok(
    photoContextCaption(photoCamera({ calibration: { quality: 0.5 } }), source).includes("качество 50%")
  );
});

test("#185: caption — non-finite/absent quality drops the quality segment", () => {
  const source = { assetId: ASSET_ID, confidence: "confirmed", attachment: mappedAttachment() };
  for (const calibration of [null, undefined, {}, { quality: null }, { quality: NaN }, { quality: Infinity }]) {
    const caption = photoContextCaption(photoCamera({ calibration }), source);
    assert.ok(!caption.includes("качество"), `calibration=${JSON.stringify(calibration)} → no quality segment: ${caption}`);
  }
});

test("#185: caption — load failure and missing link have explicit error lines", () => {
  const source = { assetId: ASSET_ID, confidence: "confirmed", attachment: mappedAttachment() };
  assert.equal(
    photoContextCaption(photoCamera(), source, { error: "load-failed" }),
    "Фото-контекст недоступен: не удалось загрузить фото"
  );
  assert.equal(
    photoContextCaption(photoCamera(), source, { error: "missing-link" }),
    "Фото-контекст недоступен: фото не связано с ракурсом"
  );
  // No source at all reads as the missing-link state (never a crash).
  assert.equal(
    photoContextCaption(photoCamera(), null),
    "Фото-контекст недоступен: фото не связано с ракурсом"
  );
  // Uncalibrated photo viewpoint: the real cause, not the missing link.
  assert.equal(
    photoContextCaption(photoCamera(), source, { error: "uncalibrated" }),
    "Фото-контекст недоступен: камера ещё не откалибрована по фото"
  );
});

test("#185: caption — no raw ids anywhere (#188)", () => {
  const source = { assetId: ASSET_ID, confidence: null, attachment: mappedAttachment() };
  const captions = [
    photoContextCaption(photoCamera({ label: null }), source),
    photoContextCaption(photoCamera(), source),
    photoContextCaption(photoCamera({ label: null }), source, { error: "load-failed" }),
    photoContextCaption(photoCamera({ label: null }), null)
  ];
  for (const caption of captions) {
    assert.ok(!caption.includes(CAMERA_ID), `no camera id in ${caption}`);
    assert.ok(!caption.includes(ASSET_ID), `no asset id in ${caption}`);
    assert.ok(!caption.includes("uuid"), `no id-shaped substrings in ${caption}`);
  }
});

test("#185: approx-suppression flag — true only for the «приблизительно» line", () => {
  const approx = { assetId: ASSET_ID, confidence: "approx", attachment: mappedAttachment() };
  const confirmed = { assetId: ASSET_ID, confidence: "confirmed", attachment: mappedAttachment() };
  const calibrated = { assetId: ASSET_ID, confidence: "calibrated", attachment: mappedAttachment() };
  const unmapped = { assetId: ASSET_ID, confidence: null, attachment: mappedAttachment() };
  assert.equal(photoContextCaptionShowsApprox(approx), true);
  assert.equal(photoContextCaptionShowsApprox(unmapped), true, "unknown confidence reads as «приблизительно»");
  assert.equal(photoContextCaptionShowsApprox(confirmed), false);
  assert.equal(photoContextCaptionShowsApprox(calibrated), false);
  assert.equal(photoContextCaptionShowsApprox(null), false);
  assert.equal(photoContextCaptionShowsApprox(undefined), false);
});
