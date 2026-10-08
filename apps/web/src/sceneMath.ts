import { Euler, Matrix4, Quaternion, Vector3 } from "three";

const BASIS = new Matrix4().makeRotationX(-Math.PI / 2);
const BASIS_INVERSE = BASIS.clone().invert();

export function canonicalPositionToThree(
  translationMm: [number, number, number]
): [number, number, number] {
  return [
    translationMm[0] / 1000,
    translationMm[2] / 1000,
    -translationMm[1] / 1000
  ];
}

export function canonicalRotationToThreeQuaternion(
  rotationDeg: [number, number, number]
): Quaternion {
  const euler = new Euler(
    (rotationDeg[0] * Math.PI) / 180,
    (rotationDeg[1] * Math.PI) / 180,
    (rotationDeg[2] * Math.PI) / 180,
    "XYZ"
  );
  const canonical = new Matrix4().makeRotationFromEuler(euler);
  const converted = BASIS.clone().multiply(canonical).multiply(BASIS_INVERSE);
  return new Quaternion().setFromRotationMatrix(converted);
}

export function canonicalDimensionsToThree(
  dimensionsMm: [number, number, number]
): [number, number, number] {
  return [
    dimensionsMm[0] / 1000,
    dimensionsMm[2] / 1000,
    dimensionsMm[1] / 1000
  ];
}

export function canonicalDirectionToThree(
  direction: [number, number, number]
): Vector3 {
  return new Vector3(direction[0], direction[2], -direction[1]);
}

// ---------------------------------------------------------------------------
// R7 (#187): inverse transforms — extract a saved viewpoint from the live
// three.js camera. Round-trip invariants (unit-tested in
// tests/sceneMath.test.mjs):
//   threeToCanonicalPosition(canonicalPositionToThree(x)) === x
//   canonicalRotationToThreeQuaternion(threeToCanonicalRotation(q)) ≈ q
// ---------------------------------------------------------------------------

/** three.js position in metres → canonical scene position in millimetres. */
export function threeToCanonicalPosition(
  positionM: [number, number, number]
): [number, number, number] {
  return [positionM[0] * 1000, -positionM[2] * 1000, positionM[1] * 1000];
}

/**
 * three.js orientation (quaternion or matrix) → canonical rotation_deg.
 * canonical = BASIS_INVERSE · three · BASIS (the inverse of the
 * canonical→three sandwich in canonicalRotationToThreeQuaternion), decoded as
 * an XYZ Euler in degrees.
 */
export function threeToCanonicalRotation(
  rotation: Quaternion | Matrix4
): [number, number, number] {
  const threeMatrix =
    rotation instanceof Matrix4
      ? rotation.clone()
      : new Matrix4().makeRotationFromQuaternion(rotation);
  const canonical = BASIS_INVERSE.clone().multiply(threeMatrix).multiply(BASIS);
  const euler = new Euler().setFromRotationMatrix(canonical, "XYZ");
  return [
    (euler.x * 180) / Math.PI,
    (euler.y * 180) / Math.PI,
    (euler.z * 180) / Math.PI
  ];
}

/** Input for viewpointIntrinsicsFromPerspectiveCamera. */
export type PerspectiveIntrinsicsInput = {
  fovDeg: number;
  widthPx: number;
  heightPx: number;
};

/**
 * Pinhole intrinsics matching three.js PerspectiveCamera projection: f covers
 * the vertical FOV, principal point sits at the frame centre. The inverse
 * (fov = 2·atan(height / (2·fy))) is exactly what CameraController applies.
 */
export function viewpointIntrinsicsFromPerspectiveCamera({
  fovDeg,
  widthPx,
  heightPx
}: PerspectiveIntrinsicsInput): { fx: number; fy: number; cx: number; cy: number } {
  const fovRad = (fovDeg * Math.PI) / 180;
  const f = heightPx / 2 / Math.tan(fovRad / 2);
  return { fx: f, fy: f, cx: widthPx / 2, cy: heightPx / 2 };
}

/** Room bounding box on the canonical plan (millimetres, plan axes). */
export type PlanBoundsMm = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
};

/** Suggested camera pose for one room, in canonical millimetres (+Z up). */
export type RoomViewSuggestion = {
  positionMm: [number, number, number];
  looksAtMm: [number, number, number];
};

/**
 * Camera placement for an auto room viewpoint: stand outside the room centre
 * along the plan diagonal and look back at the room. Mirrors the overview
 * precedent (SceneViewer planOverview + CameraController): span factor 0.95,
 * diagonal offset 0.7·d per axis, elevation 0.8·d, aim height 1.1 m — scaled
 * to the room bbox instead of the whole plan. Purely deterministic so tests
 * can pin the numbers.
 */
export function roomViewSuggestionFromBounds(
  bounds: PlanBoundsMm
): RoomViewSuggestion {
  const centerX = (bounds.minX + bounds.maxX) / 2;
  const centerY = (bounds.minY + bounds.maxY) / 2;
  const span = Math.max(
    bounds.maxX - bounds.minX,
    bounds.maxY - bounds.minY
  );
  const distance = Math.max(2000, span * 0.95);
  const elevation = Math.max(1500, span * 0.8);
  return {
    positionMm: [
      centerX + distance * 0.7,
      centerY + distance * 0.7,
      elevation
    ],
    looksAtMm: [centerX, centerY, 1100]
  };
}
