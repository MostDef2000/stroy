# Camera conventions

STROY stores one canonical camera contract that is consumed by Blender, the web viewer and calibration diagnostics.

## Coordinate system

World coordinates are right-handed with **+Z up** and millimetres as canonical units.

A camera transform is the camera object's world pose:

- `translation_mm = [x, y, z]`;
- `rotation_deg = [rx, ry, rz]`;
- XYZ Euler rotation order.

Camera-local axes follow Blender/Three.js perspective-camera convention:

- local **-Z** points forward;
- local **+Y** points up;
- image coordinates start at the top-left;
- `u` grows right and `v` grows down.

Projection therefore uses:

```text
u = fx * Xc / depth + cx
v = cy - fy * Yc / depth
depth = -Zc
```

where the camera-space point is obtained by applying the inverse camera world rotation and translation.

## Intrinsics

Every calibrated camera stores `fx/fy/cx/cy` in pixels together with image width and height.

Blender receives these values without a model-side re-fit. The renderer converts them deterministically to lens, pixel aspect and principal-point shifts.

The Three.js viewer uses the same values to build an off-axis projection matrix. Canonical +Z-up coordinates are converted to Three.js +Y-up through one explicit basis transform rather than reinterpreting individual Euler angles.

## Source photo and provenance

`source_asset_id` points to an immutable image Asset in the same project. Camera provenance can additionally list source Asset IDs and a note.

Manual calibration may be stored without observations. When 2D/3D correspondences are supplied, STROY recomputes the RMS reprojection residual in pixels and stores it on the immutable Scene revision.

## Golden calibration fixture

`fixtures/golden-camera.correspondences.json` contains an analytic camera/correspondence set. CI verifies the protected points project within **0.01 px** RMS tolerance.

This synthetic tolerance validates the implementation convention, not real-world camera accuracy. Real apartment-photo residual thresholds will be established on the actual dataset.
