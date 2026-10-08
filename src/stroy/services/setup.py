"""R6 guided setup: compose the project setup snapshot from existing data (#183).

Read-only composition for ``GET /api/v1/projects/{project_id}/setup``. Every
flag is derived from data that already exists — assets, the latest
``PlanDraftRow`` (the GET /plan/draft source), the canonical current scene via
``latest_revision`` (variant-excluding) and the attachments list. No new
tables, no migration, no heavy geometry recomputation: the scene is parsed
from the stored revision snapshot as-is.

Wire keys are English; label/copy strings are Russian (owner-facing UI).
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stroy.db.models import AssetRow, AttachmentRow
from stroy.domain.models import EntityKind, EntityState, Scene
from stroy.domain.plan import PlanDraft, PlanScaleSource
from stroy.services.plans import get_latest_draft
from stroy.services.scenes import latest_revision

# Fixed journey (R6): the first unmet gate is the current step.
_STEPS: list[tuple[str, int, str]] = [
    ("upload_plan", 1, "Загрузить план"),
    ("check_rooms_scale", 2, "Проверить комнаты и размеры"),
    ("create_3d", 3, "Создать 3D"),
    ("add_photos", 4, "Добавить реальные фото"),
    ("map_photos", 5, "Привязать фото к комнатам"),
    ("ready", 6, "Готово — перейти к дизайну"),
]

# (action id, label, page) shown as the primary CTA for the current step.
_NEXT_ACTIONS: dict[str, tuple[str, str, str]] = {
    "upload_plan": ("upload_plan", "Загрузить план квартиры", "plan"),
    "check_rooms_scale": ("review_rooms", "Проверить комнаты и размеры", "plan"),
    "create_3d": ("commit_geometry", "Создать 3D из плана", "plan"),
    "add_photos": ("add_photos", "Добавить реальные фото", "design"),
    "map_photos": ("map_photos", "Привязать фото к комнатам", "design"),
    "ready": ("open_design", "Перейти к дизайну", "design"),
}

# Flag -> owner-facing sentence for what the system already knows.
_KNOWLEDGE: dict[str, str] = {
    "plan_uploaded": "План квартиры загружен",
    "scale_known": "Масштаб плана определён",
    "geometry_draft": "В черновике плана есть комнаты и стены",
    "geometry_confirmed": "3D создано из плана",
    "room_labels": "У комнат есть названия",
    "photos_added": "Добавлены реальные фото",
    "photo_mapping": "Фото привязаны к комнатам",
}

# Flag -> what the owner still has to confirm/do (order follows the journey).
_CONFIRMATIONS: dict[str, str] = {
    "plan_uploaded": "План квартиры ещё не загружен",
    "scale_known": "Масштаб плана не задан — задайте размеры по плану",
    "geometry_draft": "В черновике плана пока нет комнат и стен",
    "geometry_confirmed": "Проверьте комнаты и создайте 3D из плана",
    "room_labels": "Проверьте и подпишите названия комнат",
    "photos_added": "Добавьте реальные фото комнат",
    "photo_mapping": "Привяжите фото к комнатам",
}

_FLAG_ORDER = (
    "plan_uploaded",
    "scale_known",
    "geometry_draft",
    "geometry_confirmed",
    "room_labels",
    "photos_added",
    "photo_mapping",
)


def _is_image(asset: AssetRow) -> bool:
    # Reviewer F1 (intended): only image media count toward plan/photo flags
    # and their diagnostics; non-image assets (e.g. PDFs) never qualify.
    media_type = asset.media_type or ""
    return media_type.lower().startswith("image/")


def _parse_draft(draft_row: Any) -> PlanDraft | None:
    """Latest draft payload, or None when absent/corrupt (read-only safety)."""
    if draft_row is None:
        return None
    try:
        return PlanDraft.model_validate(draft_row.draft_json)
    except ValidationError:
        return None


def _parse_scene(revision: Any) -> Scene | None:
    """Canonical scene from the latest revision, or None when absent/corrupt.

    Symmetric with ``_parse_draft``: a malformed stored ``scene_json`` must
    degrade the read-only setup snapshot ("no canonical scene" — scene-derived
    flags false, room_count 0), never a 500.
    """
    if revision is None:
        return None
    try:
        return Scene.model_validate(revision.scene_json)
    except ValidationError:
        return None


def compose_setup(
    project_id: str,
    assets: list[AssetRow],
    attachments: list[AttachmentRow],
    draft_row: Any,
    scene: Scene | None,
) -> dict[str, Any]:
    """Compose the setup payload from already-loaded data (no extra queries).

    ``draft_row`` is the latest ``PlanDraftRow`` (or None); ``scene`` is the
    canonical current scene parsed from the latest revision (or None).
    """
    draft = _parse_draft(draft_row)

    # --- assets: role "plan" (new) vs role "apartment" (legacy) --------------
    plan_asset_count = sum(1 for a in assets if a.role == "plan" and _is_image(a))
    legacy_apartment_asset_count = sum(
        1 for a in assets if a.role == "apartment" and _is_image(a)
    )
    plan_uploaded = plan_asset_count + legacy_apartment_asset_count > 0

    # --- draft-derived flags --------------------------------------------------
    committed = draft_row is not None and draft_row.status == "committed"
    draft_room_ids = (
        {room.id for floor in draft.floors for room in floor.rooms} if draft else set()
    )
    draft_rooms = (
        [room for floor in draft.floors for room in floor.rooms] if draft else []
    )

    scale_known = draft is not None and draft.scale.source != PlanScaleSource.UNKNOWN
    geometry_draft = draft is not None and any(
        floor.rooms or floor.walls for floor in draft.floors
    )
    # A committed draft alone is not enough: the canonical current scene must
    # actually contain the room entities built from that draft (plans.py maps
    # draft rooms to kind=room/structure entities with the same id). Demo or
    # golden-room scenes have no committed draft and never confirm geometry.
    geometry_confirmed = bool(
        committed
        and scene is not None
        and any(
            entity.kind == EntityKind.ROOM
            and entity.state == EntityState.STRUCTURE
            and entity.id in draft_room_ids
            for entity in scene.entities
        )
    )

    # Room labels: pre-commit they live in the draft; post-commit in the
    # canonical room entities built from the committed draft (same id set).
    if committed and scene is not None:
        label_values = [
            entity.display_name
            for entity in scene.entities
            if entity.kind == EntityKind.ROOM and entity.id in draft_room_ids
        ]
    else:
        label_values = [room.name for room in draft_rooms]
    room_labels = bool(label_values) and all(
        label is not None and label.strip() != "" for label in label_values
    )

    # --- photos ----------------------------------------------------------------
    assets_by_id = {asset.id: asset for asset in assets}
    photo_role_assets = [
        asset for asset in assets if asset.role == "photo" and _is_image(asset)
    ]

    def _photo_ref(asset_id: str) -> bool:
        asset = assets_by_id.get(asset_id)
        # Plan images never count as photos, whatever references them.
        return asset is not None and asset.role != "plan" and _is_image(asset)

    legacy_photo_attachment = any(
        attachment.kind == "photo"
        and attachment.asset_id is not None
        and _photo_ref(attachment.asset_id)
        for attachment in attachments
    )
    camera_photo = any(
        camera.source_asset_id is not None and _photo_ref(camera.source_asset_id)
        for camera in scene.cameras
    ) if scene is not None else False
    photos_added = bool(photo_role_assets) or legacy_photo_attachment or camera_photo

    # --- photo -> room mapping ---------------------------------------------------
    canonical_room_ids = (
        {
            entity.id
            for entity in scene.entities
            if entity.kind == EntityKind.ROOM
        }
        if scene is not None
        else set()
    )
    room_photo_attachments = [
        attachment
        for attachment in attachments
        if attachment.target_type == "room"
        and attachment.kind == "photo"
        and attachment.asset_id is not None
    ]
    mapped_room_photos = [
        attachment
        for attachment in room_photo_attachments
        if attachment.target_id in canonical_room_ids
    ]
    dangling_room_photo_attachments = [
        attachment
        for attachment in room_photo_attachments
        if attachment.target_id not in canonical_room_ids
    ]
    photo_mapping = bool(mapped_room_photos)

    flags = {
        "plan_uploaded": plan_uploaded,
        "scale_known": scale_known,
        "geometry_draft": geometry_draft,
        "geometry_confirmed": geometry_confirmed,
        "room_labels": room_labels,
        "photos_added": photos_added,
        "photo_mapping": photo_mapping,
    }
    flags["ready_for_design"] = all(flags[name] for name in _FLAG_ORDER)

    # --- journey: first unmet gate is the current step ---------------------------
    gates = [
        plan_uploaded,
        scale_known and geometry_draft and room_labels,
        geometry_confirmed,
        photos_added,
        photo_mapping,
    ]
    step_index = next((i for i, met in enumerate(gates) if not met), len(gates))
    step_id, step_number, step_label = _STEPS[step_index]

    action_id, action_label, page = _NEXT_ACTIONS[step_id]
    disabled = False
    reason: str | None = None
    if step_id == "create_3d" and not scale_known:
        disabled = True
        reason = "Сначала задайте масштаб плана"
    elif step_id == "map_photos" and not geometry_confirmed:
        disabled = True
        reason = "Сначала создайте 3D из плана"

    return {
        "project_id": project_id,
        "flags": flags,
        "current_step": {"id": step_id, "number": step_number, "label": step_label},
        "what_stroy_knows": [_KNOWLEDGE[name] for name in _FLAG_ORDER if flags[name]],
        "must_confirm": [
            _CONFIRMATIONS[name] for name in _FLAG_ORDER if not flags[name]
        ],
        "next_action": {
            "id": action_id,
            "label": action_label,
            "page": page,
            "primary": True,
            "disabled": disabled,
            "reason": reason,
        },
        "diagnostics": {
            "plan_asset_count": plan_asset_count,
            "legacy_apartment_asset_count": legacy_apartment_asset_count,
            "room_count": len(canonical_room_ids),
            "room_photo_attachment_count": len(mapped_room_photos),
            "dangling_room_photo_attachment_count": len(dangling_room_photo_attachments),
        },
    }


async def build_setup(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """Load the four existing sources and compose the setup snapshot."""
    assets = list(
        (
            await session.execute(
                select(AssetRow).where(AssetRow.project_id == project_id)
            )
        ).scalars()
    )
    attachments = list(
        (
            await session.execute(
                select(AttachmentRow).where(AttachmentRow.project_id == project_id)
            )
        ).scalars()
    )
    draft_row = await get_latest_draft(session, project_id)
    revision = await latest_revision(session, project_id)
    scene = _parse_scene(revision)
    return compose_setup(project_id, assets, attachments, draft_row, scene)
