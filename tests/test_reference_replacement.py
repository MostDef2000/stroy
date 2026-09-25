import pytest

from stroy.domain.models import Camera, SceneEntity
from stroy.editing import projected_entity_region


def test_projected_replacement_region_is_inside_calibrated_frame() -> None:
    camera = Camera.model_validate(
        {
            "id": "camera.main",
            "width_px": 1000,
            "height_px": 800,
            "intrinsics": {"fx": 800, "fy": 800, "cx": 500, "cy": 400},
            "transform": {
                "translation_mm": [0, -5000, 1500],
                "rotation_deg": [90, 0, 0],
            },
        }
    )
    entity = SceneEntity.model_validate(
        {
            "id": "object.sofa.main",
            "kind": "furniture",
            "transform": {
                "translation_mm": [0, 0, 900],
                "rotation_deg": [0, 0, 0],
                "scale": [1, 1, 1],
            },
            "geometry": {"dimensions_mm": [2200, 900, 900]},
        }
    )

    region = projected_entity_region(entity, camera)
    x0, y0, x1, y1 = region.bbox_px
    assert 0 <= x0 < x1 <= camera.width_px
    assert 0 <= y0 < y1 <= camera.height_px
    assert region.target_entity_id == entity.id
    assert region.camera_id == camera.id
    assert region.feather_px >= 8


def test_replacement_region_requires_geometry_dimensions() -> None:
    camera = Camera.model_validate(
        {
            "id": "camera.main",
            "width_px": 1000,
            "height_px": 800,
            "intrinsics": {"fx": 800, "fy": 800, "cx": 500, "cy": 400},
            "transform": {
                "translation_mm": [0, -5000, 1500],
                "rotation_deg": [90, 0, 0],
            },
        }
    )
    entity = SceneEntity(id="object.chair.main", kind="furniture")

    with pytest.raises(ValueError, match="requires dimensions"):
        projected_entity_region(entity, camera)
