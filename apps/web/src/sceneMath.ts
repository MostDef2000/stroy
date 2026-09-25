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
