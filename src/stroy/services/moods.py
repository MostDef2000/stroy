"""R9: editorial mood presets for AI concept generation.

Backend-owned single source of truth. The API validates a request's
``mood_id`` against this registry and prepends the preset's editorial
direction text (``prompt_prefix``) to the user prompt before the redesign
job is queued. Presets are intentionally small, curated, and composed in
editorial Russian so the generation direction reads naturally.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MoodPreset:
    id: str
    title: str
    prompt_prefix: str


MOOD_PRESETS: tuple[MoodPreset, ...] = (
    MoodPreset(
        id="calm-contemporary",
        title="Спокойный современный",
        prompt_prefix=(
            "Спокойный современный интерьер: нейтральная тёплая палитра, "
            "натуральные материалы, сбалансированный свет, соразмерная "
            "мебель."
        ),
    ),
    MoodPreset(
        id="warm-minimal",
        title="Тёплый минимализм",
        prompt_prefix=(
            "Тёплый минимализм: лаконичные формы, ограниченная палитра "
            "бежевых и древесных оттенков, мягкий рассеянный свет, много "
            "воздуха и свободных поверхностей."
        ),
    ),
    MoodPreset(
        id="soft-classic",
        title="Мягкая классика",
        prompt_prefix=(
            "Мягкая классика: симметричные спокойные композиции, "
            "филёнчатые фасады и лепнина в приглушённых тонах, тёплый "
            "ламповый свет, добротные ткани без парадности."
        ),
    ),
    MoodPreset(
        id="family-practical",
        title="Практично для жизни",
        prompt_prefix=(
            "Практичный семейный интерьер: износостойкие материалы, "
            "закрытые места хранения, скруглённые безопасные формы, "
            "свободные проходы и удобный для будней свет."
        ),
    ),
)

DEFAULT_MOOD_ID = "calm-contemporary"

_MOODS_BY_ID: dict[str, MoodPreset] = {mood.id: mood for mood in MOOD_PRESETS}


def get_mood(mood_id: str) -> MoodPreset | None:
    """Return the preset for ``mood_id`` or None when unknown."""
    return _MOODS_BY_ID.get(mood_id)


def compose_redesign_request_text(prompt: str, mood: MoodPreset) -> str:
    """Join the mood direction and the user prompt naturally.

    The direction text comes first (it sets the editorial mood); the user's
    own request follows when present so it can refine the preset.
    """
    text = prompt.strip()
    if text:
        return f"{mood.prompt_prefix} {text}"
    return mood.prompt_prefix
