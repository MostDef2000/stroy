import json
from pathlib import Path

import pytest

from stroy.camera import camera_residual_px, project_world_point
from stroy.domain.models import Camera


ROOT = Path(__file__).resolve().parents[1]


def fixture() -> dict:
    return json.loads(
        (ROOT / "fixtures" / "golden-camera.correspondences.json").read_text(
            encoding="utf-8"
        )
    )


def test_golden_camera_projects_protected_points_within_tolerance() -> None:
    data = fixture()
    camera = Camera.model_validate(data["camera"])
    residual = camera_residual_px(camera)
    assert residual is not None
    assert residual <= data["tolerance_px"]

    for observation in camera.calibration.observations:
        u, v, depth = project_world_point(camera, observation.world_mm)
        assert depth > 0
        assert u == pytest.approx(observation.image_px[0], abs=data["tolerance_px"])
        assert v == pytest.approx(observation.image_px[1], abs=data["tolerance_px"])


def test_point_behind_camera_is_rejected() -> None:
    camera = Camera.model_validate(fixture()["camera"])
    with pytest.raises(ValueError, match="behind camera"):
        project_world_point(camera, (0, -6000, 1500))
