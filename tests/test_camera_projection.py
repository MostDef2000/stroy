import json
import math
from pathlib import Path

import pytest

from stroy.camera import camera_residual_px, project_world_point
from stroy.camera.solve import solve_camera_pose
from stroy.domain.models import Camera, CameraObservation


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


def _assert_pose_close(
    solved: Camera,
    expected_translation: tuple[float, float, float],
    expected_rotation: tuple[float, float, float],
) -> None:
    assert math.dist(
        solved.transform.translation_mm, expected_translation
    ) <= 0.01 * math.dist(expected_translation, (0.0, 0.0, 0.0))
    for solved_angle, expected_angle in zip(
        solved.transform.rotation_deg, expected_rotation, strict=True
    ):
        assert solved_angle == pytest.approx(expected_angle, abs=0.5)


def test_solve_recovers_ground_truth_from_synthetic_correspondences() -> None:
    camera = Camera.model_validate(fixture()["camera"])
    world_points = [
        (-1500.0, 0.0, 700.0),
        (1500.0, 0.0, 700.0),
        (1500.0, 0.0, 2300.0),
        (-1500.0, 0.0, 2300.0),
        (0.0, 0.0, 1500.0),
        (800.0, 0.0, 1500.0),
        (-800.0, 0.0, 2000.0),
        (0.0, 0.0, 1000.0),
    ]
    observations = []
    for world_mm in world_points:
        u, v, _ = project_world_point(camera, world_mm)
        observations.append(CameraObservation(world_mm=world_mm, image_px=(u, v)))

    result = solve_camera_pose(
        observations,
        camera.width_px,
        camera.height_px,
        intrinsics=camera.intrinsics,
    )
    assert result.converged is True
    assert result.residual_px < 1e-3
    solved = Camera.model_validate(
        {
            **camera.model_dump(mode="json", exclude_none=True),
            "transform": result.transform.model_dump(mode="json"),
        }
    )
    _assert_pose_close(
        solved,
        camera.transform.translation_mm,
        camera.transform.rotation_deg,
    )


def test_solve_matches_golden_fixture_transform() -> None:
    data = fixture()
    camera = Camera.model_validate(data["camera"])
    result = solve_camera_pose(
        camera.calibration.observations,
        camera.width_px,
        camera.height_px,
        intrinsics=camera.intrinsics,
        initial_transform=camera.transform,
    )
    assert result.converged is True
    assert result.residual_px <= data["tolerance_px"]
    solved = Camera.model_validate(
        {
            **camera.model_dump(mode="json", exclude_none=True),
            "transform": result.transform.model_dump(mode="json"),
        }
    )
    _assert_pose_close(
        solved,
        camera.transform.translation_mm,
        camera.transform.rotation_deg,
    )


def test_solve_rejects_collinear_correspondences() -> None:
    observations = [
        CameraObservation(
            world_mm=(index * 500.0, 0.0, index * 500.0),
            image_px=(100.0 + index * 10.0, 200.0),
        )
        for index in range(3)
    ]
    with pytest.raises(ValueError, match="singular|degenerate|converge"):
        solve_camera_pose(observations, 1000, 800)


def test_solve_requires_at_least_three_observations() -> None:
    camera = Camera.model_validate(fixture()["camera"])
    with pytest.raises(ValueError, match="at least 3 observations"):
        solve_camera_pose(
            camera.calibration.observations[:2],
            camera.width_px,
            camera.height_px,
            intrinsics=camera.intrinsics,
        )
