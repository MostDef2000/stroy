from __future__ import annotations

import math

from stroy.domain.models import Camera


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [
            sum(a[row][k] * b[k][col] for k in range(3))
            for col in range(3)
        ]
        for row in range(3)
    ]


def _rotation_matrix_xyz(rotation_deg: tuple[float, float, float]) -> list[list[float]]:
    rx, ry, rz = (math.radians(value) for value in rotation_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    x = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    y = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    z = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _mat_mul(z, _mat_mul(y, x))


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [[matrix[col][row] for col in range(3)] for row in range(3)]


def _mat_vec(matrix: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(
        sum(matrix[row][col] * vector[col] for col in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def transform_local_point(
    translation_mm: tuple[float, float, float],
    rotation_deg: tuple[float, float, float],
    local_mm: tuple[float, float, float],
) -> tuple[float, float, float]:
    rotation = _rotation_matrix_xyz(rotation_deg)
    rotated = _mat_vec(rotation, local_mm)
    return tuple(
        translation_mm[index] + rotated[index]
        for index in range(3)
    )  # type: ignore[return-value]


def project_world_point(
    camera: Camera,
    world_mm: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Project canonical +Z-up world point using Blender/Three camera convention.

    Camera transform is the camera object's world pose using XYZ Euler angles.
    Local camera forward is -Z and local +Y is image-up.
    """
    translation = camera.transform.translation_mm
    delta = tuple(
        world_mm[index] - translation[index]
        for index in range(3)
    )
    world_from_camera = _rotation_matrix_xyz(camera.transform.rotation_deg)
    camera_from_world = _transpose(world_from_camera)
    x, y, z = _mat_vec(camera_from_world, delta)
    depth = -z
    if depth <= 0:
        raise ValueError("point is behind camera")

    u = camera.intrinsics.fx * (x / depth) + camera.intrinsics.cx
    v = camera.intrinsics.cy - camera.intrinsics.fy * (y / depth)
    return (u, v, depth)


def camera_residual_px(camera: Camera) -> float | None:
    calibration = camera.calibration
    if calibration is None or not calibration.observations:
        return None

    squared_error = 0.0
    valid = 0
    for observation in calibration.observations:
        try:
            u, v, _ = project_world_point(camera, observation.world_mm)
        except ValueError:
            continue
        du = u - observation.image_px[0]
        dv = v - observation.image_px[1]
        squared_error += du * du + dv * dv
        valid += 1
    if valid == 0:
        return None
    return math.sqrt(squared_error / valid)
