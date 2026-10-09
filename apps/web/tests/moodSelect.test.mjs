// Zero-dependency unit tests for the R9 editorial mood selector helpers.
//
// Node 22 has no native TypeScript support, so the source is compiled first:
//   apps/web/node_modules/.bin/tsc src/*.ts --outDir build \
//     --target ES2022 --module ESNext --moduleResolution Bundler --skipLibCheck
//   node --test "tests/*.test.mjs"
// The compiled module lands at ../build/moodSelect.js (build/ is gitignored).

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildConceptRedesignInput,
  captionMoodTitle,
  DEFAULT_MOOD_ID,
  loadMoodId,
  MOOD_CTA_PROMPT,
  moodPreset,
  MOOD_PRESETS,
  moodStorageKey,
  normalizeMoodId,
  saveMoodId
} from "../build/moodSelect.js";

/** In-memory localStorage double: records writes, seeds initial values. */
function fakeStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  const writes = [];
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => {
      map.set(key, value);
      writes.push([key, value]);
    },
    get writes() {
      return writes;
    }
  };
}

test("#197: preset list mirrors the BE registry shape (ids, RU titles, hints)", () => {
  assert.deepEqual(
    MOOD_PRESETS.map((preset) => preset.id),
    ["calm-contemporary", "warm-minimal", "soft-classic", "family-practical"]
  );
  assert.deepEqual(
    MOOD_PRESETS.map((preset) => preset.title),
    [
      "Спокойный современный",
      "Тёплый минимализм",
      "Мягкая классика",
      "Практично для жизни"
    ]
  );
  for (const preset of MOOD_PRESETS) {
    assert.ok(preset.hint.length > 0, `preset ${preset.id} carries a hint`);
  }
  // The default must be one of the mirrored ids (BE registry key).
  assert.ok(moodPreset(DEFAULT_MOOD_ID) !== null);
});

test("#197: unknown moods normalize to the default, never to a 422-prone id", () => {
  assert.equal(normalizeMoodId("calm-contemporary"), "calm-contemporary");
  assert.equal(normalizeMoodId("warm-minimal"), "warm-minimal");
  assert.equal(normalizeMoodId("garbage"), DEFAULT_MOOD_ID);
  assert.equal(normalizeMoodId(""), DEFAULT_MOOD_ID);
  assert.equal(normalizeMoodId(null), DEFAULT_MOOD_ID);
  assert.equal(normalizeMoodId(undefined), DEFAULT_MOOD_ID);
  assert.equal(normalizeMoodId(42), DEFAULT_MOOD_ID);
});

test("#197: localStorage persists per project under stroy.mood.<projectId>", () => {
  assert.equal(moodStorageKey("p1"), "stroy.mood.p1");
  const storage = fakeStorage();
  saveMoodId("proj-1", "soft-classic", storage);
  assert.equal(loadMoodId("proj-1", storage), "soft-classic");
  // Per project: a second project keeps its own default.
  assert.equal(loadMoodId("proj-2", storage), DEFAULT_MOOD_ID);
  // The write is normalized (never stores an unknown id).
  saveMoodId("proj-1", "garbage", storage);
  assert.equal(loadMoodId("proj-1", storage), DEFAULT_MOOD_ID);
});

test("#197: restore prefers the stored value; corrupt storage reads as default", () => {
  const seeded = fakeStorage({ "stroy.mood.p1": "soft-classic" });
  assert.equal(loadMoodId("p1", seeded), "soft-classic");
  const corrupt = fakeStorage({ "stroy.mood.p1": "no-such-mood" });
  assert.equal(loadMoodId("p1", corrupt), DEFAULT_MOOD_ID);
  const throwing = {
    getItem() {
      throw new Error("SecurityError");
    },
    setItem() {
      throw new Error("SecurityError");
    }
  };
  assert.equal(loadMoodId("p1", throwing), DEFAULT_MOOD_ID);
  // Saving into a throwing storage is best-effort, never throws.
  assert.doesNotThrow(() => saveMoodId("p1", "warm-minimal", throwing));
});

test("#197: CTA wiring — buildConceptRedesignInput sends mood_id + fixed prompt", () => {
  const payload = buildConceptRedesignInput({
    baseRevisionId: "rev-1",
    baseAssetId: "asset-rgb",
    moodId: "warm-minimal"
  });
  assert.deepEqual(payload, {
    base_revision_id: "rev-1",
    base_asset_id: "asset-rgb",
    prompt: MOOD_CTA_PROMPT,
    strength: 0.6,
    mood_id: "warm-minimal"
  });
  // The prompt is the fixed short intent; the BE prepends the direction.
  assert.ok(payload.prompt.length > 0);
  // Unknown/stale mood ids normalize to the default preset id.
  assert.equal(
    buildConceptRedesignInput({
      baseRevisionId: "rev",
      baseAssetId: "asset",
      moodId: "gone"
    }).mood_id,
    DEFAULT_MOOD_ID
  );
  // Strength stays at the panel default window.
  assert.ok(payload.strength >= 0.2 && payload.strength <= 0.95);
});

test("#197: captionMoodTitle — the generation's own mood_title wins over the selector", () => {
  // BE services/generations.py stamps mood_id + mood_title into
  // structured_conditioning; the worker mirrors the manifest into
  // job.result.generation_manifest (shape used by the DesignPage call site).
  const generated = {
    manifest: {
      structured_conditioning: {
        purpose: "room_redesign",
        mood_id: "soft-classic",
        mood_title: "Мягкая классика"
      }
    }
  };
  // The selector has moved on to another preset — the sketch keeps its own label.
  assert.equal(captionMoodTitle(generated, "warm-minimal"), "Мягкая классика");
  // api.ts Generation shape (manifest.structured_conditioning) behaves the same.
  assert.equal(captionMoodTitle(generated, DEFAULT_MOOD_ID), "Мягкая классика");
  // Blank/whitespace titles are not usable — fall through to the preset.
  assert.equal(
    captionMoodTitle(
      { manifest: { structured_conditioning: { mood_title: "   " } } },
      "warm-minimal"
    ),
    "Тёплый минимализм"
  );
  // Non-string junk is ignored, never leaked into the caption.
  assert.equal(
    captionMoodTitle(
      { manifest: { structured_conditioning: { mood_title: 42 } } },
      "warm-minimal"
    ),
    "Тёплый минимализм"
  );
});

test("#197: captionMoodTitle — without a stamped mood falls back to the current preset", () => {
  // conditioning present but no mood_title (e.g. non-mood redesign pipelines).
  assert.equal(
    captionMoodTitle(
      { manifest: { structured_conditioning: { purpose: "room_redesign" } } },
      "family-practical"
    ),
    "Практично для жизни"
  );
  // No conditioning at all — same fallback, matching the pre-fix behavior.
  assert.equal(captionMoodTitle({ manifest: {} }, "warm-minimal"), "Тёплый минимализм");
  assert.equal(captionMoodTitle({ manifest: null }, "warm-minimal"), "Тёплый минимализм");
  assert.equal(captionMoodTitle(null, "warm-minimal"), "Тёплый минимализм");
  // Nothing usable anywhere (unknown fallback id) → null: the call site shows
  // the plain CONCEPT_GENERATED_LABEL without a dangling mood suffix.
  assert.equal(captionMoodTitle(null, "gone"), null);
  assert.equal(captionMoodTitle({ manifest: {} }, "gone"), null);
});
