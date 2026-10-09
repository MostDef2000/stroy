// Pure helpers for the R9 editorial mood selector (#197): the client-side
// mirror of the backend mood registry (BE services/moods.py), per-project
// localStorage persistence and the «Сделать эскиз концепта» redesign payload.
// No React and no network so the module is unit-tested in isolation (see
// tests/moodSelect.test.mjs). The BE owns the editorial direction text — the
// FE sends mood_id only and the backend composes the final prompt.

import { REDESIGN_STRENGTH_DEFAULT } from "./twinDesign.js";

export type MoodPreset = {
  id: string;
  title: string;
  hint: string;
};

/**
 * Mirror of BE services/moods.py MOOD_PRESETS: ids + RU titles + short UI
 * hints. The long editorial prompt text stays server-side — changing a preset
 * here without the BE registry makes the API answer 422 unknown_mood, so the
 * two lists must move together.
 */
export const MOOD_PRESETS: readonly MoodPreset[] = [
  {
    id: "calm-contemporary",
    title: "Спокойный современный",
    hint: "Нейтральная тёплая палитра, натуральные материалы, сбалансированный свет"
  },
  {
    id: "warm-minimal",
    title: "Тёплый минимализм",
    hint: "Лаконичные формы, бежево-древесная палитра, много воздуха"
  },
  {
    id: "soft-classic",
    title: "Мягкая классика",
    hint: "Спокойные симметричные композиции, приглушённые тона, тёплый ламповый свет"
  },
  {
    id: "family-practical",
    title: "Практично для жизни",
    hint: "Износостойкие материалы, закрытое хранение, безопасные скруглённые формы"
  }
];

export const DEFAULT_MOOD_ID = "calm-contemporary";

export function moodPreset(moodId: string): MoodPreset | null {
  return MOOD_PRESETS.find((preset) => preset.id === moodId) ?? null;
}

/**
 * Normalize any stored/unknown value to a known preset id; unknown falls
 * back to the default so a stale or hand-edited localStorage value can never
 * produce a 422 unknown_mood from the API.
 */
export function normalizeMoodId(value: unknown): string {
  return typeof value === "string" && moodPreset(value) !== null
    ? value
    : DEFAULT_MOOD_ID;
}

export function moodStorageKey(projectId: string): string {
  return `stroy.mood.${projectId}`;
}

/** Minimal storage surface (localStorage-shaped) so tests can inject a fake. */
export type MoodStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

function defaultStorage(): MoodStorage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

/** Restore the project's mood; unknown/corrupt values read as the default. */
export function loadMoodId(
  projectId: string,
  storage: MoodStorage | null | undefined = defaultStorage()
): string {
  try {
    return normalizeMoodId(storage?.getItem(moodStorageKey(projectId)));
  } catch {
    return DEFAULT_MOOD_ID;
  }
}

/** Best-effort persistence: private mode / quota errors never break the UI. */
export function saveMoodId(
  projectId: string,
  moodId: string,
  storage: MoodStorage | null | undefined = defaultStorage()
): void {
  try {
    storage?.setItem(moodStorageKey(projectId), normalizeMoodId(moodId));
  } catch {
    /* ignore */
  }
}

/**
 * The fixed owner intent sent with the mood so the CTA works without any
 * typed instruction (BE RedesignRequest requires a non-empty prompt; the
 * mood direction is prepended server-side).
 */
export const MOOD_CTA_PROMPT = "эскиз концепта комнаты";

export type ConceptDraft = {
  baseRevisionId: string;
  baseAssetId: string;
  moodId: string;
};

export type ConceptRedesignPayload = {
  base_revision_id: string;
  base_asset_id: string;
  prompt: string;
  strength: number;
  mood_id: string;
};

/**
 * Minimal structural view of a generation record for caption purposes.
 * Satisfied by api.ts Generation and by `{ manifest: job.result.
 * generation_manifest }` (the worker mirrors the full manifest into the job
 * result, worker/runtime.py). Unknown-typed on purpose: the manifest shape is
 * BE-owned and the helper narrows defensively.
 */
export type ConceptGenerationLike = {
  manifest?: unknown;
};

/**
 * Build the POST /redesigns body for the mood CTA: the mood drives the
 * editorial direction, the prompt is the fixed short intent and the strength
 * stays at the panel default. Structural shape mirrors twinDesign's
 * buildRedesignInput so the api.createRedesign call site stays thin.
 */
export function buildConceptRedesignInput(
  draft: ConceptDraft
): ConceptRedesignPayload {
  return {
    base_revision_id: draft.baseRevisionId,
    base_asset_id: draft.baseAssetId,
    prompt: MOOD_CTA_PROMPT,
    strength: REDESIGN_STRENGTH_DEFAULT,
    mood_id: normalizeMoodId(draft.moodId)
  };
}

/**
 * Mood title for the concept-result caption, read from the generation record
 * itself: BE services/generations.py stamps `mood_id`/`mood_title` into the
 * manifest's structured_conditioning when a preset was applied, so a sketch
 * generated under an earlier mood keeps its own label even after the selector
 * moves on (the CURRENT preset is only a fallback, never the source of truth).
 * Falls back to the fallback id's preset title — the pre-fix behavior — when
 * the record carries no usable mood_title; null (callers show the plain
 * label) only when that fallback id is unknown too.
 */
export function captionMoodTitle(
  generation: ConceptGenerationLike | null | undefined,
  fallbackMoodId: string
): string | null {
  const manifest = generation?.manifest;
  const conditioning =
    manifest && typeof manifest === "object"
      ? (manifest as { structured_conditioning?: unknown }).structured_conditioning
      : undefined;
  const title =
    conditioning && typeof conditioning === "object"
      ? (conditioning as Record<string, unknown>)["mood_title"]
      : undefined;
  if (typeof title === "string" && title.trim().length > 0) return title;
  return moodPreset(fallbackMoodId)?.title ?? null;
}
