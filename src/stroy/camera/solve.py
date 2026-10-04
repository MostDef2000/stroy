"""Solve a camera world pose from 2D-3D correspondences.

This module is deliberately stdlib-only (no numpy): the projection conventions
live in :mod:`stroy.camera.projection` and are reused here so the solver and the
residual validator agree byte-for-byte.

v0 assumptions, matching the golden calibration fixture:

* the world is right-handed, +Z up, millimetres;
* the camera transform is the object's world pose with XYZ Euler angles and the
  Blender/Three.js perspective convention (local -Z forward, local +Y up);
* both intrinsics ``fx``/``fy`` are equal and the principal point is centered
  when ``intrinsics`` is not supplied::

      fx = fy = 1.1 * max(width_px, height_px)
      cx = width_px / 2, cy = height_px / 2

* the lens has zero distortion.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from stroy.camera.projection import _rotation_matrix_xyz, project_world_point
from stroy.domain.models import (
    Camera,
    CameraIntrinsics,
    CameraObservation,
    CameraTransform,
)

_MIN_OBSERVATIONS = 3
_MAX_ITERATIONS = 200
_RMS_TOL_PX = 1e-7
_ROTATION_FD_STEP_RAD = 1e-6
_SINGULAR_RANK_TOL = 1e-9

Vector = tuple[float, float, float]
Matrix = list[list[float]]


@dataclass(frozen=True)
class SolveResult:
    """Outcome of :func:`solve_camera_pose`."""

    transform: CameraTransform
    residual_px: float
    converged: bool
    iterations: int


def default_intrinsics(width_px: int, height_px: int) -> CameraIntrinsics:
    """Return the documented v0 fallback intrinsics for a frame size."""
    focal = 1.1 * max(width_px, height_px)
    return CameraIntrinsics(
        fx=focal,
        fy=focal,
        cx=width_px / 2.0,
        cy=height_px / 2.0,
    )


def solve_camera_pose(
    observations: Sequence[CameraObservation],
    width_px: int,
    height_px: int,
    intrinsics: CameraIntrinsics | None = None,
    initial_transform: CameraTransform | None = None,
) -> SolveResult:
    """Fit a camera world pose to labeled 2D-3D correspondences.

    Minimizes the sum of squared re-projection errors over the six pose
    parameters (three translation components in mm, three XYZ Euler angles in
    radians internally) with a Levenberg-Marquardt iteration.

    ``initial_transform`` seeds the optimisation when given; otherwise a
    centroid/plane-normal heuristic is used and both view directions are tried.

    Raises:
        ValueError: fewer than three observations, a singular normal matrix, or
            failure to converge (degenerate / collinear correspondences).
    """
    correspondences = list(observations)
    if len(correspondences) < _MIN_OBSERVATIONS:
        raise ValueError(
            f"camera pose solve requires at least {_MIN_OBSERVATIONS} observations, "
            f"got {len(correspondences)}"
        )

    resolved_intrinsics = intrinsics or default_intrinsics(width_px, height_px)

    heuristic = _heuristic_candidates(correspondences, resolved_intrinsics)
    if initial_transform is not None:
        # The supplied pose seeds the optimisation, but a poor guess (e.g. the
        # all-zero "unsolved" transform) can start with points behind the camera;
        # keep the centroid heuristic as a fallback candidate.
        candidates = [_params_from_transform(initial_transform), *heuristic]
    else:
        candidates = heuristic

    best: tuple[list[float], float, int] | None = None
    for candidate in candidates:
        try:
            solved = _levenberg_marquardt(
                candidate,
                correspondences,
                width_px,
                height_px,
                resolved_intrinsics,
            )
        except ValueError:
            # A degenerate starting basin (e.g. the mirrored view direction)
            # must not discard another candidate that can converge.
            continue
        if solved is None:
            continue
        if best is None or solved[1] < best[1]:
            best = solved

    if best is None:
        raise ValueError(
            "camera pose solve failed: singular normal matrix or degenerate correspondences"
        )

    params, residual_px, iterations = best
    if residual_px > _RMS_TOL_PX:
        raise ValueError(
            "camera pose solve did not converge "
            f"(residual {residual_px:.6g} px): degenerate correspondences"
        )

    return SolveResult(
        transform=_transform_from_params(params),
        residual_px=residual_px,
        converged=True,
        iterations=iterations,
    )


# --------------------------------------------------------------------------- #
# Parameter helpers
# --------------------------------------------------------------------------- #


def _params_from_transform(transform: CameraTransform) -> list[float]:
    tx, ty, tz = transform.translation_mm
    rx, ry, rz = (math.radians(value) for value in transform.rotation_deg)
    return [tx, ty, tz, rx, ry, rz]


def _transform_from_params(params: Sequence[float]) -> CameraTransform:
    return CameraTransform(
        translation_mm=(params[0], params[1], params[2]),
        rotation_deg=(
            math.degrees(params[3]),
            math.degrees(params[4]),
            math.degrees(params[5]),
        ),
    )


def _make_camera(
    params: Sequence[float],
    width_px: int,
    height_px: int,
    intrinsics: CameraIntrinsics,
) -> Camera:
    return Camera(
        id="solve.candidate",
        width_px=width_px,
        height_px=height_px,
        intrinsics=intrinsics,
        transform=_transform_from_params(params),
    )


# --------------------------------------------------------------------------- #
# Residuals and Jacobian
# --------------------------------------------------------------------------- #


def _residuals(
    params: Sequence[float],
    observations: Sequence[CameraObservation],
    width_px: int,
    height_px: int,
    intrinsics: CameraIntrinsics,
) -> list[float] | None:
    camera = _make_camera(params, width_px, height_px, intrinsics)
    residuals: list[float] = []
    for observation in observations:
        try:
            u, v, _ = project_world_point(camera, observation.world_mm)
        except ValueError:
            return None
        residuals.append(u - observation.image_px[0])
        residuals.append(v - observation.image_px[1])
    return residuals


def _mat_vec(matrix: Matrix, vector: Sequence[float]) -> Vector:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )


def _transpose(matrix: Matrix) -> Matrix:
    return [[matrix[col][row] for col in range(3)] for row in range(3)]


def _jacobian_terms(
    params: Sequence[float],
    observations: Sequence[CameraObservation],
    intrinsics: CameraIntrinsics,
) -> list[tuple[list[float], list[float]]] | None:
    """Return per-observation ``(d(u)/dp, d(v)/dp)`` for the 6 parameters.

    Translation derivatives are analytic; rotation derivatives use a central
    finite difference of the shared ``_rotation_matrix_xyz`` convention, which
    keeps the Euler-order math in a single place.
    """
    rotation_deg = (
        math.degrees(params[3]),
        math.degrees(params[4]),
        math.degrees(params[5]),
    )
    world_from_camera = _rotation_matrix_xyz(rotation_deg)
    camera_from_world = _transpose(world_from_camera)
    translation = (params[0], params[1], params[2])

    rotation_jacobians: list[Matrix] = []
    for axis in range(3):
        plus = list(rotation_deg)
        minus = list(rotation_deg)
        plus[axis] += math.degrees(_ROTATION_FD_STEP_RAD)
        minus[axis] -= math.degrees(_ROTATION_FD_STEP_RAD)
        mat_plus = _rotation_matrix_xyz((plus[0], plus[1], plus[2]))
        mat_minus = _rotation_matrix_xyz((minus[0], minus[1], minus[2]))
        # d(camera_from_world)/d(axis) = transpose(d(world_from_camera)/d(axis))
        rotation_jacobians.append(
            _transpose(
                [
                    [
                        (mat_plus[row][col] - mat_minus[row][col])
                        / (2 * _ROTATION_FD_STEP_RAD)
                        for col in range(3)
                    ]
                    for row in range(3)
                ]
            )
        )

    terms: list[tuple[list[float], list[float]]] = []
    for observation in observations:
        delta = tuple(
            observation.world_mm[index] - translation[index] for index in range(3)
        )
        x, y, z = _mat_vec(camera_from_world, delta)
        depth = -z
        if depth <= 0:
            return None

        # dc[coordinate][parameter]
        dc = [[0.0] * 6 for _ in range(3)]
        for param in range(3):
            for coordinate in range(3):
                dc[coordinate][param] = -camera_from_world[coordinate][param]
        for axis in range(3):
            derivative = _mat_vec(rotation_jacobians[axis], delta)
            for coordinate in range(3):
                dc[coordinate][3 + axis] = derivative[coordinate]

        inv_depth_sq = 1.0 / (depth * depth)
        du = [
            intrinsics.fx * (dc[0][param] * depth + x * dc[2][param]) * inv_depth_sq
            for param in range(6)
        ]
        dv = [
            -intrinsics.fy * (dc[1][param] * depth + y * dc[2][param]) * inv_depth_sq
            for param in range(6)
        ]
        terms.append((du, dv))
    return terms


# --------------------------------------------------------------------------- #
# Levenberg-Marquardt
# --------------------------------------------------------------------------- #


def _normal_equations(
    terms: Sequence[tuple[Sequence[float], Sequence[float]]],
    residuals: Sequence[float],
) -> tuple[Matrix, list[float]]:
    matrix = [[0.0] * 6 for _ in range(6)]
    gradient = [0.0] * 6
    for index, (du, dv) in enumerate(terms):
        ru = residuals[2 * index]
        rv = residuals[2 * index + 1]
        for a in range(6):
            gradient[a] += du[a] * ru + dv[a] * rv
            row = matrix[a]
            for b in range(a, 6):
                row[b] += du[a] * du[b] + dv[a] * dv[b]
    for a in range(6):
        for b in range(a + 1, 6):
            matrix[b][a] = matrix[a][b]
    return matrix, gradient


def _is_singular(matrix: Matrix) -> bool:
    work = [row[:] for row in matrix]
    scale = max((abs(value) for row in work for value in row), default=0.0)
    if scale == 0.0:
        return True
    for column in range(6):
        pivot_row = max(range(column, 6), key=lambda row: abs(work[row][column]))
        if abs(work[pivot_row][column]) < _SINGULAR_RANK_TOL * scale:
            return True
        work[column], work[pivot_row] = work[pivot_row], work[column]
        pivot = work[column][column]
        for row in range(column + 1, 6):
            factor = work[row][column] / pivot
            if factor == 0.0:
                continue
            for col in range(column, 6):
                work[row][col] -= factor * work[column][col]
    return False


def _solve_linear(matrix: Matrix, rhs: list[float]) -> list[float] | None:
    work = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    for column in range(6):
        pivot_row = max(range(column, 6), key=lambda row: abs(work[row][column]))
        if abs(work[pivot_row][column]) < 1e-18:
            return None
        work[column], work[pivot_row] = work[pivot_row], work[column]
        pivot = work[column][column]
        for col in range(column, 7):
            work[column][col] /= pivot
        for row in range(6):
            if row == column:
                continue
            factor = work[row][column]
            if factor == 0.0:
                continue
            for col in range(column, 7):
                work[row][col] -= factor * work[column][col]
    return [work[row][6] for row in range(6)]


def _damped(matrix: Matrix, damping: float) -> Matrix:
    damped = [row[:] for row in matrix]
    for index in range(6):
        diagonal = damped[index][index]
        damped[index][index] = diagonal + damping * max(abs(diagonal), 1e-12)
    return damped


def _levenberg_marquardt(
    params: Sequence[float],
    observations: Sequence[CameraObservation],
    width_px: int,
    height_px: int,
    intrinsics: CameraIntrinsics,
) -> tuple[list[float], float, int] | None:
    current = list(params)
    residuals = _residuals(current, observations, width_px, height_px, intrinsics)
    if residuals is None:
        return None
    cost = sum(value * value for value in residuals)
    cost_tolerance = 2 * len(observations) * _RMS_TOL_PX * _RMS_TOL_PX
    damping = 1e-3
    iterations = 0

    for _ in range(_MAX_ITERATIONS):
        iterations += 1
        if cost <= cost_tolerance:
            break
        terms = _jacobian_terms(current, observations, intrinsics)
        if terms is None:
            return None
        matrix, gradient = _normal_equations(terms, residuals)
        if _is_singular(matrix):
            raise ValueError(
                "camera pose solve failed: singular normal matrix "
                "(degenerate correspondences)"
            )

        improved = False
        for _ in range(24):
            step = _solve_linear(
                _damped(matrix, damping),
                [-value for value in gradient],
            )
            if step is None:
                damping *= 10.0
                continue
            trial = [current[index] + step[index] for index in range(6)]
            trial_residuals = _residuals(
                trial, observations, width_px, height_px, intrinsics
            )
            if trial_residuals is None:
                damping *= 10.0
                continue
            trial_cost = sum(value * value for value in trial_residuals)
            if trial_cost < cost:
                current = trial
                residuals = trial_residuals
                cost = trial_cost
                damping = max(damping * 0.3, 1e-12)
                improved = True
                break
            damping *= 10.0

        if not improved:
            break

    rms = math.sqrt(cost / (2 * len(observations)))
    return current, rms, iterations


# --------------------------------------------------------------------------- #
# Initialization heuristic
# --------------------------------------------------------------------------- #


def _heuristic_candidates(
    observations: Sequence[CameraObservation],
    intrinsics: CameraIntrinsics,
) -> list[list[float]]:
    points = [observation.world_mm for observation in observations]
    count = len(points)
    centroid = tuple(
        sum(point[axis] for point in points) / count for axis in range(3)
    )

    covariance = [[0.0] * 3 for _ in range(3)]
    for point in points:
        offset = tuple(point[axis] - centroid[axis] for axis in range(3))
        for a in range(3):
            for b in range(3):
                covariance[a][b] += offset[a] * offset[b]
    eigenvalues, eigenvectors = _jacobi_eigen(covariance)
    smallest = min(range(3), key=lambda index: eigenvalues[index])
    normal = _normalize(tuple(eigenvectors[smallest]))

    world_extent = _max_pairwise_distance(points)
    image_extent = _max_pairwise_distance(
        [observation.image_px for observation in observations]
    )
    if image_extent <= 1e-9:
        image_extent = 1.0
    focal = 0.5 * (intrinsics.fx + intrinsics.fy)
    depth = focal * world_extent / image_extent
    if not math.isfinite(depth) or depth <= 0.0:
        depth = max(world_extent, 1.0)

    candidates: list[list[float]] = []
    for sign in (1.0, -1.0):
        forward = _normalize((sign * normal[0], sign * normal[1], sign * normal[2]))
        up: Vector = (0.0, 0.0, 1.0)
        if abs(_dot(forward, up)) > 0.99:
            up = (0.0, 1.0, 0.0)
        rotation = _look_at(forward, up)
        rx, ry, rz = _euler_xyz_from_matrix(rotation)
        position = tuple(
            centroid[axis] - depth * forward[axis] for axis in range(3)
        )
        candidates.append(
            [
                position[0],
                position[1],
                position[2],
                math.radians(rx),
                math.radians(ry),
                math.radians(rz),
            ]
        )
    return candidates


def _normalize(vector: Sequence[float]) -> Vector:
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return (0.0, 0.0, 1.0)
    return (
        vector[0] / norm,
        vector[1] / norm,
        vector[2] / norm,
    )


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Sequence[float], b: Sequence[float]) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _max_pairwise_distance(points: Sequence[Sequence[float]]) -> float:
    best = 0.0
    for first in range(len(points)):
        for second in range(first + 1, len(points)):
            distance = math.dist(points[first], points[second])
            if distance > best:
                best = distance
    return best


def _look_at(forward: Vector, up: Vector) -> Matrix:
    z_axis = _normalize((-forward[0], -forward[1], -forward[2]))
    x_axis = _normalize(_cross(up, z_axis))
    y_axis = _cross(z_axis, x_axis)
    return [
        [x_axis[0], y_axis[0], z_axis[0]],
        [x_axis[1], y_axis[1], z_axis[1]],
        [x_axis[2], y_axis[2], z_axis[2]],
    ]


def _euler_xyz_from_matrix(matrix: Matrix) -> Vector:
    sin_y = max(-1.0, min(1.0, -matrix[2][0]))
    ry = math.asin(sin_y)
    if abs(matrix[2][0]) < 0.999999:
        rx = math.atan2(matrix[2][1], matrix[2][2])
        rz = math.atan2(matrix[1][0], matrix[0][0])
    else:
        rz = 0.0
        if sin_y > 0.0:
            rx = math.atan2(matrix[0][1], matrix[1][1])
        else:
            rx = math.atan2(-matrix[0][1], matrix[1][1])
    return (math.degrees(rx), math.degrees(ry), math.degrees(rz))


def _jacobi_eigen(matrix: Matrix) -> tuple[list[float], Matrix]:
    work = [row[:] for row in matrix]
    vectors = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(100):
        off_diagonal = math.sqrt(
            work[0][1] ** 2 + work[0][2] ** 2 + work[1][2] ** 2
        )
        if off_diagonal < 1e-15:
            break
        for p in range(3):
            for q in range(p + 1, 3):
                if abs(work[p][q]) < 1e-18:
                    continue
                theta = 0.5 * math.atan2(
                    2 * work[p][q], work[q][q] - work[p][p]
                )
                cos_theta = math.cos(theta)
                sin_theta = math.sin(theta)
                for row in range(3):
                    a_p = work[row][p]
                    a_q = work[row][q]
                    work[row][p] = cos_theta * a_p - sin_theta * a_q
                    work[row][q] = sin_theta * a_p + cos_theta * a_q
                for column in range(3):
                    a_p = work[p][column]
                    a_q = work[q][column]
                    work[p][column] = cos_theta * a_p - sin_theta * a_q
                    work[q][column] = sin_theta * a_p + cos_theta * a_q
                for row in range(3):
                    v_p = vectors[row][p]
                    v_q = vectors[row][q]
                    vectors[row][p] = cos_theta * v_p - sin_theta * v_q
                    vectors[row][q] = sin_theta * v_p + cos_theta * v_q
    eigenvalues = [work[index][index] for index in range(3)]
    eigenvectors = [
        [vectors[row][index] for row in range(3)] for index in range(3)
    ]
    return eigenvalues, eigenvectors
