import { Canvas, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import { PerspectiveCamera } from "three";
import type { SceneCamera, SceneDocument, SceneEntity } from "./api";
import {
  canonicalDimensionsToThree,
  canonicalPositionToThree,
  canonicalRotationToThreeQuaternion
} from "./sceneMath";

function dimensions(entity: SceneEntity): [number, number, number] {
  const geometry = entity.geometry ?? {};
  const raw =
    (geometry["dimensions_mm"] as [number, number, number] | undefined) ??
    (geometry["size_mm"] as [number, number, number] | undefined);
  if (raw?.length === 3) {
    return canonicalDimensionsToThree(raw);
  }
  return entity.kind === "wall" ? [4, 2.8, 0.12] : [0.8, 0.8, 0.8];
}

function EntityMesh({
  entity,
  selected,
  debugLocks,
  onSelect
}: {
  entity: SceneEntity;
  selected: boolean;
  debugLocks: boolean;
  onSelect: () => void;
}) {
  const size = dimensions(entity);
  const p = entity.transform?.translation_mm ?? [0, 0, 0];
  const r = entity.transform?.rotation_deg ?? [0, 0, 0];
  const geometryLocked = Boolean(entity.locks?.geometry);
  const color =
    (entity.metadata?.["color"] as string | undefined) ??
    (selected
      ? "#d8b37a"
      : debugLocks && geometryLocked
        ? "#d16645"
        : "#b7b9bd");

  return (
    <mesh
      position={canonicalPositionToThree(p)}
      quaternion={canonicalRotationToThreeQuaternion(r)}
      onClick={(event) => {
        event.stopPropagation();
        onSelect();
      }}
    >
      <boxGeometry args={size} />
      <meshStandardMaterial
        color={color}
        wireframe={debugLocks && geometryLocked}
        transparent
        opacity={entity.kind === "wall" ? 0.72 : 1}
      />
    </mesh>
  );
}

function CameraController({ calibrated }: { calibrated: SceneCamera | null }) {
  const { camera } = useThree();

  useEffect(() => {
    if (!(camera instanceof PerspectiveCamera)) return;

    if (!calibrated) {
      camera.position.set(6, 5, 6);
      camera.quaternion.identity();
      camera.fov = 45;
      camera.aspect = 1.6;
      camera.lookAt(0, 0.8, 0);
      camera.updateProjectionMatrix();
      return;
    }

    camera.position.set(...canonicalPositionToThree(calibrated.transform.translation_mm));
    camera.quaternion.copy(
      canonicalRotationToThreeQuaternion(calibrated.transform.rotation_deg)
    );

    const { fx, fy, cx, cy } = calibrated.intrinsics;
    const width = calibrated.width_px;
    const height = calibrated.height_px;
    const near = 0.01;
    const far = 1000;

    camera.near = near;
    camera.far = far;
    camera.aspect = width / height;
    camera.fov = (2 * Math.atan(height / (2 * fy)) * 180) / Math.PI;

    camera.projectionMatrix.set(
      (2 * fx) / width,
      0,
      1 - (2 * cx) / width,
      0,
      0,
      (2 * fy) / height,
      (2 * cy) / height - 1,
      0,
      0,
      0,
      -(far + near) / (far - near),
      (-2 * far * near) / (far - near),
      0,
      0,
      -1,
      0
    );
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
  }, [calibrated, camera]);

  return null;
}

export function SceneViewer({ scene }: { scene: SceneDocument | null }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [cameraId, setCameraId] = useState<string>("overview");
  const [debugLocks, setDebugLocks] = useState(false);
  const entities = useMemo(() => scene?.entities ?? [], [scene]);
  const selected = entities.find((entity) => entity.id === selectedId) ?? null;
  const calibrated =
    cameraId === "overview"
      ? null
      : scene?.cameras.find((camera) => camera.id === cameraId) ?? null;

  useEffect(() => {
    if (cameraId !== "overview" && !scene?.cameras.some((camera) => camera.id === cameraId)) {
      setCameraId("overview");
    }
    if (selectedId && !entities.some((entity) => entity.id === selectedId)) {
      setSelectedId(null);
    }
  }, [cameraId, entities, scene, selectedId]);

  return (
    <div className="viewer-shell">
      <div className="viewer-toolbar">
        <button
          className={cameraId === "overview" ? "secondary active" : "secondary"}
          onClick={() => setCameraId("overview")}
        >
          Overview
        </button>
        {(scene?.cameras ?? []).map((camera) => (
          <button
            key={camera.id}
            className={cameraId === camera.id ? "secondary active" : "secondary"}
            onClick={() => setCameraId(camera.id)}
          >
            {camera.id}
          </button>
        ))}
        <button
          className={debugLocks ? "secondary active" : "secondary"}
          onClick={() => setDebugLocks((value) => !value)}
        >
          Geometry locks
        </button>
      </div>

      <div
        className="viewer"
        style={
          calibrated
            ? { aspectRatio: `${calibrated.width_px} / ${calibrated.height_px}` }
            : undefined
        }
      >
        <Canvas camera={{ position: [6, 5, 6], fov: 45 }}>
          <CameraController calibrated={calibrated} />
          <ambientLight intensity={1.4} />
          <directionalLight position={[4, 8, 4]} intensity={2} />
          <gridHelper args={[12, 24]} />
          {entities.map((entity) => (
            <EntityMesh
              key={entity.id}
              entity={entity}
              selected={selectedId === entity.id}
              debugLocks={debugLocks}
              onSelect={() => setSelectedId(entity.id)}
            />
          ))}
        </Canvas>
        <div className="viewer-caption">
          {selected ? (
            <>
              <strong>{selected.id}</strong>
              <span>
                {selected.kind}
                {selected.locks?.geometry ? " · geometry locked" : ""}
                {selected.locks?.transform ? " · transform locked" : ""}
                {selected.locks?.material ? " · material locked" : ""}
              </span>
            </>
          ) : calibrated ? (
            <>
              <strong>{calibrated.id}</strong>
              <span>
                fx {calibrated.intrinsics.fx.toFixed(1)} · fy{" "}
                {calibrated.intrinsics.fy.toFixed(1)}
                {calibrated.calibration?.residual != null
                  ? ` · residual ${calibrated.calibration.residual.toFixed(2)} px`
                  : ""}
              </span>
            </>
          ) : (
            "click an entity to inspect its semantic ID"
          )}
        </div>
      </div>
    </div>
  );
}
